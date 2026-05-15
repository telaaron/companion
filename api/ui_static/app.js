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
  function renderToolBlocks(text) {
    const lines = text.split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const m = line.match(/^([●✗⏺])\s+(\w+)\(([^)]*)\)\s*$/);
      if (m) {
        const icon = m[1];
        const name = m[2];
        const args = m[3];
        const body = [];
        let inBody = false;
        i += 1;
        while (i < lines.length) {
          const next = lines[i];
          // First body line starts with the └ / ⎿ marker.
          if (!inBody && /^\s*[└⎿]/.test(next)) {
            body.push(next.replace(/^\s*[└⎿]\s?/, ""));
            inBody = true;
            i += 1;
            continue;
          }
          if (inBody) {
            // Stop on a new tool header line or a blank line.
            if (/^[●✗⏺]\s+\w+\(/.test(next)) break;
            if (next.trim() === "") {
              i += 1;
              break;
            }
            body.push(next);
            i += 1;
            continue;
          }
          break;
        }
        const payload = encodeURIComponent(
          JSON.stringify({ icon, name, args, body: body.join("\n") })
        );
        out.push(`§§TOOL§§${payload}§§/TOOL§§`);
        continue;
      }
      out.push(line);
      i += 1;
    }
    return out.join("\n");
  }

  function md(text) {
    if (!text) return "";
    text = renderToolBlocks(text);
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
    s = s.replace(/§§TOOL§§(.+?)§§\/TOOL§§/gs, (_m, payload) => {
      try {
        const o = JSON.parse(decodeURIComponent(payload));
        const header =
          `<span class="tool-glyph">${escapeHtml(o.icon || "●")}</span>` +
          `<span class="tool-name">${escapeHtml(o.name || "")}</span>` +
          `<span class="tool-args">(${escapeHtml(o.args || "")})</span>`;
        const body = o.body
          ? `<details class="tool-body"><summary>show output</summary><pre>${escapeHtml(o.body)}</pre></details>`
          : "";
        return `<div class="tool-block">${header}${body}</div>`;
      } catch {
        return "";
      }
    });
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

  // Folder picker — modal directory browser backed by /v1/fs/browse.
  function openFolderPicker(initialPath, onChoose) {
    const overlay = el("div", { class: "modal-overlay" });
    const card = el("div", { class: "modal-card", style: "max-width:640px;" });
    overlay.appendChild(card);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) document.body.removeChild(overlay);
    });
    document.body.appendChild(overlay);

    let currentPath = initialPath || "";
    const breadcrumb = el("div", { class: "muted fs-12", style: "margin-bottom:8px;" });
    const list = el("div", { class: "folder-list" });
    const pathInput = el("input", { class: "form-input", value: currentPath });
    const close = () => document.body.removeChild(overlay);

    async function load(path) {
      currentPath = path;
      pathInput.value = path;
      breadcrumb.textContent = `Browsing: ${path || "(home)"}`;
      list.innerHTML = "Loading…";
      try {
        const data = await api(`/v1/fs/browse?path=${encodeURIComponent(path)}`);
        currentPath = data.path;
        pathInput.value = data.path;
        breadcrumb.textContent = `Browsing: ${data.path}`;
        list.innerHTML = "";
        if (data.parent) {
          list.appendChild(
            el(
              "div",
              {
                class: "folder-item",
                onclick: () => load(data.parent),
              },
              el("span", { class: "folder-icon" }, "↰"),
              el("span", {}, "..")
            )
          );
        }
        (data.children || []).forEach((c) => {
          list.appendChild(
            el(
              "div",
              {
                class: "folder-item",
                onclick: () => load(c.path),
              },
              el("span", { class: "folder-icon" }, "▸"),
              el("span", {}, c.name)
            )
          );
        });
        if ((data.children || []).length === 0) {
          list.appendChild(
            el("div", { class: "muted fs-12", style: "padding:8px;" }, "(no sub-folders)")
          );
        }
      } catch (e) {
        list.innerHTML = "";
        list.appendChild(
          el("div", { class: "muted fs-12", style: "color:var(--error);padding:8px;" }, `Error: ${e.message}`)
        );
      }
    }

    card.appendChild(el("h2", {}, "Pick a folder"));
    // Quick-path chips populated async from /v1/fs/quick-paths.
    const quickBar = el("div", { class: "quick-paths" });
    card.appendChild(quickBar);
    api("/v1/fs/quick-paths")
      .then((qp) => {
        quickBar.innerHTML = "";
        (qp.paths || []).forEach((p) => {
          quickBar.appendChild(
            el(
              "button",
              {
                class: "btn btn-ghost btn-sm quick-path",
                type: "button",
                title: p.path,
                onclick: () => load(p.path),
              },
              p.label
            )
          );
        });
      })
      .catch(() => {
        quickBar.innerHTML = "";
      });
    card.appendChild(breadcrumb);
    card.appendChild(
      el(
        "div",
        { class: "row gap-2 align-center", style: "margin-bottom:8px;" },
        pathInput,
        el(
          "button",
          {
            class: "btn btn-ghost btn-sm",
            type: "button",
            onclick: () => load(pathInput.value || ""),
          },
          "Go"
        )
      )
    );
    pathInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        load(pathInput.value || "");
      }
    });
    card.appendChild(list);
    card.appendChild(
      el(
        "div",
        { class: "row gap-2 align-center", style: "margin-top:12px; justify-content:flex-end;" },
        el(
          "button",
          { class: "btn btn-ghost", type: "button", onclick: close },
          "Cancel"
        ),
        el(
          "button",
          {
            class: "btn btn-primary",
            type: "button",
            onclick: () => {
              onChoose(currentPath);
              close();
            },
          },
          "Use this folder"
        )
      )
    );

    load(currentPath);
  }

  // Async session rename — calls a one-shot, non-streaming completion to ask
  // the model for a 4-word title summary. Falls back silently on any error.
  // Skips work if the current title was already user-edited (not derived).
  async function maybeRenameSessionAsync(session, assistantReply) {
    if (!session || !session.id) return;
    const current = (session.title || "").trim();
    const wasDerived =
      !current ||
      current === "untitled" ||
      current.length > 50 ||
      current.endsWith("…");
    if (!wasDerived) return;
    try {
      const fresh = await loadSessionDetail(session.id);
      const msgs = fresh.messages || [];
      const firstUser = msgs.find((m) => m.role === "user");
      if (!firstUser) return;
      const prompt =
        "Give a 3-6 word title for this conversation. ONLY the title, no quotes, no punctuation.\n\n" +
        `USER: ${firstUser.content.slice(0, 600)}\n\n` +
        `ASSISTANT: ${(assistantReply || "").slice(0, 600)}`;
      const headers = { "Content-Type": "application/json" };
      if (AUTH) headers.Authorization = `Bearer ${AUTH}`;
      const response = await fetch("/v1/messages", {
        method: "POST",
        headers,
        body: JSON.stringify({
          model: session.model || "deepseek/deepseek-v4-flash",
          max_tokens: 40,
          stream: false,
          messages: [{ role: "user", content: prompt }],
          metadata: { fcc_internal: "title_rename" },
        }),
      });
      if (!response.ok) return;
      const body = await response.text();
      // Server may stream even with stream:false depending on provider; pull
      // first text chunk we can find.
      const m = body.match(/"text":\s*"([^"]+)"/);
      let title = m ? m[1] : "";
      title = title.replace(/[\n"'`]/g, "").trim();
      if (title && title.length >= 3 && title.length <= 80) {
        session.title = title;
        await updateSession(session.id, {
          title,
          model: session.model || "",
          project_id: session.project_id || null,
        });
        const titleEl = document.querySelector(".chat-title");
        if (titleEl) titleEl.textContent = title;
        void loadSessions();
      }
    } catch {
      /* silent — derived title from first message is fine fallback */
    }
  }

  function deriveSessionTitle(text) {
    const cleaned = (text || "")
      .trim()
      .replace(/\s+/g, " ")
      .replace(/^[#>\-*\s]+/, "");
    if (!cleaned) return "untitled";
    // Cut at first sentence boundary or word boundary near 60 chars.
    const sentenceCut = cleaned.split(/[.!?\n]/, 1)[0].trim();
    const candidate = sentenceCut || cleaned;
    if (candidate.length <= 60) return candidate;
    const words = candidate.slice(0, 60).split(" ");
    if (words.length > 1) words.pop(); // drop the half-word
    return words.join(" ") + "…";
  }

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
        sub: "Multi-provider conversations · streaming",
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

    // Search + project filter
    const filterState = {
      query: localStorage.getItem("fcc:chat:query") || "",
      projectId: localStorage.getItem("fcc:chat:project_filter") || "",
      collapsed: JSON.parse(localStorage.getItem("fcc:chat:collapsed") || "{}"),
    };

    const searchInput = el("input", {
      class: "chat-search",
      type: "search",
      placeholder: "Search sessions…",
      value: filterState.query,
    });
    const projectFilter = el("select", { class: "chat-project-filter" });
    list.appendChild(
      el(
        "div",
        { class: "chat-list-filters" },
        searchInput,
        projectFilter
      )
    );

    const items = el("div", { class: "chat-list-items" });
    list.appendChild(items);

    const [sessions, projects] = await Promise.all([
      loadSessions(),
      loadProjects(),
    ]);
    const projectMap = Object.fromEntries(projects.map((p) => [p.id, p]));

    // Populate project filter dropdown
    projectFilter.innerHTML = "";
    projectFilter.appendChild(el("option", { value: "" }, "all projects"));
    projectFilter.appendChild(el("option", { value: "__none__" }, "no project"));
    projects.forEach((p) => {
      const opt = el("option", { value: p.id }, p.name);
      if (filterState.projectId === p.id) opt.selected = true;
      projectFilter.appendChild(opt);
    });
    if (filterState.projectId === "__none__") {
      projectFilter.value = "__none__";
    }

    const renderItems = () => {
      items.innerHTML = "";
      const q = filterState.query.trim().toLowerCase();
      const visible = sessions.filter((s) => {
        if (filterState.projectId === "__none__") {
          if (s.project_id) return false;
        } else if (filterState.projectId && s.project_id !== filterState.projectId) {
          return false;
        }
        if (!q) return true;
        const title = (s.title || "").toLowerCase();
        return title.includes(q);
      });

      if (!visible.length) {
        items.appendChild(
          el(
            "div",
            { class: "empty" },
            el("div", { class: "empty-icon" }, "◇"),
            el("div", { class: "empty-title" }, sessions.length ? "No matches" : "No sessions yet"),
            el(
              "div",
              { class: "empty-sub" },
              sessions.length
                ? "Clear the filter or pick a different project."
                : "Hit ‘+ new’ to start one."
            )
          )
        );
        return;
      }

      // Group by project (or "(no project)") — collapsible folders
      const groups = new Map();
      const NONE_KEY = "__none__";
      visible.forEach((s) => {
        const key = s.project_id || NONE_KEY;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(s);
      });
      // Sort groups: project name asc, no-project last
      const orderedKeys = [...groups.keys()].sort((a, b) => {
        if (a === NONE_KEY) return 1;
        if (b === NONE_KEY) return -1;
        return (projectMap[a]?.name || "").localeCompare(projectMap[b]?.name || "");
      });

      orderedKeys.forEach((key) => {
        const group = groups.get(key);
        const proj = key === NONE_KEY ? null : projectMap[key];
        const label = proj ? proj.name : "(no project)";
        const isCollapsed = !!filterState.collapsed[key];
        const groupEl = el("div", { class: "session-group" });
        const header = el(
          "button",
          {
            class: "session-group-header" + (isCollapsed ? " collapsed" : ""),
            type: "button",
            onclick: () => {
              filterState.collapsed[key] = !filterState.collapsed[key];
              localStorage.setItem(
                "fcc:chat:collapsed",
                JSON.stringify(filterState.collapsed)
              );
              renderItems();
            },
          },
          el(
            "span",
            { class: "session-group-toggle" },
            isCollapsed ? "▸" : "▾"
          ),
          proj
            ? el("span", {
                class: "project-dot",
                style: { background: proj.color },
              })
            : null,
          el("span", { class: "session-group-label" }, label),
          el("span", { class: "session-group-count" }, `${group.length}`)
        );
        groupEl.appendChild(header);
        if (!isCollapsed) {
          const groupItems = el("div", { class: "session-group-items" });
          group.forEach((s) => {
            const isActive = s.id === activeSessionId;
            groupItems.appendChild(
              el(
                "button",
                {
                  class: "session-item" + (isActive ? " active" : ""),
                  onclick: async () => {
                    activeSessionId = s.id;
                    await renderChat();
                  },
                },
                el(
                  "div",
                  { class: "session-title truncate" },
                  s.title || "untitled"
                ),
                el(
                  "div",
                  { class: "session-meta" },
                  el("span", {}, fmtTime(s.updated_at))
                )
              )
            );
          });
          groupEl.appendChild(groupItems);
        }
        items.appendChild(groupEl);
      });
    };

    searchInput.addEventListener("input", () => {
      filterState.query = searchInput.value;
      localStorage.setItem("fcc:chat:query", filterState.query);
      renderItems();
    });
    projectFilter.addEventListener("change", () => {
      filterState.projectId = projectFilter.value;
      localStorage.setItem("fcc:chat:project_filter", filterState.projectId);
      renderItems();
    });

    renderItems();

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
            "Companion is your multi-provider AI; this UI is the dashboard for it."
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
      {
        class: "chat-title",
        contenteditable: "true",
        spellcheck: "false",
        title: "Click to rename — press Enter or click away to save",
        "data-placeholder": "untitled",
      },
      session.title || "untitled"
    );
    const persistTitle = async () => {
      const t = (title.textContent || "").trim().slice(0, 200) || "untitled";
      session.title = t;
      await updateSession(session.id, {
        title: t,
        model: session.model || "",
        project_id: session.project_id || null,
      });
      void loadSessions();
    };
    title.addEventListener("blur", persistTitle);
    title.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        title.blur();
      }
      if (e.key === "Escape") {
        title.textContent = session.title || "untitled";
        title.blur();
      }
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
            placeholder: "Ask anything…",
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

    // Resume in-flight job if the tab was closed mid-stream.
    const activeJobId = getActiveJob(session.id);
    if (activeJobId) {
      api(`/v1/jobs/${encodeURIComponent(activeJobId)}`)
        .then((job) => {
          if (job && ["pending", "running"].includes(job.status)) {
            streamJobIntoChat(session, activeJobId, messages);
          } else {
            setActiveJob(session.id, null);
          }
        })
        .catch(() => setActiveJob(session.id, null));
    }
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
          msg.role === "user" ? "you" : "Companion"
        ),
        msg.created_at ? el("span", {}, fmtTime(msg.created_at)) : null
      ),
      el("div", { class: "message-body", html: md(msg.content || "") })
    );
    if (msg.streaming) wrap.classList.add("streaming");
    if (msg.error) wrap.classList.add("error");
    return wrap;
  }

  // localStorage helpers: track the in-flight job_id per session so the chat
  // can resume if the tab closed mid-stream.
  function activeJobKey(sessionId) {
    return `fcc:active_job:${sessionId}`;
  }
  function setActiveJob(sessionId, jobId) {
    if (jobId) localStorage.setItem(activeJobKey(sessionId), jobId);
    else localStorage.removeItem(activeJobKey(sessionId));
  }
  function getActiveJob(sessionId) {
    return localStorage.getItem(activeJobKey(sessionId));
  }

  async function sendInChat(session, modelSelected, text, messagesHost) {
    // Persist user msg.
    const userRow = await appendMessage(session.id, "user", text);
    messagesHost.appendChild(renderChatMessage(userRow));

    // Auto-name the session from the first user message if still untitled.
    const isUntitled =
      !session.title ||
      session.title === "untitled" ||
      session.title.trim() === "";
    if (isUntitled) {
      const derived = deriveSessionTitle(text);
      session.title = derived;
      try {
        await updateSession(session.id, {
          title: derived,
          model: session.model || "",
          project_id: session.project_id || null,
        });
        const titleEl = document.querySelector(".chat-title");
        if (titleEl) titleEl.textContent = derived;
        void loadSessions();
      } catch (e) {
        console.warn("auto-name failed", e);
      }
    }

    const fresh = await loadSessionDetail(session.id);
    const messages = (fresh.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
    }));

    // Background-job flow: server owns the request lifetime, the UI just
    // watches an event stream. Tab close ≠ job cancellation.
    let job;
    try {
      job = await api(`/v1/sessions/${encodeURIComponent(session.id)}/jobs`, {
        method: "POST",
        body: JSON.stringify({
          model: modelSelected || "deepseek/deepseek-v4-flash",
          messages,
          max_tokens: 4096,
          project_id: session.project_id || null,
          metadata: { session_id: session.id },
        }),
      });
    } catch (e) {
      const errMsg = { role: "assistant", content: `Error starting job: ${e.message}`, error: true };
      messagesHost.appendChild(renderChatMessage(errMsg));
      return;
    }
    setActiveJob(session.id, job.id);

    await streamJobIntoChat(session, job.id, messagesHost);
  }

  // streamJobIntoChat: open the SSE event-source for a job and pump chunks
  // into the chat view. Idempotent — safe to call on remount with an existing
  // job_id (replays from seq 0).
  async function streamJobIntoChat(session, jobId, messagesHost) {
    const assistant = {
      role: "assistant",
      content: "",
      streaming: true,
      created_at: Date.now(),
    };
    const node = renderChatMessage(assistant);
    messagesHost.appendChild(node);
    messagesHost.scrollTop = messagesHost.scrollHeight;

    // Inline cancel button — visible while the job is running, hidden when done.
    let cancelled = false;
    const cancelBtn = el(
      "button",
      {
        class: "btn btn-ghost btn-sm cancel-job-btn",
        type: "button",
        title: "Cancel this run",
        onclick: async () => {
          cancelBtn.disabled = true;
          cancelBtn.textContent = "Cancelling…";
          try {
            await api(`/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
              method: "POST",
            });
            cancelled = true;
            ctrl.abort();
          } catch (e) {
            toastShow(`Cancel failed: ${e.message}`, "error");
            cancelBtn.disabled = false;
            cancelBtn.textContent = "Stop";
          }
        },
      },
      "■ Stop"
    );
    node.appendChild(cancelBtn);

    let lastSeq = -1;
    let ctrl = new AbortController();

    // Smart auto-scroll: stick to the bottom unless the user manually
    // scrolled up. Detect "manually scrolled" by tracking distance-from-bottom
    // on the user's scroll events; programmatic scrollTop assignments don't
    // change this flag mid-flight.
    let stickToBottom = true;
    const BOTTOM_THRESHOLD = 80; // px
    const isNearBottom = () =>
      messagesHost.scrollHeight - messagesHost.scrollTop - messagesHost.clientHeight
        < BOTTOM_THRESHOLD;
    let lastUserScrollAt = 0;
    messagesHost.addEventListener("scroll", () => {
      lastUserScrollAt = Date.now();
      stickToBottom = isNearBottom();
    });

    const renderBody = () => {
      const body = node.querySelector(".message-body");
      if (body) body.innerHTML = md(assistant.content);
      // Only auto-scroll when the user has been at-bottom recently. If they
      // scrolled up to read earlier content, stay put.
      if (stickToBottom) {
        messagesHost.scrollTop = messagesHost.scrollHeight;
      } else if (Date.now() - lastUserScrollAt > 60_000) {
        // 1 minute of no scroll interaction — assume idle and snap back.
        messagesHost.scrollTop = messagesHost.scrollHeight;
      }
    };

    const consumeChunk = (rawBlock) => {
      // rawBlock may contain id:, event:, data: lines (one event)
      const lines = rawBlock.split("\n");
      let eventType = "sse";
      const dataParts = [];
      for (const line of lines) {
        if (line.startsWith("id:")) {
          const v = parseInt(line.slice(3).trim(), 10);
          if (!Number.isNaN(v)) lastSeq = v;
        } else if (line.startsWith("event:")) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataParts.push(line.slice(5).trim());
        }
      }
      const data = dataParts.join("");
      // Upstream SSE chunks carry their own ``event: content_block_delta``
      // etc. lines — anything that isn't a lifecycle marker we forward to
      // the Anthropic-SSE parser. Without this, every upstream event got
      // dropped because eventType was never "sse".
      const lifecycleEvents = new Set([
        "job_started",
        "job_finished",
        "job_error",
      ]);
      if (!lifecycleEvents.has(eventType)) {
        handleSseEvent(rawBlock, assistant);
        renderBody();
        return;
      }
      if (eventType === "job_finished" || eventType === "job_error") {
        if (eventType === "job_error") {
          assistant.error = true;
          try {
            const p = JSON.parse(data);
            assistant.content += (assistant.content ? "\n\n" : "") + `Error: ${p.error || "unknown"}`;
          } catch {
            assistant.content += (assistant.content ? "\n\n" : "") + "Error";
          }
          renderBody();
        }
        // job done — caller breaks out of stream
      }
    };

    const openStream = async () => {
      const headers = {
        Accept: "text/event-stream",
      };
      if (AUTH) headers.Authorization = `Bearer ${AUTH}`;
      if (lastSeq >= 0) headers["Last-Event-ID"] = String(lastSeq);
      const url = `/v1/jobs/${encodeURIComponent(jobId)}/events`;
      const response = await fetch(url, { headers, signal: ctrl.signal });
      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
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
          if (ev.trim() && !ev.trim().startsWith(":")) consumeChunk(ev);
        }
      }
    };

    // Page Visibility — when the tab regains focus, force-reconnect with the
    // last seen seq. Background tabs sometimes throttle fetch readers; this
    // guarantees catch-up replay of any events emitted while we were hidden.
    let pendingResume = false;
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return;
      pendingResume = true;
      try {
        ctrl.abort(); // openStream's reader.read() rejects → outer loop reconnects
      } catch {
        /* ignore */
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    try {
      // Auto-reconnect loop: if the stream drops while the job is still
      // running, reconnect with the last seen seq.
      while (true) {
        try {
          await openStream();
        } catch (e) {
          if (!pendingResume && e.name !== "AbortError") {
            console.warn("event stream interrupted:", e);
          }
        }
        pendingResume = false;
        const job = await api(`/v1/jobs/${encodeURIComponent(jobId)}`).catch(() => null);
        if (!job) break;
        if (["done", "error", "cancelled"].includes(job.status)) break;
        // job still running but our connection dropped — wait then resume
        await new Promise((r) => setTimeout(r, 400));
        ctrl = new AbortController();
      }
    } finally {
      document.removeEventListener("visibilitychange", onVisibility);
      assistant.streaming = false;
      node.classList.remove("streaming");
      if (assistant.error) node.classList.add("error");
      if (cancelled) {
        assistant.content +=
          (assistant.content ? "\n\n" : "") + "_— cancelled by user_";
      }
      renderBody();
      cancelBtn.remove();
      setActiveJob(session.id, null);
      try {
        await appendMessage(session.id, "assistant", assistant.content);
      } catch (e) {
        console.warn("persist failed:", e);
      }
      void refreshFooterMetrics();
      // Async auto-rename via the model if still untitled.
      void maybeRenameSessionAsync(session, assistant.content);
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
    const wsBrowseBtn = el(
      "button",
      {
        type: "button",
        class: "btn btn-ghost btn-sm",
        onclick: () => openFolderPicker(wsIn.value || "", (chosen) => {
          wsIn.value = chosen;
        }),
      },
      "Browse…"
    );
    const wsRow = el("div", { class: "row gap-2 align-center", style: { width: "100%" } }, wsIn, wsBrowseBtn);
    wsIn.style.flex = "1";

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
        wsRow,
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
          "Visible to Companion for every session attached to this project."
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
    const tbody = el("tbody", {});
    data.edits.forEach((e) => {
      let meta = {};
      try {
        meta = typeof e.metadata === "string" ? JSON.parse(e.metadata) : (e.metadata || {});
      } catch {
        meta = {};
      }
      const hasDiff = typeof meta.diff === "string" && meta.diff.trim();
      const row = el(
        "tr",
        {
          class: hasDiff ? "edits-row clickable" : "edits-row",
          style: hasDiff ? "cursor:pointer;" : "",
        },
        el("td", { class: "mono" }, fmtTime(e.ts)),
        el("td", {}, el("span", { class: "badge" }, e.op)),
        el(
          "td",
          { class: "mono truncate", style: { maxWidth: "640px" } },
          e.path
        ),
        el(
          "td",
          { class: "num tabular" },
          (e.bytes_delta >= 0 ? "+" : "") + String(e.bytes_delta)
        ),
        el(
          "td",
          { class: "muted fs-12" },
          hasDiff ? "click to expand ▾" : ""
        )
      );
      tbody.appendChild(row);
      if (hasDiff) {
        const diffRow = el("tr", { class: "edits-diff-row", style: "display:none;" });
        const diffCell = el(
          "td",
          { colspan: "5", style: "padding:0;" },
          renderDiff(meta.diff)
        );
        diffRow.appendChild(diffCell);
        tbody.appendChild(diffRow);
        row.addEventListener("click", () => {
          const open = diffRow.style.display !== "none";
          diffRow.style.display = open ? "none" : "table-row";
          const indicator = row.lastChild;
          if (indicator) {
            indicator.textContent = open ? "click to expand ▾" : "▴ collapse";
          }
        });
      }
    });
    const tbl = el(
      "table",
      { class: "table edits-table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", {}, "time"),
          el("th", {}, "op"),
          el("th", {}, "path"),
          el("th", {}, "Δ bytes"),
          el("th", {}, "")
        )
      ),
      tbody
    );
    body.appendChild(el("div", { class: "card" }, tbl));
  }

  // Render a unified-diff string as colorised lines.
  function renderDiff(diffText) {
    const pre = el("pre", { class: "diff-pre" });
    const lines = (diffText || "").split("\n");
    lines.forEach((line) => {
      let cls = "diff-context";
      if (line.startsWith("+++") || line.startsWith("---")) cls = "diff-header";
      else if (line.startsWith("@@")) cls = "diff-hunk";
      else if (line.startsWith("+")) cls = "diff-add";
      else if (line.startsWith("-")) cls = "diff-del";
      pre.appendChild(el("span", { class: cls }, line + "\n"));
    });
    return pre;
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
          el("code", {}, "pkill -f free-claude-code && uv run fcc-server"),
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
        if (item.cta_action && Object.keys(item.cta_action).length) {
          // One-click activation: hit /v1/env for each key/value, then re-render.
          card.appendChild(
            el(
              "button",
              {
                class: "btn btn-primary btn-sm",
                type: "button",
                onclick: async (e) => {
                  const btn = e.currentTarget;
                  btn.disabled = true;
                  btn.textContent = "Working…";
                  try {
                    for (const [k, v] of Object.entries(item.cta_action)) {
                      await api("/v1/env", {
                        method: "PUT",
                        body: JSON.stringify({ key: k, value: String(v) }),
                      });
                    }
                    toastShow(`${item.title} — enabled`, "ok");
                    renderSettings();
                  } catch (err) {
                    toastShow(`Failed: ${err.message}`, "error");
                    btn.disabled = false;
                    btn.textContent = item.cta_label || "Enable";
                  }
                },
              },
              item.cta_label || "Enable"
            )
          );
        } else if (item.cta_field) {
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
