// free-claude-code dashboard — vanilla JS SPA.
//
// Single-file app split into:
//   - tiny route table + html() helper
//   - api() wrapper around fetch with auth header
//   - per-route render functions (renderChat, renderProjects, ...)
//   - chat SSE streaming
//
// Backed by /v1/* routes from api/dashboard_routes.py + /v1/messages.

(() => {
  "use strict";

  // ============================================================ Config
  const AUTH = "freecc"; // matches default ANTHROPIC_AUTH_TOKEN
  const RANGES = ["24h", "7d", "30d", "all"];
  const DEFAULT_RANGE = "7d";

  // ============================================================ tiny utils
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null) continue;
      if (k === "class" || k === "className") node.className = v;
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (k === "style" && typeof v === "object") {
        Object.assign(node.style, v);
      } else if (k === "html") node.innerHTML = v;
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false) node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function fmtUsd(v) {
    if (v == null) return "$0.00";
    if (v >= 100) return "$" + v.toFixed(2);
    if (v >= 1) return "$" + v.toFixed(3);
    if (v >= 0.001) return "$" + v.toFixed(4);
    if (v > 0) return "$" + v.toFixed(6);
    return "$0.00";
  }
  function fmtNum(v) {
    return (v ?? 0).toLocaleString();
  }
  function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay)
      return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  function fmtBytes(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  // ============================================================ API client
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${AUTH}`,
        ...(opts.headers || {}),
      },
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
    }
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    return res.text();
  }

  // ============================================================ Theme
  const THEME_KEY = "fcc-ui-theme/v2";
  function loadTheme() {
    return localStorage.getItem(THEME_KEY) || "dark";
  }
  function applyTheme(name) {
    document.documentElement.setAttribute("data-theme", name);
    localStorage.setItem(THEME_KEY, name);
    const label = $("#theme-toggle-label");
    if (label) label.textContent = name;
  }
  function toggleTheme() {
    const cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "dark" ? "light" : "dark");
  }

  // ============================================================ Markdown-lite
  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function safeUrl(url) {
    return /^(https?:|data:image\/(png|jpe?g|gif|webp|svg\+xml);base64,)/i.test(url)
      ? url
      : "#";
  }
  function md(text) {
    if (!text) return "";
    const blocks = [];
    let s = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, l, b) => {
      blocks.push({ l, b });
      return ` ${blocks.length - 1} `;
    });
    s = escapeHtml(s);
    s = s.replace(
      /!\[([^\]]*)\]\(([^)]+)\)/g,
      (_m, alt, url) =>
        `<img src="${safeUrl(url)}" alt="${alt}" loading="lazy" />`
    );
    s = s.replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      (_m, t, u) =>
        `<a href="${safeUrl(u)}" target="_blank" rel="noopener noreferrer">${t}</a>`
    );
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[\s>])_([^_\n]+)_/g, "$1<em>$2</em>");
    s = s.replace(/ (\d+) /g, (_m, idx) => {
      const { l, b } = blocks[Number(idx)];
      const cls = l ? ` class="lang-${escapeHtml(l)}"` : "";
      return `<pre><code${cls}>${escapeHtml(b)}</code></pre>`;
    });
    return s;
  }

  // ============================================================ Router
  const ROUTES = {
    chat: { label: "Chat", render: () => renderChat() },
    projects: { label: "Projects", render: () => renderProjects() },
    usage: { label: "Usage", render: () => renderUsage() },
    files: { label: "File edits", render: () => renderFiles() },
    audit: { label: "Audit log", render: () => renderAudit() },
    env: { label: "Env vault", render: () => renderEnv() },
    root: { label: "Root files", render: () => renderRoot() },
    skills: { label: "Skills", render: () => renderSkills() },
    settings: { label: "Settings", render: () => renderSettings() },
  };
  let currentRoute = location.hash.replace("#", "") || "chat";
  if (!ROUTES[currentRoute]) currentRoute = "chat";

  function setRoute(name) {
    if (!ROUTES[name]) return;
    currentRoute = name;
    location.hash = name;
    $$(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.route === name)
    );
    ROUTES[name].render();
  }

  function pageHeader({ title, sub, actions = [] }) {
    return el(
      "header",
      { class: "page-header" },
      el(
        "div",
        {},
        el("h1", { class: "page-title" }, title),
        sub ? el("div", { class: "page-sub" }, sub) : null
      ),
      el("div", { class: "page-actions" }, ...actions)
    );
  }

  // ============================================================ Chat view
  /** @type {{[id:string]: {messages: any[], inflight?: AbortController}}} */
  const chatState = {};
  let activeSessionId = null;

  async function loadSessions() {
    return (await api("/v1/sessions")).sessions || [];
  }
  async function loadProjects() {
    return (await api("/v1/projects")).projects || [];
  }
  async function loadSessionDetail(id) {
    return await api(`/v1/sessions/${encodeURIComponent(id)}`);
  }
  async function createSession({ title, model, project_id }) {
    return await api("/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ title, model, project_id }),
    });
  }
  async function updateSession(id, payload) {
    return await api(`/v1/sessions/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  }
  async function deleteSession(id) {
    return await api(`/v1/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
  }
  async function appendMessage(id, role, content) {
    return await api(`/v1/sessions/${encodeURIComponent(id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ role, content }),
    });
  }

  async function renderChat() {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "Chat",
        sub: "deepseek-routed conversations · streaming",
      })
    );
    const shell = el("div", { class: "chat-shell" });
    const list = el("aside", { class: "chat-list" });
    const main = el("section", { class: "chat-main" });
    shell.append(list, main);
    view.appendChild(shell);

    list.append(
      el(
        "div",
        { class: "chat-list-header" },
        el("h2", {}, "sessions"),
        el(
          "button",
          {
            class: "primary",
            onclick: async () => {
              const s = await createSession({ title: "untitled", model: "" });
              activeSessionId = s.id;
              await renderChat();
            },
          },
          "+ new"
        )
      )
    );
    const items = el("div", { class: "chat-list-items" });
    list.appendChild(items);

    const [sessions, projects] = await Promise.all([
      loadSessions(),
      loadProjects(),
    ]);
    const projectMap = Object.fromEntries(projects.map((p) => [p.id, p]));

    if (!sessions.length) {
      items.appendChild(
        el(
          "div",
          { class: "empty" },
          el("div", { class: "empty-icon" }, "◇"),
          el("div", { class: "empty-title" }, "No sessions yet"),
          el(
            "div",
            { class: "empty-sub" },
            "Hit ‘+ new’ to start one. Sessions are stored locally and visible to ds."
          )
        )
      );
    }
    for (const s of sessions) {
      const isActive = s.id === activeSessionId;
      items.appendChild(
        el(
          "button",
          {
            class: "session-item" + (isActive ? " active" : ""),
            onclick: async () => {
              activeSessionId = s.id;
              await renderChat();
            },
          },
          el("div", { class: "session-title truncate" }, s.title || "untitled"),
          el(
            "div",
            { class: "session-meta" },
            el("span", {}, fmtTime(s.updated_at)),
            s.project_id && projectMap[s.project_id]
              ? el(
                  "span",
                  { class: "project-pill" },
                  el("span", {
                    class: "project-dot",
                    style: { background: projectMap[s.project_id].color },
                  }),
                  projectMap[s.project_id].name
                )
              : null
          )
        )
      );
    }

    if (!activeSessionId && sessions.length) {
      activeSessionId = sessions[0].id;
    }

    if (!activeSessionId) {
      main.appendChild(
        el(
          "div",
          { class: "empty" },
          el("div", { class: "empty-icon" }, "✦"),
          el("div", { class: "empty-title" }, "Pick or start a session"),
          el(
            "div",
            { class: "empty-sub" },
            "ds runs your DeepSeek proxy; this UI is the dashboard for it."
          )
        )
      );
      return;
    }

    const detail = await loadSessionDetail(activeSessionId);
    const upstreamModels = await api("/v1/models/upstream").catch(() => ({
      providers: [],
    }));
    renderChatMain(main, detail, projects, upstreamModels);
  }

  function renderChatMain(host, session, projects, upstreamModels) {
    host.innerHTML = "";
    const topbar = el("div", { class: "chat-topbar" });
    const title = el(
      "div",
      { class: "chat-title", contenteditable: "true", spellcheck: "false" },
      session.title || "untitled"
    );
    title.addEventListener("blur", async () => {
      const t = (title.textContent || "").trim().slice(0, 200) || "untitled";
      await updateSession(session.id, {
        title: t,
        model: session.model || "",
        project_id: session.project_id || null,
      });
    });

    // Project select
    const projectSelect = el(
      "select",
      {
        title: "Project",
        onchange: async () => {
          await updateSession(session.id, {
            title: session.title,
            model: session.model || "",
            project_id: projectSelect.value || null,
          });
        },
      },
      el("option", { value: "" }, "no project"),
      ...projects.map((p) =>
        el(
          "option",
          { value: p.id, selected: p.id === session.project_id },
          p.name
        )
      )
    );

    // Model select — combine upstream-discovered + Claude defaults.
    const modelOptions = [
      "claude-3-5-sonnet-20241022",
      "claude-3-opus-20240229",
      "claude-3-5-haiku-20241022",
    ];
    for (const p of upstreamModels.providers || []) {
      for (const m of p.models || []) {
        modelOptions.push(`${p.provider}/${m}`);
      }
    }
    const modelSelect = el(
      "select",
      {
        title: "Model",
        onchange: async () => {
          await updateSession(session.id, {
            title: session.title,
            model: modelSelect.value,
            project_id: session.project_id || null,
          });
        },
      },
      ...modelOptions.map((m) =>
        el(
          "option",
          { value: m, selected: m === (session.model || "claude-3-5-sonnet-20241022") },
          m
        )
      )
    );

    const deleteBtn = el(
      "button",
      {
        class: "danger ghost",
        title: "delete",
        onclick: async () => {
          if (!confirm("Delete this session?")) return;
          await deleteSession(session.id);
          activeSessionId = null;
          await renderChat();
        },
      },
      "✕"
    );

    topbar.append(title, projectSelect, modelSelect, deleteBtn);
    host.appendChild(topbar);

    const messages = el("div", { class: "messages" });
    host.appendChild(messages);

    const composer = el(
      "form",
      {
        class: "composer",
        autocomplete: "off",
        onsubmit: async (e) => {
          e.preventDefault();
          const text = ta.value.trim();
          if (!text) return;
          ta.value = "";
          ta.style.height = "auto";
          await sendInChat(session, modelSelect.value, text, messages);
        },
      },
      el(
        "div",
        { class: "composer-hint" },
        "↵ send · shift+↵ newline · ",
        el("code", {}, "imagine: a glass cat"),
        " generates an image"
      ),
      el(
        "div",
        { class: "composer-row" },
        (() => {
          const ta = el("textarea", {
            id: "chat-input",
            placeholder: "message ds…",
            rows: 1,
          });
          ta.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
              e.preventDefault();
              composer.requestSubmit();
            }
          });
          ta.addEventListener("input", () => {
            ta.style.height = "auto";
            ta.style.height = Math.min(ta.scrollHeight, 240) + "px";
          });
          return ta;
        })(),
        el("button", { class: "primary", type: "submit" }, "send")
      )
    );
    const ta = $("textarea", composer);
    host.appendChild(composer);

    // Render existing messages
    for (const m of session.messages || []) {
      messages.appendChild(renderChatMessage(m));
    }
    setTimeout(() => {
      messages.scrollTop = messages.scrollHeight;
      ta.focus();
    }, 0);
  }

  function renderChatMessage(msg) {
    const wrap = el(
      "article",
      { class: "message " + (msg.role || "assistant") },
      el(
        "div",
        { class: "message-meta" },
        el(
          "span",
          { class: "message-role" },
          msg.role === "user" ? "you" : "ds"
        ),
        msg.created_at ? el("span", {}, fmtTime(msg.created_at)) : null
      ),
      el("div", { class: "message-body", html: md(msg.content || "") })
    );
    if (msg.streaming) wrap.classList.add("streaming");
    if (msg.error) wrap.classList.add("error");
    return wrap;
  }

  async function sendInChat(session, modelSelected, text, messagesHost) {
    // Persist user msg.
    const userRow = await appendMessage(session.id, "user", text);
    messagesHost.appendChild(renderChatMessage(userRow));

    const assistant = {
      role: "assistant",
      content: "",
      streaming: true,
      created_at: Date.now(),
    };
    const node = renderChatMessage(assistant);
    messagesHost.appendChild(node);
    messagesHost.scrollTop = messagesHost.scrollHeight;

    // Build messages list from server state (already includes the user we just saved).
    const fresh = await loadSessionDetail(session.id);
    const messages = (fresh.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
    }));

    const payload = {
      model: modelSelected || "claude-3-5-sonnet-20241022",
      max_tokens: 4096,
      stream: true,
      messages,
      metadata: {
        user_id: `fcc:project_id=${session.project_id || ""},session_id=${session.id}`,
        // Routed through workspace_resolver so the agent loop sandboxes Bash
        // and file ops to this project's directory when set.
        project_id: session.project_id || null,
        session_id: session.id,
      },
    };

    const ctrl = new AbortController();
    try {
      const response = await fetch("/v1/messages", {
        method: "POST",
        signal: ctrl.signal,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${AUTH}`,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok || !response.body) {
        const text = await response.text().catch(() => "");
        throw new Error(`HTTP ${response.status}: ${text.slice(0, 240)}`);
      }
      const reader = response.body.getReader();
      const dec = new TextDecoder("utf-8");
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const ev = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          handleSseEvent(ev, assistant);
          const body = node.querySelector(".message-body");
          if (body) body.innerHTML = md(assistant.content);
          messagesHost.scrollTop = messagesHost.scrollHeight;
        }
      }
    } catch (err) {
      assistant.error = true;
      assistant.content +=
        (assistant.content ? "\n\n" : "") + `Error: ${err.message || err}`;
    } finally {
      assistant.streaming = false;
      node.classList.remove("streaming");
      if (assistant.error) node.classList.add("error");
      const body = node.querySelector(".message-body");
      if (body) body.innerHTML = md(assistant.content);
      // Persist assistant turn.
      try {
        await appendMessage(session.id, "assistant", assistant.content);
      } catch (e) {
        console.warn("persist failed:", e);
      }
      // Refresh nav-footer cost metrics.
      void refreshFooterMetrics();
    }
  }

  function handleSseEvent(rawEvent, assistant) {
    const lines = rawEvent.split("\n");
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const dataStr = line.slice(5).trim();
      if (!dataStr || dataStr === "[DONE]") continue;
      let parsed;
      try {
        parsed = JSON.parse(dataStr);
      } catch {
        continue;
      }
      if (parsed.type === "content_block_delta") {
        const d = parsed.delta || {};
        if (d.type === "text_delta" && typeof d.text === "string") {
          assistant.content += d.text;
        }
      } else if (parsed.type === "error") {
        assistant.error = true;
        const msg = parsed.error?.message || parsed.message || "stream error";
        assistant.content += (assistant.content ? "\n\n" : "") + `[error] ${msg}`;
      }
    }
  }

  // ============================================================ Projects view
  async function renderProjects() {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "Projects",
        sub: "Group sessions and share context across them.",
        actions: [
          el(
            "button",
            { class: "primary", onclick: () => editProject(null) },
            "+ New project"
          ),
        ],
      })
    );

    const body = el("div", { class: "page-body" });
    view.appendChild(body);

    const projects = await loadProjects();
    if (!projects.length) {
      body.appendChild(
        el(
          "div",
          { class: "empty" },
          el("div", { class: "empty-icon" }, "▣"),
          el("div", { class: "empty-title" }, "No projects yet"),
          el(
            "div",
            { class: "empty-sub" },
            "Projects let you set shared context (system prompt) for every session in them."
          )
        )
      );
      return;
    }

    const grid = el("div", { class: "grid-cards" });
    for (const p of projects) {
      grid.appendChild(
        el(
          "div",
          {
            class: "card",
            style: { borderLeft: `3px solid ${p.color || "#6366f1"}` },
          },
          el(
            "div",
            { class: "card-title" },
            el("span", {}, p.name),
            el(
              "button",
              {
                class: "ghost",
                title: "edit",
                onclick: () => editProject(p),
              },
              "✎"
            )
          ),
          el("div", { class: "card-sub truncate" }, p.description || "—"),
          el(
            "div",
            { class: "muted", style: { fontSize: "12px" } },
            (p.shared_context || "").slice(0, 160) ||
              el("span", { class: "faint" }, "no shared context")
          ),
          el(
            "div",
            { class: "row", style: { marginTop: "12px" } },
            el(
              "button",
              {
                onclick: () => {
                  activeSessionId = null;
                  setRoute("chat");
                },
              },
              "Open in chat"
            ),
            el(
              "button",
              {
                class: "danger ghost right",
                onclick: async () => {
                  if (!confirm(`Delete project ${p.name}?`)) return;
                  await api(`/v1/projects/${encodeURIComponent(p.id)}`, {
                    method: "DELETE",
                  });
                  renderProjects();
                },
              },
              "Delete"
            )
          )
        )
      );
    }
    body.appendChild(grid);
  }

  async function editProject(existing) {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: existing ? `Edit: ${existing.name}` : "New project",
        actions: [
          el("button", { onclick: () => setRoute("projects") }, "← Back"),
        ],
      })
    );
    const body = el("div", { class: "page-body" });
    const card = el("div", { class: "card", style: { maxWidth: "720px" } });

    const nameIn = el("input", {
      value: existing?.name || "",
      placeholder: "Project name",
    });
    const descIn = el("input", {
      value: existing?.description || "",
      placeholder: "Short description",
    });
    const colorIn = el("input", {
      value: existing?.color || "#6366f1",
      type: "color",
      style: { width: "60px", padding: "0", height: "32px" },
    });
    const ctxIn = el("textarea", {
      placeholder:
        "Shared context — included as system prompt prefix for every session in this project.",
      rows: 10,
    });
    ctxIn.value = existing?.shared_context || "";
    const wsIn = el("input", {
      value: existing?.workspace_path || "",
      placeholder: "/Users/you/projects/nureine",
    });

    card.append(
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "Name"),
        nameIn
      ),
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "Description"),
        descIn
      ),
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "Color"),
        colorIn
      ),
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "Workspace path"),
        wsIn,
        el(
          "div",
          { class: "field-help" },
          "Sandboxed root for the agent loop when sessions in this project run. Overrides AGENT_DEFAULT_WORKSPACE."
        )
      ),
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "Shared context"),
        ctxIn,
        el(
          "div",
          { class: "field-help" },
          "Visible to ds for every session attached to this project."
        )
      ),
      el(
        "div",
        { class: "row" },
        el(
          "button",
          {
            class: "primary",
            onclick: async () => {
              const payload = {
                name: nameIn.value.trim(),
                description: descIn.value.trim(),
                shared_context: ctxIn.value,
                color: colorIn.value,
                workspace_path: wsIn.value.trim(),
              };
              if (existing) {
                await api(`/v1/projects/${encodeURIComponent(existing.id)}`, {
                  method: "PUT",
                  body: JSON.stringify(payload),
                });
              } else {
                await api("/v1/projects", {
                  method: "POST",
                  body: JSON.stringify(payload),
                });
              }
              setRoute("projects");
            },
          },
          existing ? "Save" : "Create"
        ),
        el(
          "button",
          { class: "ghost", onclick: () => setRoute("projects") },
          "Cancel"
        )
      )
    );

    body.appendChild(card);
    view.appendChild(body);
  }

  // ============================================================ Usage view
  async function renderUsage(range = DEFAULT_RANGE) {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "Usage",
        sub: "tokens · images · cost across all upstreams",
        actions: [
          el(
            "div",
            { class: "range-tabs" },
            ...RANGES.map((r) =>
              el(
                "button",
                {
                  class: "range-tab" + (r === range ? " active" : ""),
                  onclick: () => renderUsage(r),
                },
                r
              )
            )
          ),
        ],
      })
    );

    const body = el("div", { class: "page-body" });
    view.appendChild(body);

    let data;
    try {
      data = await api(`/v1/usage?range=${encodeURIComponent(range)}`);
    } catch (err) {
      body.appendChild(el("div", { class: "empty" }, String(err)));
      return;
    }
    const t = data.summary?.totals || {};
    const stats = el(
      "div",
      { class: "grid-cards" },
      stat("total cost", fmtUsd(t.cost_usd)),
      stat("input tokens", fmtNum(t.input_tokens)),
      stat("output tokens", fmtNum(t.output_tokens)),
      stat("images", fmtNum(t.images)),
      stat("events", fmtNum(t.events))
    );
    body.appendChild(stats);

    body.appendChild(
      providerTable(
        "By provider",
        data.summary?.by_provider || [],
        ["provider", "cost_usd", "input_tokens", "output_tokens", "events"]
      )
    );
    body.appendChild(
      providerTable(
        "By model",
        data.summary?.by_model || [],
        ["model", "provider", "cost_usd", "input_tokens", "output_tokens", "events"]
      )
    );
    body.appendChild(
      providerTable(
        "By kind",
        data.summary?.by_kind || [],
        ["kind", "events", "cost_usd"]
      )
    );

    // Recent events
    const recent = el("div", { class: "card" });
    recent.appendChild(el("div", { class: "card-title" }, "Recent events"));
    if (!data.recent_events?.length) {
      recent.appendChild(
        el("div", { class: "muted" }, "no events in this range")
      );
    } else {
      const headers = [
        "time",
        "kind",
        "provider",
        "model",
        "in",
        "out",
        "imgs",
        "cost",
        "ms",
      ];
      const tbl = el(
        "table",
        { class: "table" },
        el(
          "thead",
          {},
          el("tr", {}, ...headers.map((h) => el("th", {}, h)))
        ),
        el(
          "tbody",
          {},
          ...data.recent_events.map((e) =>
            el(
              "tr",
              {},
              el("td", { class: "mono" }, fmtTime(e.ts)),
              el("td", {}, el("span", { class: "badge" }, e.kind)),
              el("td", { class: "mono" }, e.provider),
              el(
                "td",
                { class: "mono truncate", style: { maxWidth: "260px" } },
                e.model
              ),
              el("td", { class: "num" }, fmtNum(e.input_tokens)),
              el("td", { class: "num" }, fmtNum(e.output_tokens)),
              el("td", { class: "num" }, fmtNum(e.images)),
              el("td", { class: "num tabular" }, fmtUsd(e.cost_usd)),
              el("td", { class: "num" }, fmtNum(e.duration_ms))
            )
          )
        )
      );
      recent.appendChild(tbl);
    }
    body.appendChild(recent);
  }

  function stat(label, value, sub) {
    return el(
      "div",
      { class: "stat" },
      el("div", { class: "stat-label" }, label),
      el("div", { class: "stat-value" }, value),
      sub ? el("div", { class: "stat-sub" }, sub) : null
    );
  }

  function providerTable(title, rows, columns) {
    const card = el("div", { class: "card" });
    card.appendChild(el("div", { class: "card-title" }, title));
    if (!rows.length) {
      card.appendChild(el("div", { class: "muted" }, "no data yet"));
      return card;
    }
    const numeric = new Set([
      "cost_usd",
      "input_tokens",
      "output_tokens",
      "events",
      "images",
    ]);
    const tbl = el(
      "table",
      { class: "table" },
      el(
        "thead",
        {},
        el("tr", {}, ...columns.map((c) => el("th", {}, c.replace(/_/g, " "))))
      ),
      el(
        "tbody",
        {},
        ...rows.map((row) =>
          el(
            "tr",
            {},
            ...columns.map((c) => {
              const v = row[c];
              if (numeric.has(c)) {
                if (c === "cost_usd")
                  return el("td", { class: "num tabular" }, fmtUsd(v));
                return el("td", { class: "num tabular" }, fmtNum(v));
              }
              return el(
                "td",
                { class: "mono" },
                String(v ?? "—")
              );
            })
          )
        )
      )
    );
    card.appendChild(tbl);
    return card;
  }

  // ============================================================ Files view
  async function renderFiles() {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "File edits",
        sub: "edits, writes, and creations recorded by the proxy",
      })
    );
    const body = el("div", { class: "page-body" });
    view.appendChild(body);
    const data = await api("/v1/files?limit=200").catch(() => ({ edits: [] }));
    if (!data.edits?.length) {
      body.appendChild(
        el(
          "div",
          { class: "empty" },
          el("div", { class: "empty-icon" }, "≡"),
          el("div", { class: "empty-title" }, "No file edits recorded"),
          el(
            "div",
            { class: "empty-sub" },
            "Edits made through the dashboard (root files, env vault) are tracked here."
          )
        )
      );
      return;
    }
    const tbl = el(
      "table",
      { class: "table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", {}, "time"),
          el("th", {}, "op"),
          el("th", {}, "path"),
          el("th", {}, "Δ bytes")
        )
      ),
      el(
        "tbody",
        {},
        ...data.edits.map((e) =>
          el(
            "tr",
            {},
            el("td", { class: "mono" }, fmtTime(e.ts)),
            el("td", {}, el("span", { class: "badge" }, e.op)),
            el(
              "td",
              { class: "mono truncate", style: { maxWidth: "640px" } },
              e.path
            ),
            el("td", { class: "num tabular" }, e.bytes_delta)
          )
        )
      )
    );
    body.appendChild(el("div", { class: "card" }, tbl));
  }

  // ============================================================ Audit view
  async function renderAudit() {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "Audit log",
        sub: "env edits, config changes, system events",
      })
    );
    const body = el("div", { class: "page-body" });
    view.appendChild(body);
    const data = await api("/v1/audit?limit=200").catch(() => ({ events: [] }));
    if (!data.events?.length) {
      body.appendChild(
        el(
          "div",
          { class: "empty" },
          el("div", { class: "empty-icon" }, "◐"),
          el("div", { class: "empty-title" }, "No audit events"),
          el(
            "div",
            { class: "empty-sub" },
            "Mutations through the dashboard are logged here."
          )
        )
      );
      return;
    }
    const tbl = el(
      "table",
      { class: "table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", {}, "time"),
          el("th", {}, "category"),
          el("th", {}, "event"),
          el("th", {}, "detail")
        )
      ),
      el(
        "tbody",
        {},
        ...data.events.map((e) =>
          el(
            "tr",
            {},
            el("td", { class: "mono" }, fmtTime(e.ts)),
            el("td", {}, el("span", { class: "badge" }, e.category)),
            el("td", { class: "mono" }, e.event),
            el(
              "td",
              { class: "truncate", style: { maxWidth: "560px" } },
              e.detail || ""
            )
          )
        )
      )
    );
    body.appendChild(el("div", { class: "card" }, tbl));
  }

  // ============================================================ Env vault view
  async function renderEnv() {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "Env vault",
        sub: "your global ~/.config/free-claude-code/.env",
        actions: [
          el(
            "button",
            { onclick: () => promptUpsertEnv() },
            "+ Set key"
          ),
        ],
      })
    );
    const body = el("div", { class: "page-body" });
    view.appendChild(body);
    const data = await api("/v1/env");
    body.appendChild(
      el(
        "div",
        { class: "card" },
        el(
          "div",
          { class: "card-title" },
          el("span", {}, "Keys"),
          el("span", { class: "badge" }, data.path)
        ),
        data.entries?.length
          ? el(
              "table",
              { class: "table" },
              el(
                "thead",
                {},
                el(
                  "tr",
                  {},
                  el("th", {}, "key"),
                  el("th", {}, "value"),
                  el("th", {})
                )
              ),
              el(
                "tbody",
                {},
                ...data.entries.map((e) =>
                  el(
                    "tr",
                    {},
                    el("td", { class: "mono" }, e.key),
                    el(
                      "td",
                      { class: "mono truncate", style: { maxWidth: "520px" } },
                      e.secret ? e.masked : e.value
                    ),
                    el(
                      "td",
                      { class: "row" },
                      el(
                        "button",
                        {
                          class: "ghost",
                          onclick: () => promptUpsertEnv(e.key, e.value, e.secret),
                        },
                        "edit"
                      ),
                      el(
                        "button",
                        {
                          class: "danger ghost",
                          onclick: async () => {
                            if (
                              !confirm(`Remove ${e.key} from the env vault?`)
                            )
                              return;
                            await api(
                              `/v1/env/${encodeURIComponent(e.key)}`,
                              { method: "DELETE" }
                            );
                            renderEnv();
                          },
                        },
                        "remove"
                      )
                    )
                  )
                )
              )
            )
          : el("div", { class: "muted" }, "no entries — env file is empty"),
        el(
          "div",
          { class: "field-help", style: { marginTop: "12px" } },
          "Restart the proxy after edits to apply changes (",
          el("code", {}, "pkill -f free-claude-code && ds"),
          ")."
        )
      )
    );
  }

  async function promptUpsertEnv(existingKey = "", existingValue = "", isSecret = false) {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: existingKey ? `Edit ${existingKey}` : "Set env key",
        actions: [el("button", { onclick: () => renderEnv() }, "← Back")],
      })
    );
    const body = el("div", { class: "page-body" });
    const card = el("div", { class: "card", style: { maxWidth: "720px" } });
    const keyIn = el("input", {
      value: existingKey,
      placeholder: "EXAMPLE_API_KEY",
    });
    if (existingKey) keyIn.disabled = true;
    const valIn = el("textarea", {
      placeholder: "value",
      rows: 4,
    });
    valIn.value = existingValue;
    if (isSecret) valIn.placeholder = "(secret value, stored as-is)";

    card.append(
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "Key"),
        keyIn,
        el(
          "div",
          { class: "field-help" },
          "Uppercase letters, digits, underscores. Will not be quoted."
        )
      ),
      el(
        "div",
        { class: "field" },
        el("label", { class: "field-label" }, "Value"),
        valIn
      ),
      el(
        "div",
        { class: "row" },
        el(
          "button",
          {
            class: "primary",
            onclick: async () => {
              const key = (keyIn.value || "").trim();
              if (!key) return;
              await api("/v1/env", {
                method: "PUT",
                body: JSON.stringify({ key, value: valIn.value }),
              });
              renderEnv();
            },
          },
          "Save"
        ),
        el("button", { class: "ghost", onclick: () => renderEnv() }, "Cancel")
      )
    );
    body.appendChild(card);
    view.appendChild(body);
  }

  // ============================================================ Root files view
  async function renderRoot() {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "Root files",
        sub: "AGENTS.md · CLAUDE.md · PLAN.md · ROADMAP.md · README.md · .env.example",
      })
    );
    const body = el("div", { class: "page-body" });
    view.appendChild(body);
    const data = await api("/v1/root-files");
    body.appendChild(
      el(
        "div",
        { class: "card" },
        el(
          "div",
          { class: "card-title" },
          el("span", {}, "Files"),
          el("span", { class: "badge" }, data.root)
        ),
        data.files?.length
          ? el(
              "table",
              { class: "table" },
              el(
                "thead",
                {},
                el(
                  "tr",
                  {},
                  el("th", {}, "name"),
                  el("th", {}, "size"),
                  el("th", {}, "modified"),
                  el("th", {})
                )
              ),
              el(
                "tbody",
                {},
                ...data.files.map((f) =>
                  el(
                    "tr",
                    {},
                    el("td", { class: "mono" }, f.name),
                    el("td", { class: "num tabular" }, fmtBytes(f.size)),
                    el("td", { class: "mono" }, fmtTime(f.modified_at)),
                    el(
                      "td",
                      {},
                      el(
                        "button",
                        { onclick: () => openRootFileEditor(f.name) },
                        "edit"
                      )
                    )
                  )
                )
              )
            )
          : el("div", { class: "muted" }, "no allowed files found")
      )
    );
  }

  async function openRootFileEditor(name) {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: `Edit · ${name}`,
        actions: [el("button", { onclick: () => setRoute("root") }, "← Back")],
      })
    );
    const body = el("div", { class: "page-body" });
    view.appendChild(body);
    const data = await api(`/v1/root-files/${encodeURIComponent(name)}`);
    const ta = el("textarea", {});
    ta.value = data.content || "";
    body.appendChild(
      el(
        "div",
        { class: "card editor" },
        ta,
        el(
          "div",
          { class: "row" },
          el(
            "button",
            {
              class: "primary",
              onclick: async () => {
                await api(`/v1/root-files/${encodeURIComponent(name)}`, {
                  method: "PUT",
                  body: JSON.stringify({ content: ta.value }),
                });
                setRoute("root");
              },
            },
            "Save"
          ),
          el(
            "button",
            { class: "ghost", onclick: () => setRoute("root") },
            "Cancel"
          )
        )
      )
    );
  }

  // ============================================================ Skills view
  async function renderSkills() {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "Skills",
        sub: "discovered SKILL.md files in your Claude install",
      })
    );
    const body = el("div", { class: "page-body" });
    view.appendChild(body);
    const data = await api("/v1/skills");
    if (!data.skills?.length) {
      body.appendChild(
        el(
          "div",
          { class: "empty" },
          el("div", { class: "empty-icon" }, "★"),
          el("div", { class: "empty-title" }, "No skills found"),
          el(
            "div",
            { class: "empty-sub" },
            "Searched: " + (data.search_paths || []).join(", ")
          )
        )
      );
      return;
    }
    const grid = el("div", { class: "grid-cards" });
    for (const s of data.skills) {
      grid.appendChild(
        el(
          "div",
          { class: "card" },
          el("div", { class: "card-title" }, s.name),
          el("div", { class: "card-sub" }, s.description || "no description"),
          el(
            "div",
            { class: "muted truncate", style: { fontSize: "11px" } },
            s.path
          )
        )
      );
    }
    body.appendChild(grid);
  }

  // ============================================================ Settings view
  async function renderSettings() {
    const view = $("#view");
    view.innerHTML = "";
    view.appendChild(
      pageHeader({
        title: "Settings",
        sub: "non-sensitive snapshot of the running proxy",
        actions: [
          el(
            "button",
            {
              class: "btn btn-primary",
              type: "button",
              onclick: () => openSetupWizard(),
            },
            "✦ Setup wizard"
          ),
        ],
      })
    );
    const body = el("div", { class: "page-body" });
    view.appendChild(body);

    // Capabilities snapshot at the top — Active / Needs attention / Suggested
    try {
      const caps = await api("/v1/capabilities");
      body.appendChild(renderCapabilitiesPanel(caps));
    } catch (e) {
      console.warn("capabilities load failed", e);
    }

    const data = await api("/v1/settings");
    const agent = data.agent_mode || {};
    body.appendChild(
      el(
        "div",
        { class: "grid-cards" },
        editableKvCard("Agent mode", [
          { label: "enabled", value: String(agent.enabled ?? false), envKey: "AGENT_MODE_ENABLED" },
          { label: "workspace", value: agent.workspace || "", envKey: "AGENT_DEFAULT_WORKSPACE" },
          { label: "max_turns", value: agent.max_turns ?? 10, envKey: "AGENT_MAX_TURNS", type: "number" },
          { label: "tool_call_limit_per_min", value: agent.tool_call_limit_per_min ?? 60, envKey: "AGENT_TOOL_CALL_LIMIT_PER_MIN", type: "number" },
          { label: "global_tool_call_limit_per_min", value: agent.global_tool_call_limit_per_min ?? 0, envKey: "AGENT_GLOBAL_TOOL_CALL_LIMIT_PER_MIN", type: "number" },
          { label: "bash_denylist", value: agent.bash_denylist || "", envKey: "AGENT_BASH_DENYLIST" },
          { label: "bash_extra_env", value: agent.bash_extra_env || "", envKey: "AGENT_BASH_EXTRA_ENV" },
        ]),
        editableKvCard("Models", [
          { label: "MODEL", value: data.model, envKey: "MODEL" },
          { label: "MODEL_OPUS", value: data.model_opus || "", envKey: "MODEL_OPUS" },
          { label: "MODEL_SONNET", value: data.model_sonnet || "", envKey: "MODEL_SONNET" },
          { label: "MODEL_HAIKU", value: data.model_haiku || "", envKey: "MODEL_HAIKU" },
          { label: "MODEL_SUBAGENT", value: data.model_subagent || "", envKey: "MODEL_SUBAGENT" },
          { label: "MODEL_FALLBACK_CHAIN", value: data.model_fallback_chain || "", envKey: "MODEL_FALLBACK_CHAIN" },
        ]),
        editableKvCard("Thinking", [
          { label: "default_enabled", value: String(data.thinking?.default_enabled), envKey: "ENABLE_MODEL_THINKING" },
          { label: "budget_max", value: data.thinking?.budget_max ?? "", envKey: "THINKING_BUDGET_MAX", type: "number" },
        ]),
        editableKvCard("Image gen", [
          { label: "provider", value: data.image_gen?.provider || "", envKey: "IMAGE_GEN_PROVIDER" },
          { label: "model", value: data.image_gen?.model || "", envKey: "IMAGE_GEN_MODEL" },
        ]),
        editableKvCard("Vision fallback", [
          { label: "provider", value: data.deepseek_image_fallback?.provider || "", envKey: "DEEPSEEK_IMAGE_FALLBACK_PROVIDER" },
          { label: "model", value: data.deepseek_image_fallback?.model || "", envKey: "DEEPSEEK_IMAGE_FALLBACK_MODEL" },
        ]),
        editableKvCard("API tokens (cloud)", [
          { label: "GITHUB_TOKEN", value: "", envKey: "GITHUB_TOKEN", secret: true },
          { label: "VERCEL_TOKEN", value: "", envKey: "VERCEL_TOKEN", secret: true },
          { label: "SUPABASE_URL", value: "", envKey: "SUPABASE_URL" },
          { label: "SUPABASE_SERVICE_ROLE_KEY", value: "", envKey: "SUPABASE_SERVICE_ROLE_KEY", secret: true },
          { label: "OPENAI_API_KEY", value: "", envKey: "OPENAI_API_KEY", secret: true },
        ]),
        kvCard("Server (restart required)", {
          host: data.host,
          port: data.port,
          auth_set: String(data.anthropic_auth_token_set),
          env_file: data.env_file,
        })
      )
    );

    // Pricing card
    const pricing = await api("/v1/pricing").catch(() => null);
    if (pricing) {
      const tokenRows = Object.entries(pricing.token_prices || {}).map(
        ([k, v]) => ({
          ref: k,
          input_per_mtok: v.input_per_mtok,
          output_per_mtok: v.output_per_mtok,
        })
      );
      const imgRows = Object.entries(pricing.image_prices || {}).map(
        ([k, v]) => ({ ref: k, per_image_usd: v })
      );
      body.appendChild(
        el(
          "div",
          { class: "card" },
          el("div", { class: "card-title" }, "Token pricing"),
          el(
            "table",
            { class: "table" },
            el(
              "thead",
              {},
              el(
                "tr",
                {},
                el("th", {}, "ref"),
                el("th", {}, "input $/M"),
                el("th", {}, "output $/M")
              )
            ),
            el(
              "tbody",
              {},
              ...tokenRows.map((r) =>
                el(
                  "tr",
                  {},
                  el("td", { class: "mono" }, r.ref),
                  el(
                    "td",
                    { class: "num tabular" },
                    fmtUsd(r.input_per_mtok)
                  ),
                  el(
                    "td",
                    { class: "num tabular" },
                    fmtUsd(r.output_per_mtok)
                  )
                )
              )
            )
          )
        )
      );
      body.appendChild(
        el(
          "div",
          { class: "card" },
          el("div", { class: "card-title" }, "Image pricing"),
          el(
            "table",
            { class: "table" },
            el(
              "thead",
              {},
              el(
                "tr",
                {},
                el("th", {}, "ref"),
                el("th", {}, "$/image")
              )
            ),
            el(
              "tbody",
              {},
              ...imgRows.map((r) =>
                el(
                  "tr",
                  {},
                  el("td", { class: "mono" }, r.ref),
                  el("td", { class: "num tabular" }, fmtUsd(r.per_image_usd))
                )
              )
            )
          )
        )
      );
    }
  }

  function kvCard(title, data) {
    return el(
      "div",
      { class: "card" },
      el("div", { class: "card-title" }, title),
      el(
        "table",
        { class: "table" },
        el(
          "tbody",
          {},
          ...Object.entries(data).map(([k, v]) =>
            el(
              "tr",
              {},
              el(
                "td",
                { class: "mono", style: { width: "40%", color: "var(--fg-muted)" } },
                k
              ),
              el("td", { class: "mono truncate" }, String(v))
            )
          )
        )
      )
    );
  }

  // editableKvCard: like kvCard but each row can be edited via inline input.
  // rows: [{ label, value, envKey, secret?, type? }] — envKey present ⇒ editable.
  function editableKvCard(title, rows) {
    const tbody = el("tbody", {});
    rows.forEach((row) => {
      const valueCell = el("td", { class: "mono" });
      const renderDisplay = () => {
        valueCell.innerHTML = "";
        const displayVal =
          row.secret && row.value
            ? "•".repeat(8)
            : row.value === "" || row.value == null
              ? "—"
              : String(row.value);
        const span = el(
          "span",
          { class: "truncate", style: { flex: "1" } },
          displayVal
        );
        valueCell.appendChild(
          el(
            "div",
            { class: "row gap-2 align-center" },
            span,
            row.envKey
              ? el(
                  "button",
                  {
                    class: "btn btn-ghost btn-sm",
                    type: "button",
                    title: "Edit",
                    onclick: () => beginEdit(),
                  },
                  "✎"
                )
              : null
          )
        );
      };
      const beginEdit = () => {
        valueCell.innerHTML = "";
        const input = el("input", {
          class: "form-input",
          type: row.secret ? "password" : row.type === "number" ? "number" : "text",
          value: row.value == null ? "" : String(row.value),
          style: { flex: "1", padding: "4px 8px", fontSize: "13px" },
        });
        const save = el(
          "button",
          {
            class: "btn btn-primary btn-sm",
            type: "button",
            onclick: async () => {
              const newVal = input.value;
              save.disabled = true;
              try {
                await api("/v1/env", {
                  method: "PUT",
                  body: JSON.stringify({ key: row.envKey, value: newVal }),
                });
                row.value = newVal;
                toastShow(`Saved ${row.envKey}`, "ok");
                renderDisplay();
              } catch (e) {
                toastShow(`Save failed: ${e.message}`, "error");
                save.disabled = false;
              }
            },
          },
          "Save"
        );
        const cancel = el(
          "button",
          {
            class: "btn btn-ghost btn-sm",
            type: "button",
            onclick: () => renderDisplay(),
          },
          "Cancel"
        );
        valueCell.appendChild(
          el("div", { class: "row gap-2 align-center" }, input, save, cancel)
        );
        input.focus();
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") save.click();
          if (e.key === "Escape") renderDisplay();
        });
      };
      renderDisplay();
      tbody.appendChild(
        el(
          "tr",
          { "data-env-key": row.envKey || "" },
          el(
            "td",
            { class: "mono", style: { width: "40%", color: "var(--fg-muted)" } },
            row.label
          ),
          valueCell
        )
      );
    });
    return el(
      "div",
      { class: "card" },
      el("div", { class: "card-title" }, title),
      el("table", { class: "table" }, tbody)
    );
  }

  function toastShow(message, level = "ok") {
    let stack = document.querySelector("#fcc-toasts");
    if (!stack) {
      stack = el("div", { id: "fcc-toasts", class: "toast-stack" });
      document.body.appendChild(stack);
    }
    const t = el("div", { class: `toast toast-${level}` }, message);
    stack.appendChild(t);
    setTimeout(() => t.remove(), 3200);
  }

  // ============================================================ Capabilities panel
  function renderCapabilitiesPanel(caps) {
    const groups = [
      { id: "active", label: "Active", pill: "ok" },
      { id: "inactive", label: "Needs attention", pill: "warn" },
      { id: "suggested", label: "Suggested", pill: "accent" },
    ];
    const grid = el("div", { class: "grid-cards" });
    let total = 0;
    groups.forEach((group) => {
      const items = caps[group.id] || [];
      items.forEach((item) => {
        total += 1;
        const card = el(
          "div",
          { class: `card cap-card cap-${group.id}` },
          el(
            "div",
            { class: "row gap-2 align-center" },
            el("span", { class: `pill pill-${group.pill}` }, group.label),
            el("strong", { class: "fs-13" }, item.title)
          ),
          el("p", { class: "muted fs-12" }, item.summary || "")
        );
        if (item.cta_field) {
          card.appendChild(
            el(
              "button",
              {
                class: "btn btn-ghost btn-sm",
                type: "button",
                onclick: () => focusEnvKey(item.cta_field),
              },
              item.cta_label || "Open"
            )
          );
        }
        grid.appendChild(card);
      });
    });
    if (total === 0) {
      grid.appendChild(
        el(
          "div",
          { class: "card muted" },
          "No capabilities reported. Run the setup wizard."
        )
      );
    }
    return el(
      "section",
      { class: "card", style: "padding:16px; gap:8px;" },
      el(
        "div",
        { class: "row space-between align-center" },
        el("strong", {}, "Capabilities"),
        el("span", { class: "muted fs-12" }, `${total} item${total === 1 ? "" : "s"}`)
      ),
      grid
    );
  }

  function focusEnvKey(envKey) {
    setRoute("env");
    renderEnv().then(() => {
      const safeKey = (envKey || "").replace(/[^A-Z0-9_]/gi, "");
      const row = document.querySelector(`[data-env-key="${safeKey}"]`);
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "center" });
        row.classList.add("env-row-highlight");
        setTimeout(() => row.classList.remove("env-row-highlight"), 1600);
      }
    });
  }

  // ============================================================ Setup wizard (inline modal)
  const WIZARD_PROVIDERS = [
    { id: "deepseek", label: "DeepSeek", field: "DEEPSEEK_API_KEY", model: "deepseek/deepseek-v4-flash", blurb: "Cheapest broad-capability default." },
    { id: "open_router", label: "OpenRouter", field: "OPENROUTER_API_KEY", model: "open_router/anthropic/claude-3.5-sonnet", blurb: "Single key, every model." },
    { id: "nvidia_nim", label: "NVIDIA NIM", field: "NVIDIA_NIM_API_KEY", model: "nvidia_nim/z-ai/glm4.7", blurb: "Free tier + GLM models." },
    { id: "zai", label: "Z.ai Coding Plan", field: "ZAI_API_KEY", model: "zai/glm-4.6", blurb: "Subscription with high quotas." },
  ];

  function openSetupWizard() {
    const wizardState = {
      step: 0,
      provider: null,
      apiKey: "",
      agentEnabled: true,
      workspace: "",
      bashDenylist: "rm,sudo",
    };

    const overlay = el("div", { class: "modal-overlay" });
    const card = el("div", { class: "modal-card wizard-card" });
    overlay.appendChild(card);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) document.body.removeChild(overlay);
    });
    document.body.appendChild(overlay);

    const steps = ["Welcome", "Provider", "Agent", "Done"];

    function renderStep() {
      card.innerHTML = "";
      // stepper
      const stepper = el("div", { class: "wizard-stepper" });
      steps.forEach((s, i) => {
        stepper.appendChild(
          el(
            "div",
            { class: `step ${i === wizardState.step ? "current" : ""} ${i < wizardState.step ? "done" : ""}` },
            s
          )
        );
      });
      card.appendChild(stepper);

      const body = el("div", { class: "wizard-body" });
      card.appendChild(body);

      if (wizardState.step === 0) {
        body.appendChild(el("h2", {}, "Welcome"));
        body.appendChild(
          el(
            "p",
            { class: "muted" },
            "Connect a provider, enable agent mode, and start sending requests. 3 steps."
          )
        );
      } else if (wizardState.step === 1) {
        body.appendChild(el("h2", {}, "Pick a provider"));
        WIZARD_PROVIDERS.forEach((p) => {
          const active = wizardState.provider === p.id;
          const row = el(
            "label",
            { class: `wizard-option ${active ? "active" : ""}` },
            el("input", {
              type: "radio",
              name: "wzp",
              value: p.id,
              checked: active ? "checked" : null,
              onchange: () => {
                wizardState.provider = p.id;
                renderStep();
              },
            }),
            el(
              "div",
              {},
              el("strong", {}, p.label),
              el("div", { class: "muted fs-12" }, p.blurb)
            )
          );
          body.appendChild(row);
        });
        body.appendChild(el("label", { class: "form-label" }, "API key"));
        body.appendChild(
          el("input", {
            class: "form-input",
            type: "password",
            placeholder: "sk-...",
            value: wizardState.apiKey,
            oninput: (e) => (wizardState.apiKey = e.target.value),
          })
        );
      } else if (wizardState.step === 2) {
        body.appendChild(el("h2", {}, "Agent mode"));
        body.appendChild(
          el(
            "p",
            { class: "muted" },
            "Server-side tools (Read, Write, Edit, Bash, …) sandboxed to a workspace."
          )
        );
        body.appendChild(
          el(
            "label",
            { class: "wizard-option" },
            el("input", {
              type: "checkbox",
              checked: wizardState.agentEnabled ? "checked" : null,
              onchange: (e) => (wizardState.agentEnabled = e.target.checked),
            }),
            el("div", {}, el("strong", {}, "Enable agent mode"))
          )
        );
        body.appendChild(el("label", { class: "form-label" }, "Workspace path"));
        body.appendChild(
          el("input", {
            class: "form-input",
            type: "text",
            placeholder: "/Users/you/projects",
            value: wizardState.workspace,
            oninput: (e) => (wizardState.workspace = e.target.value),
          })
        );
        body.appendChild(el("label", { class: "form-label" }, "Bash denylist"));
        body.appendChild(
          el("input", {
            class: "form-input",
            type: "text",
            placeholder: "rm,sudo,curl|sh",
            value: wizardState.bashDenylist,
            oninput: (e) => (wizardState.bashDenylist = e.target.value),
          })
        );
      } else if (wizardState.step === 3) {
        body.appendChild(el("h2", {}, "All set"));
        body.appendChild(
          el(
            "p",
            { class: "muted" },
            "Configuration applied. The proxy hot-reloads. Open Chat and try it."
          )
        );
      }

      const actions = el("div", { class: "wizard-actions" });
      const backBtn = el(
        "button",
        {
          type: "button",
          class: "btn btn-ghost",
          disabled: wizardState.step === 0 ? "disabled" : null,
          onclick: () => {
            if (wizardState.step > 0) {
              wizardState.step -= 1;
              renderStep();
            }
          },
        },
        "Back"
      );
      const nextBtn = el(
        "button",
        {
          type: "button",
          class: "btn btn-primary",
          onclick: async () => {
            if (wizardState.step === 1) {
              if (!wizardState.provider || wizardState.apiKey.length < 4) {
                alert("Pick a provider and enter an API key.");
                return;
              }
            }
            if (wizardState.step === 2 && wizardState.agentEnabled && !wizardState.workspace) {
              alert("Set a workspace path (or disable agent mode).");
              return;
            }
            if (wizardState.step === 2) {
              await applyWizard(wizardState);
            }
            if (wizardState.step === steps.length - 1) {
              document.body.removeChild(overlay);
              setRoute("settings");
              renderSettings();
              return;
            }
            wizardState.step += 1;
            renderStep();
          },
        },
        wizardState.step === steps.length - 1
          ? "Done"
          : wizardState.step === 2
            ? "Apply →"
            : "Next →"
      );
      actions.appendChild(backBtn);
      actions.appendChild(el("div", { style: "flex:1" }));
      actions.appendChild(nextBtn);
      card.appendChild(actions);
    }

    async function applyWizard(s) {
      const provider = WIZARD_PROVIDERS.find((p) => p.id === s.provider);
      const updates = [];
      if (provider) {
        updates.push({ key: provider.field, value: s.apiKey, is_secret: true });
        updates.push({ key: "MODEL", value: provider.model, is_secret: false });
      }
      updates.push({
        key: "AGENT_MODE_ENABLED",
        value: String(s.agentEnabled),
        is_secret: false,
      });
      if (s.workspace) {
        updates.push({
          key: "AGENT_DEFAULT_WORKSPACE",
          value: s.workspace,
          is_secret: false,
        });
      }
      if (s.bashDenylist) {
        updates.push({
          key: "AGENT_BASH_DENYLIST",
          value: s.bashDenylist,
          is_secret: false,
        });
      }
      for (const u of updates) {
        try {
          await api("/v1/env", {
            method: "PUT",
            body: JSON.stringify(u),
          });
        } catch (e) {
          console.warn("env write failed", u.key, e);
        }
      }
    }

    renderStep();
  }

  // ============================================================ Footer metrics
  async function refreshFooterMetrics() {
    try {
      const today = await api("/v1/usage?range=24h");
      const week = await api("/v1/usage?range=7d");
      $("#metric-today").textContent = fmtUsd(today.summary?.totals?.cost_usd);
      $("#metric-week").textContent = fmtUsd(week.summary?.totals?.cost_usd);
    } catch {
      /* ignore */
    }
  }
  async function probeStatus() {
    const tag = $("#brand-status");
    if (!tag) return;
    try {
      const r = await fetch("/health");
      tag.textContent = r.ok ? "● online" : "● degraded";
      tag.style.color = r.ok ? "var(--success)" : "var(--warning)";
    } catch {
      tag.textContent = "○ offline";
      tag.style.color = "var(--error)";
    }
  }

  // ============================================================ Boot
  function wireNav() {
    $$(".nav-item").forEach((b) => {
      b.addEventListener("click", () => setRoute(b.dataset.route));
    });
    $("#theme-toggle").addEventListener("click", toggleTheme);
    window.addEventListener("hashchange", () => {
      const r = location.hash.replace("#", "") || "chat";
      if (ROUTES[r]) setRoute(r);
    });
  }
  function boot() {
    applyTheme(loadTheme());
    wireNav();
    setRoute(currentRoute);
    refreshFooterMetrics();
    probeStatus();
    setInterval(refreshFooterMetrics, 30_000);
    setInterval(probeStatus, 30_000);
  }
  boot();
})();
