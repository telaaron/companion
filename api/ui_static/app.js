// companion dashboard — vanilla JS SPA.
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
  function _relativeTime(tsMs) {
    if (!tsMs) return "never";
    const diff = Date.now() - new Date(tsMs).getTime();
    if (diff < 0) return "now";
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return sec <= 1 ? "1 second ago" : sec + " seconds ago";
    const min = Math.floor(sec / 60);
    if (min < 60) return min === 1 ? "1 minute ago" : min + " minutes ago";
    const hrs = Math.floor(min / 60);
    if (hrs < 24) return hrs === 1 ? "1 hour ago" : hrs + " hours ago";
    const days = Math.floor(hrs / 24);
    if (days === 1) return "1 day ago";
    return days + " days ago";
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
        const toolName = o.name || "";
        const toolLower = toolName.toLowerCase();
        const rawArgs = (o.args || "").trim();

        const isFileOp = /^(Read|Write|Edit)$/i.test(toolName);
        const filePath = isFileOp ? rawArgs.split(",")[0].trim().replace(/^["']|["']$/g, "") : "";
        const dataPath = filePath ? ` data-filepath="${escapeHtml(filePath)}"` : "";
        const dataKind = isFileOp
          ? ` data-filekind="${escapeHtml(/^Write$/i.test(toolName) ? "write" : "read")}"`
          : "";
        const clickable = isFileOp && filePath ? ` tool-block-clickable` : "";

        // Meta summary — lines / size in brief
        const bodyText = o.body || "";
        const bodyLines = bodyText ? bodyText.split("\n").length : 0;
        const meta = bodyText
          ? `<span class="tool-meta">${bodyLines} line${bodyLines !== 1 ? "s" : ""}</span>`
          : "";

        // Inline header — fits on a single line
        const header =
          `<span class="tool-glyph">${escapeHtml(o.icon || "●")}</span>` +
          `<span class="tool-name">${escapeHtml(toolName)}</span>` +
          `<span class="tool-args">${escapeHtml(rawArgs)}</span>` +
          meta;

        // Body: wrap in <details> so output is collapsed by default.
        const inner = bodyText
          ? `<details class="tool-block-toggle"><summary class="tool-block-head">${header}</summary><pre class="tool-block-body">${escapeHtml(bodyText)}</pre></details>`
          : `<div class="tool-block-head tool-block-head-static">${header}</div>`;

        return `<div class="tool-block${clickable}" data-tool="${escapeHtml(toolLower)}"${dataPath}${dataKind}>${inner}</div>`;
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

  // ============================================================ Credential masking
  function maskCredentials(text) {
    if (!text) return text;
    return text
      // Mask Bearer/Basic tokens: "Bearer abc123..." -> "Bearer ****abc"
      .replace(/\b(Bearer|bearer|Basic|basic)\s+([A-Za-z0-9\-_+/=]{8,})/g,
        (_m, scheme, token) => {
          const vis = Math.min(4, Math.floor(token.length / 4));
          return scheme + " " + token.slice(0, vis) + "****" + token.slice(-vis);
        })
      // Mask --flag VALUE patterns (e.g. --api-key sk-abc -> --api-key sk-****)
      .replace(/(--(?:api[_-]?key|token|auth|secret|password|bearer|access[_-]?token|key|authorization))\s+(\S+)/gi,
        (_m, flag, value) => {
          const vis = Math.min(4, Math.floor(value.length / 4));
          return flag + " " + value.slice(0, vis) + "****" + value.slice(-vis);
        })
      // Mask --flag=VALUE patterns
      .replace(/(--(?:api[_-]?key|token|auth|secret|password|bearer|access[_-]?token|key|authorization))=(\S+)/gi,
        (_m, flag, value) => {
          const vis = Math.min(4, Math.floor(value.length / 4));
          return flag + "=" + value.slice(0, vis) + "****" + value.slice(-vis);
        })
      // Mask bare hex strings that look like API keys (>= 32 hex chars)
      .replace(/\b([A-Fa-f0-9]{32,})\b/g,
        (_m, hex) => hex.slice(0, 4) + "****" + hex.slice(-4));
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
    memory: { label: "Memory", render: () => renderMemory() },
    routines: { label: "Routines", render: () => renderRoutines() },
    insights: { label: "Insights", render: () => renderInsights() },
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
    if (typeof lucide !== "undefined") lucide.createIcons();
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
              el("i", { "data-lucide": "arrow-up", class: "folder-icon" }),
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
              el("i", { "data-lucide": "folder", class: "folder-icon" }),
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

  // Async session rename — POSTs to /v1/sessions/{id}/auto-rename which calls
  // the configured provider server-side (single round-trip, no tools).
  // Falls back to the derived title from the first user message on any error.
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

    // Resolve first user message for the rename payload.
    let firstUserMsg = "";
    try {
      const fresh = await loadSessionDetail(session.id);
      const msgs = fresh.messages || [];
      const firstUser = msgs.find((m) => m.role === "user");
      if (firstUser) firstUserMsg = firstUser.content || "";
    } catch {
      /* ignore — proceed with empty first user msg */
    }

    const _doRename = async () => {
      const headers = { "Content-Type": "application/json" };
      if (AUTH) headers.Authorization = `Bearer ${AUTH}`;
      const response = await fetch(
        `/v1/sessions/${encodeURIComponent(session.id)}/auto-rename`,
        {
          method: "POST",
          headers,
          body: JSON.stringify({
            first_user_message: firstUserMsg.slice(0, 600),
            first_assistant_message: (assistantReply || "").slice(0, 600),
          }),
        }
      );
      if (!response.ok) return "";
      const data = await response.json();
      return (data.title || "").trim();
    };

    const _applyTitle = (title) => {
      if (!title || title.length < 3) return false;
      session.title = title;
      const titleEl = document.querySelector(".chat-title");
      if (titleEl) titleEl.textContent = title;
      void loadSessions();
      return true;
    };

    try {
      let title = await _doRename();
      if (_applyTitle(title)) return;

      // First attempt returned falsy/unparseable — retry once after 3 s.
      console.log("auto-rename: first attempt returned empty title, retrying in 3 s…");
      await new Promise((resolve) => setTimeout(resolve, 3000));
      title = await _doRename();
      if (_applyTitle(title)) return;

      // Both attempts failed — fall back to derived title from first user msg.
      console.log("auto-rename: both attempts failed, using derived fallback");
    } catch (err) {
      console.warn("auto-rename failed:", err);
    }
  }

  // Wrap a <select> with a label-prefix for the chat topbar.
  function wrapSelect(label, select) {
    select.classList.add("topbar-select-input");
    return el(
      "label",
      { class: "topbar-select" },
      el("span", { class: "topbar-select-label" }, label),
      select
    );
  }

  async function refreshSessionUsage(sessionId, badgeEl) {
    try {
      const usage = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/usage`);
      const total = (usage.input_tokens || 0) + (usage.output_tokens || 0);
      const tokens = badgeEl.querySelector(".chat-usage-tokens");
      const cost = badgeEl.querySelector(".chat-usage-cost");
      if (tokens) tokens.textContent = fmtTokens(total);
      if (cost) cost.textContent = fmtUsd(usage.cost_usd || 0);
    } catch {
      /* no-op — show defaults */
    }
  }

  function fmtTokens(n) {
    if (!n) return "0 tok";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M tok";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "k tok";
    return `${n} tok`;
  }

  // Quick-save: build a synthetic prompt that asks the AI to summarise the
  // conversation + push to the configured Obsidian vault. Falls back to
  // toast if no vault tool is available.
  async function saveSessionToVault(session, modelSelected, messagesHost) {
    const today = new Date().toISOString().slice(0, 10);
    const prompt =
      "Summarise the key insights, decisions, and TODOs from this " +
      "conversation as a concise Markdown note. Then call the Obsidian " +
      "Append tool to save it under `" +
      `Companion/${today}-${(session.title || "untitled").replace(/[^a-zA-Z0-9-_]/g, "-").slice(0, 40)}.md` +
      "`. If no Obsidian vault tool is registered, write the note to " +
      "`AGENTS.md` instead and tell me which path you used.";
    await sendInChat(session, modelSelected, prompt, messagesHost);
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
          el("i", { "data-lucide": "message-circle", class: "empty-icon" }),
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

  // ============================================================ Voice mode
  //
  // Mic icon button in composer row. Click to start/stop recording.
  // Returns button node (null if MediaRecorder unavailable).

  function _buildVoiceButton(ta) {
    if (!window.MediaRecorder || !navigator.mediaDevices) return null;

    let _recording = false;
    let _mediaRecorder = null;
    let _stream = null;
    let _chunks = [];

    const btn = el("button", {
      class: "mic-btn",
      type: "button",
      title: "Record voice input",
    }, el("i", { "data-lucide": "mic" }));

    async function _start() {
      try {
        _stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (_e) {
        toastShow("Microphone access denied", "error");
        return;
      }
      _chunks = [];
      _mediaRecorder = new MediaRecorder(_stream, {
        mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : "audio/webm",
      });
      _mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) _chunks.push(e.data);
      };
      _mediaRecorder.start(100);
      _recording = true;
      btn.classList.add("recording");
      btn.title = "Stop recording";
    }

    async function _stop() {
      if (!_recording || !_mediaRecorder) return;
      _recording = false;
      btn.classList.remove("recording");
      btn.classList.add("transcribing");
      btn.title = "Record voice input";

      await new Promise((resolve) => {
        _mediaRecorder.onstop = resolve;
        _mediaRecorder.stop();
      });
      if (_stream) {
        _stream.getTracks().forEach((t) => t.stop());
        _stream = null;
      }

      const blob = new Blob(_chunks, { type: _mediaRecorder.mimeType });
      _chunks = [];
      btn.classList.remove("transcribing");

      if (blob.size < 100) return;

      const form = new FormData();
      form.append("audio", blob, "audio.webm");
      try {
        const res = await fetch("/v1/transcribe", {
          method: "POST",
          headers: { Authorization: "Bearer " + AUTH },
          body: form,
        });
        if (!res.ok) {
          let msg = `Transcription error (${res.status})`;
          try {
            const body = await res.json();
            if (body.detail) msg = body.detail;
          } catch {
            /* use default message */
          }
          toastShow(msg, "error");
          return;
        }
        const data = await res.json();
        const text = (data.text || "").trim();
        if (!text) {
          toastShow("Voice recording captured but no speech detected.", "warning");
          return;
        }
        ta.value = ta.value ? ta.value + " " + text : text;
        ta.dispatchEvent(new Event("input"));
        ta.focus();
      } catch (_e) {
        toastShow("Voice transcription failed — is the server reachable?", "error");
      }
    }

    btn.addEventListener("click", () => {
      if (_recording) _stop();
      else _start();
    });

    return btn;
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

    // Model select — only provider-discovered models. No hardcoded Claude
    // ids (they require an upstream key most users don't have).
    const modelOptions = [];
    for (const p of upstreamModels.providers || []) {
      for (const m of p.models || []) {
        modelOptions.push(`${p.provider}/${m}`);
      }
    }
    const defaultModel =
      session.model || modelOptions[0] || "deepseek/deepseek-v4-flash";
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
          { value: m, selected: m === defaultModel },
          m
        )
      )
    );

    const usageBadge = el(
      "div",
      { class: "chat-usage", title: "Token + cost for this conversation" },
      el("span", { class: "chat-usage-tokens" }, "0 tok"),
      el("span", { class: "chat-usage-sep" }, "·"),
      el("span", { class: "chat-usage-cost" }, "$0.000")
    );
    void refreshSessionUsage(session.id, usageBadge);

    const vaultBtn = el(
      "button",
      {
        class: "icon-btn",
        title: "Save key insights to Obsidian vault",
        onclick: () => saveSessionToVault(session, modelSelect.value, messages),
      },
      el("i", { "data-lucide": "notebook-pen" })
    );

    const deleteBtn = el(
      "button",
      {
        class: "icon-btn icon-btn-danger",
        title: "Delete session",
        onclick: async () => {
          if (!confirm("Delete this session?")) return;
          await deleteSession(session.id);
          activeSessionId = null;
          await renderChat();
        },
      },
      el("i", { "data-lucide": "x" })
    );

    const titleRow = el(
      "div",
      { class: "chat-topbar-titlerow" },
      title,
      usageBadge
    );
    const controlsRow = el(
      "div",
      { class: "chat-topbar-controls" },
      el(
        "div",
        { class: "chat-topbar-selects" },
        wrapSelect("project", projectSelect),
        wrapSelect("model", modelSelect)
      ),
      el("div", { class: "chat-topbar-actions" }, vaultBtn, deleteBtn)
    );
    topbar.append(titleRow, controlsRow);
    host.appendChild(topbar);

    const messages = el("div", { class: "messages" });
    host.appendChild(messages);
    // Stash regen-ctx on the host so streamJobIntoChat can find it after a
    // streaming reply finishes (no signature churn through callers).
    messages._regenCtx = {
      session,
      modelOptions,
      defaultModel,
      getMessagesHost: () => messages,
    };

    // Event delegation: click on a file-tool block opens the preview panel.
    messages.addEventListener("click", (e) => {
      const block = e.target.closest(".tool-block-clickable");
      if (!block) return;
      const path = block.dataset.filepath;
      const kind = block.dataset.filekind || "read";
      if (!path) return;
      _previewPush(session.id, path, kind);
      openPreviewPanel(path, session.id);
    });

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
    // Insert mic button before send button (after ta is available).
    const _micBtn = _buildVoiceButton(ta);
    if (_micBtn) {
      const sendBtn = composer.querySelector("button[type=submit]");
      sendBtn.parentNode.insertBefore(_micBtn, sendBtn);
    }
    host.appendChild(composer);

    // Render existing messages; populate preview LRU from past tool calls.
    const _seenPaths = new Set();
    const _trackMsg = (content) => {
      const toolRe = /^[●✗⏺]\s+(Read|Write|Edit)\(([^)]*)\)\s*$/gm;
      let m;
      while ((m = toolRe.exec(content || "")) !== null) {
        const rawPath = m[2].split(",")[0].trim().replace(/^["']|["']$/g, "");
        if (rawPath && !_seenPaths.has(rawPath)) {
          _seenPaths.add(rawPath);
          const kind = /^Write$/i.test(m[1]) ? "write" : "read";
          _previewPush(session.id, rawPath, kind);
        }
      }
    };
    for (const m of session.messages || []) {
      if (m.role === "assistant") _trackMsg(m.content);
      messages.appendChild(renderChatMessage(m, messages._regenCtx));
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

  function renderChatMessage(msg, ctx) {
    const wrap = el(
      "article",
      { class: "message " + (msg.role || "assistant"), "data-msg-id": msg.id || "" },
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

    // Regenerate button on assistant bubbles (Perplexity-style).
    // Skip while streaming — only finalized replies get one.
    if (
      ctx &&
      (msg.role || "assistant") === "assistant" &&
      !msg.streaming &&
      !msg.error
    ) {
      attachRegenerateButton(wrap, ctx);
      attachPinButton(wrap, msg, ctx);
    }
    return wrap;
  }

  // attachRegenerateButton: ↺ overlay opens model picker.
  // Picking a model REPLACES this assistant bubble with a fresh one —
  // the old message is removed from DB + DOM before the new job runs.
  function attachRegenerateButton(messageNode, ctx) {
    const btn = el(
      "button",
      {
        class: "regen-btn",
        type: "button",
        title: "Regenerate with another model",
      },
      "↺"
    );
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      document.querySelectorAll(".regen-popover").forEach((n) => n.remove());

      const pop = el("div", { class: "regen-popover" });
      pop.appendChild(el("div", { class: "regen-popover-header" }, "Regenerate with…"));

      // Search filter (UX-010)
      const filterInput = el("input", {
        class: "regen-popover-filter",
        type: "text",
        placeholder: "Filter models…",
      });

      const list = el("div", { class: "regen-popover-list" });

      function renderList(filter) {
        list.innerHTML = "";
        const q = (filter || "").toLowerCase().trim();
        let visible = 0;
        for (const m of ctx.modelOptions) {
          if (q && !m.toLowerCase().includes(q)) continue;
          visible++;
          const item = el("button", {
            class: "regen-popover-item",
            type: "button",
            title: m, // tooltip for long names (UX-009)
          }, m);
          if (m === ctx.defaultModel) item.classList.add("current");
          item.addEventListener("click", async (ev) => {
            ev.stopPropagation();
            pop.remove();
            await regenInChat(messageNode, m, ctx);
          });
          list.appendChild(item);
        }
        if (!visible) {
          list.appendChild(el("div", { class: "regen-popover-empty" }, "No models match"));
        }
      }

      // Filter on input (debounced)
      let filterTimer;
      filterInput.addEventListener("input", () => {
        clearTimeout(filterTimer);
        filterTimer = setTimeout(() => renderList(filterInput.value), 60);
      });

      pop.appendChild(filterInput);
      pop.appendChild(list);
      renderList("");
      messageNode.appendChild(pop);
      setTimeout(() => filterInput.focus(), 60);

      const dismiss = (ev) => {
        if (!pop.contains(ev.target) && ev.target !== btn) {
          pop.remove();
          document.removeEventListener("click", dismiss);
        }
      };
      setTimeout(() => document.addEventListener("click", dismiss), 0);
    });
    messageNode.appendChild(btn);
  }

  // regenInChat: remove old assistant bubble from DOM + DB, re-run job.
  async function regenInChat(messageNode, model, ctx) {
    const messagesHost = ctx.getMessagesHost();

    // Delete from DB if node carries a message id.
    const msgId = messageNode.dataset.msgId;
    if (msgId) {
      try {
        await api(
          `/v1/sessions/${encodeURIComponent(ctx.session.id)}/messages/${encodeURIComponent(msgId)}`,
          { method: "DELETE" }
        );
      } catch (_e) {
        // Non-fatal — proceed anyway; stale row harmless.
      }
    }
    // Remove from DOM.
    messageNode.remove();

    // Fire a new job with the current history (which now ends at the user msg).
    const fresh = await loadSessionDetail(ctx.session.id);
    const messages = (fresh.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
    }));
    if (!messages.length || messages[messages.length - 1].role !== "user") {
      toastShow("Nothing to regenerate — no prior user message.", "error");
      return;
    }

    let job;
    try {
      job = await api(`/v1/sessions/${encodeURIComponent(ctx.session.id)}/jobs`, {
        method: "POST",
        body: JSON.stringify({
          model: model || ctx.defaultModel || "deepseek/deepseek-v4-flash",
          messages,
          max_tokens: 4096,
          project_id: ctx.session.project_id || null,
          metadata: { session_id: ctx.session.id },
        }),
      });
    } catch (e) {
      messagesHost.appendChild(
        renderChatMessage({ role: "assistant", content: `Error: ${e.message}`, error: true })
      );
      return;
    }
    setActiveJob(ctx.session.id, job.id);
    await streamJobIntoChat(ctx.session, job.id, messagesHost);
  }

  function findPrevUserText(assistantNode) {
    let cursor = assistantNode.previousElementSibling;
    while (cursor) {
      if (cursor.classList && cursor.classList.contains("user")) {
        const body = cursor.querySelector(".message-body");
        return body ? body.innerText.trim() : "";
      }
      cursor = cursor.previousElementSibling;
    }
    return "";
  }

  // attachPinButton: 📌 kebab-menu action on finalized assistant bubbles.
  // Only shown when the session belongs to a project.
  function attachPinButton(messageNode, msg, ctx) {
    const projectId = ctx.session && ctx.session.project_id;
    if (!projectId) return;

    const btn = el(
      "button",
      {
        class: "pin-btn",
        type: "button",
        title: "Pin to project memory",
      },
      el("i", { "data-lucide": "pin" })
    );
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const body = messageNode.querySelector(".message-body");
      const content = body ? body.innerText.trim() : (msg.content || "").trim();
      if (!content) {
        toastShow("Nothing to pin — empty message.", "error");
        return;
      }
      try {
        await api(
          `/v1/projects/${encodeURIComponent(projectId)}/memories`,
          {
            method: "POST",
            body: JSON.stringify({
              content: content.slice(0, 4000),
              source_session_id: ctx.session.id,
            }),
          }
        );
        toastShow("Pinned to project memory ✓", "ok");
      } catch (err) {
        toastShow(`Pin failed: ${err.message}`, "error");
      }
    });
    messageNode.appendChild(btn);
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

    // Register file-tool paths in the preview LRU as they stream in.
    const _registeredPaths = new Set();
    const _trackFilePaths = (content) => {
      const toolRe = /^[●✗⏺]\s+(Read|Write|Edit)\(([^)]*)\)\s*$/gm;
      let m;
      while ((m = toolRe.exec(content)) !== null) {
        const toolName = m[1];
        const rawPath = m[2].split(",")[0].trim().replace(/^["']|["']$/g, "");
        if (rawPath && !_registeredPaths.has(rawPath)) {
          _registeredPaths.add(rawPath);
          const kind = /^Write$/i.test(toolName) ? "write" : "read";
          _previewPush(session.id, rawPath, kind);
        }
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
        _trackFilePaths(assistant.content);
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

    // ─── Resilient stream: native EventSource + Last-Event-ID resume,
    // online/offline awareness, and periodic terminal-status polling.
    //
    // EventSource is significantly more resilient than fetch+reader:
    //   • Auto-reconnects on network drops with exponential backoff.
    //   • Sends Last-Event-ID header on reconnect — server replays.
    //   • Survives Chrome background-tab throttling without hanging.
    //
    // EventSource cannot set custom headers, so auth is via ?token= query
    // param (handled by require_api_key in api/dependencies.py).
    //
    // Defensive layers stacked on top of EventSource's built-ins:
    //   1. Online/offline listeners — close on offline, re-open on online.
    //   2. Periodic /v1/jobs/{id} poll every 5 s — detects terminal status
    //      even if EventSource never closes (e.g. proxy ate close frame).
    //   3. Exponential backoff cap to avoid hammering the server.

    let es = null;
    let pollTimer = null;
    let backoffMs = 500;
    const BACKOFF_CAP_MS = 8000;
    let terminal = false;

    const feedEvent = (eventType, data, lastEventId) => {
      if (lastEventId) {
        const v = parseInt(lastEventId, 10);
        if (!Number.isNaN(v)) lastSeq = v;
      }
      consumeChunk(`event: ${eventType}\ndata: ${data}\n\n`);
    };

    const ANTHROPIC_EVENTS = [
      "message_start",
      "message_delta",
      "message_stop",
      "content_block_start",
      "content_block_delta",
      "content_block_stop",
      "ping",
      "error",
    ];
    const LIFECYCLE_EVENTS = ["job_started", "job_finished", "job_error"];

    const buildUrl = () => {
      const u = new URL(
        `/v1/jobs/${encodeURIComponent(jobId)}/events`,
        window.location.origin
      );
      if (AUTH) u.searchParams.set("token", AUTH);
      // EventSource sends Last-Event-ID natively after first connect,
      // but on initial open it doesn't — pass via query param for resume.
      if (lastSeq >= 0) u.searchParams.set("last_event_id", String(lastSeq));
      return u.toString();
    };

    const closeStream = () => {
      if (es) {
        try { es.close(); } catch { /* ignore */ }
        es = null;
      }
    };

    const finishLoop = async () => {
      terminal = true;
      closeStream();
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    };

    const openStream = () => {
      closeStream();
      es = new EventSource(buildUrl());
      es.onopen = () => { backoffMs = 500; };
      es.onerror = () => {
        // EventSource auto-reconnects, but if browser gives up (e.g. 401)
        // we manually re-open after backoff with the latest lastSeq.
        if (terminal) return;
        if (es && es.readyState === EventSource.CLOSED) {
          setTimeout(() => {
            if (terminal) return;
            backoffMs = Math.min(backoffMs * 2, BACKOFF_CAP_MS);
            openStream();
          }, backoffMs);
        }
      };
      const wire = (name) => {
        es.addEventListener(name, (e) => {
          if (terminal) return;
          feedEvent(name, e.data || "", e.lastEventId);
          if (name === "job_finished" || name === "job_error") {
            void finishLoop();
          }
        });
      };
      ANTHROPIC_EVENTS.forEach(wire);
      LIFECYCLE_EVENTS.forEach(wire);
    };

    // Fallback poll: if EventSource somehow misses the terminal lifecycle
    // event, the job-status poll detects it and closes the loop.
    pollTimer = setInterval(async () => {
      if (terminal) return;
      try {
        const job = await api(`/v1/jobs/${encodeURIComponent(jobId)}`);
        if (job && ["done", "error", "cancelled"].includes(job.status)) {
          await finishLoop();
        }
      } catch { /* network blip — ignore */ }
    }, 5000);

    // Online/offline: pause on offline, resume on online with full replay.
    const onOnline = () => {
      if (terminal) return;
      backoffMs = 500;
      openStream();
    };
    const onOffline = () => {
      closeStream();
    };
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);

    // Hook user-cancel: ctrl.abort() must also tear down EventSource.
    ctrl.signal.addEventListener("abort", () => { void finishLoop(); });

    try {
      openStream();
      // Wait until the loop signals terminal (lifecycle event OR poll).
      while (!terminal) {
        await new Promise((r) => setTimeout(r, 250));
      }
    } finally {
      terminal = true;
      closeStream();
      if (pollTimer) clearInterval(pollTimer);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
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
      // Attach regenerate button now that the reply is finalized.
      if (!assistant.error && messagesHost._regenCtx) {
        attachRegenerateButton(node, messagesHost._regenCtx);
      }
      try {
        await appendMessage(session.id, "assistant", assistant.content);
      } catch (e) {
        console.warn("persist failed:", e);
      }
      void refreshFooterMetrics();
      // Refresh per-chat token + cost counter.
      const badge = document.querySelector(".chat-usage");
      if (badge) void refreshSessionUsage(session.id, badge);
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

    // Pull all sessions once + bucket by project for the cards below.
    const allSessions = await loadSessions();
    const byProject = new Map();
    for (const s of allSessions) {
      if (!s.project_id) continue;
      if (!byProject.has(s.project_id)) byProject.set(s.project_id, []);
      byProject.get(s.project_id).push(s);
    }

    for (const p of projects) {
      const sessions = byProject.get(p.id) || [];
      const card = el("article", { class: "project-card" });

      const header = el(
        "div",
        { class: "project-card-header" },
        el("div", {
          class: "project-card-color",
          style: { background: p.color || "#6366f1" },
        }),
        el(
          "div",
          { class: "project-card-meta" },
          el("div", { class: "project-card-name" }, p.name),
          el(
            "div",
            { class: "project-card-desc" },
            p.description || (p.workspace_path
              ? el("span", { class: "mono" }, p.workspace_path)
              : "—")
          )
        ),
        el(
          "div",
          { class: "project-card-actions" },
          el(
            "button",
            {
              class: "icon-btn",
              title: "Edit",
              onclick: () => editProject(p),
            },
            el("i", { "data-lucide": "pencil" })
          ),
          el(
            "button",
            {
              class: "icon-btn",
              title: "New chat in this project",
              onclick: async () => {
                const s = await createSession({
                  title: "untitled",
                  model: "",
                  project_id: p.id,
                });
                activeSessionId = s.id;
                setRoute("chat");
              },
            },
            el("i", { "data-lucide": "plus" })
          ),
          el(
            "button",
            {
              class: "icon-btn icon-btn-danger",
              title: "Delete project",
              onclick: async () => {
                if (!confirm(`Delete project ${p.name}?`)) return;
                await api(`/v1/projects/${encodeURIComponent(p.id)}`, {
                  method: "DELETE",
                });
                renderProjects();
              },
            },
            el("i", { "data-lucide": "x" })
          )
        )
      );
      card.appendChild(header);

      const sessList = el("div", { class: "project-sessions" });
      if (!sessions.length) {
        sessList.appendChild(
          el(
            "div",
            { class: "project-sessions-empty" },
            "No sessions yet — click + to start one"
          )
        );
      } else {
        for (const s of sessions.slice(0, 12)) {
          sessList.appendChild(
            el(
              "div",
              {
                class: "project-session-row",
                onclick: () => {
                  activeSessionId = s.id;
                  setRoute("chat");
                },
              },
              el(
                "span",
                { class: "project-session-title" },
                s.title || "untitled"
              ),
              el(
                "span",
                { class: "project-session-time" },
                fmtTime(s.updated_at)
              )
            )
          );
        }
        if (sessions.length > 12) {
          sessList.appendChild(
            el(
              "div",
              { class: "project-sessions-empty" },
              `+ ${sessions.length - 12} more`
            )
          );
        }
      }
      card.appendChild(sessList);

      // Memories panel: list pinned memories with delete + session link.
      const memPanel = el("div", { class: "project-memories" });
      const memTitle = el("div", { class: "project-memories-title" },
        el("i", { "data-lucide": "pin", style: { marginRight: "6px" } }), "Memories");
      memPanel.appendChild(memTitle);

      async function refreshMemories() {
        // Remove all children except the title.
        while (memPanel.children.length > 1) memPanel.removeChild(memPanel.lastChild);
        let mems;
        try {
          const resp = await api(
            `/v1/projects/${encodeURIComponent(p.id)}/memories`
          );
          mems = resp.memories || [];
        } catch {
          mems = [];
        }
        if (!mems.length) {
          memPanel.appendChild(
            el(
              "div",
              { class: "project-sessions-empty" },
              "No pinned memories yet — use the pin button on an assistant reply"
            )
          );
          return;
        }
        for (const m of mems) {
          const row = el("div", { class: "memory-row" });
          const snippet = el(
            "div",
            { class: "memory-content" },
            m.content.length > 120 ? m.content.slice(0, 120) + "…" : m.content
          );
          const meta = el(
            "div",
            { class: "memory-meta" },
            fmtTime(m.created_at)
          );
          if (m.source_session_id) {
            const link = el(
              "button",
              {
                class: "memory-link",
                type: "button",
                title: "Open source session",
                onclick: (e) => {
                  e.stopPropagation();
                  activeSessionId = m.source_session_id;
                  setRoute("chat");
                },
              },
              el("i", { "data-lucide": "external-link" })
            );
            meta.appendChild(link);
          }
          const delBtn = el(
            "button",
            {
              class: "icon-btn icon-btn-danger",
              type: "button",
              title: "Delete memory",
              onclick: async (e) => {
                e.stopPropagation();
                try {
                  await api(
                    `/v1/projects/${encodeURIComponent(p.id)}/memories/${encodeURIComponent(m.id)}`,
                    { method: "DELETE" }
                  );
                  toastShow("Memory deleted", "ok");
                  refreshMemories();
                } catch (err) {
                  toastShow(`Delete failed: ${err.message}`, "error");
                }
              },
            },
            el("i", { "data-lucide": "x" })
          );
          row.append(snippet, meta, delBtn);
          memPanel.appendChild(row);
        }
      }

      refreshMemories();
      card.appendChild(memPanel);
      body.appendChild(card);
    }
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
        sub: "your global ~/.config/companion/.env",
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
          el("code", {}, "pkill -f companion-server && uv run companion-server"),
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
        title: "Tools & Connectors",
        sub: "MCP servers + skills · auto-discovered from your Claude install",
      })
    );
    const body = el("div", { class: "page-body" });
    view.appendChild(body);

    // ---------- MCP servers section
    const mcpSection = el("section", { class: "skills-section" });
    mcpSection.appendChild(
      el(
        "div",
        { class: "section-heading" },
        el("h3", {}, "MCP servers"),
        el(
          "span",
          { class: "muted fs-12" },
          "discovered from claude_desktop_config.json"
        )
      )
    );
    const mcpGrid = el("div", { class: "grid-cards" });
    mcpSection.appendChild(mcpGrid);
    body.appendChild(mcpSection);
    const mcpData = await api("/v1/mcp").catch(() => ({ discovered: [], running: [] }));
    const runningByName = Object.fromEntries(
      (mcpData.running || []).map((r) => [r.name, r])
    );
    if (!mcpData.discovered?.length) {
      mcpGrid.appendChild(
        el(
          "div",
          { class: "empty" },
          el("i", { "data-lucide": "cable", class: "empty-icon" }),
          el("div", { class: "empty-title" }, "No MCP servers discovered"),
          el(
            "div",
            { class: "empty-sub" },
            "Configure Claude Desktop's mcpServers or drop a YAML in plugins/"
          )
        )
      );
    } else {
      for (const srv of mcpData.discovered) {
        const live = runningByName[srv.name];
        const status = live?.status || "stopped";
        const toolCount = live?.tool_count || 0;
        const card = el(
          "article",
          { class: "mcp-card mcp-" + status },
          el(
            "header",
            { class: "mcp-card-header" },
            el(
              "div",
              { class: "mcp-card-meta" },
              el("strong", {}, srv.name),
              el("div", { class: "muted fs-12" }, maskCredentials(srv.command + " " + (srv.args || []).join(" ")))
            ),
            el(
              "span",
              { class: "pill pill-" + (status === "running" ? "ok" : "warn") },
              status
            )
          ),
          el(
            "div",
            { class: "mcp-card-body" },
            el(
              "span",
              { class: "muted fs-12" },
              `${toolCount} tool${toolCount === 1 ? "" : "s"}`
            ),
            ...(live?.tools || []).slice(0, 6).map((t) =>
              el("span", { class: "mcp-tool-chip" }, t)
            ),
            (live?.tools || []).length > 6
              ? el("span", { class: "muted fs-12" }, ` +${live.tools.length - 6} more`)
              : null
          ),
          el(
            "div",
            { class: "mcp-card-actions" },
            el(
              "button",
              {
                class: "btn btn-sm",
                onclick: async () => {
                  try {
                    await api(`/v1/mcp/${encodeURIComponent(srv.name)}/restart`, {
                      method: "POST",
                    });
                    toastShow(`${srv.name}: restart triggered`, "ok");
                    setTimeout(renderSkills, 800);
                  } catch (e) {
                    toastShow(`Restart failed: ${e.message}`, "error");
                  }
                },
              },
              status === "running" ? "Restart" : "Start"
            ),
            status === "running"
              ? el(
                  "button",
                  {
                    class: "btn btn-sm btn-ghost",
                    onclick: async () => {
                      try {
                        await api(`/v1/mcp/${encodeURIComponent(srv.name)}/stop`, {
                          method: "POST",
                        });
                        toastShow(`${srv.name}: stopped`, "ok");
                        setTimeout(renderSkills, 400);
                      } catch (e) {
                        toastShow(`Stop failed: ${e.message}`, "error");
                      }
                    },
                  },
                  "Stop"
                )
              : null,
            live?.last_error
              ? el("span", { class: "muted fs-12" }, live.last_error)
              : null
          )
        );
        mcpGrid.appendChild(card);
      }
    }

    // ---------- Installed skills section (marketplace)
    const installedSection = el("section", { class: "skills-section" });
    installedSection.appendChild(
      el(
        "div",
        { class: "section-heading" },
        el("h3", {}, "Installed skills"),
        el("span", { class: "muted fs-12" }, "skills/ directory in this repo")
      )
    );
    const localData = await api("/v1/skills/local").catch(() => ({ skills: [] }));
    if (!localData.skills?.length) {
      installedSection.appendChild(
        el(
          "div",
          { class: "empty" },
          el("i", { "data-lucide": "sparkles", class: "empty-icon" }),
          el("div", { class: "empty-title" }, "No skills installed"),
          el("div", { class: "empty-sub" }, "Browse the catalog below and click Install")
        )
      );
    } else {
      const grid = el("div", { class: "grid-cards" });
      for (const s of localData.skills) {
        const card = el(
          "article",
          { class: "card" },
          el("div", { class: "card-title" }, s.name),
          el("div", { class: "card-sub" }, s.description || "no description"),
          el("div", { class: "muted truncate", style: { fontSize: "11px" } }, s.entry),
          el(
            "div",
            { class: "mcp-card-actions" },
            el(
              "button",
              {
                class: "btn btn-sm btn-ghost",
                onclick: async () => {
                  if (!confirm(`Uninstall skill "${s.slug}"? This removes the skills/${s.slug} folder.`)) return;
                  try {
                    await api(`/v1/skills/local/${encodeURIComponent(s.slug)}`, { method: "DELETE" });
                    toastShow(`Skill "${s.slug}" uninstalled`, "ok");
                    setTimeout(renderSkills, 400);
                  } catch (e) {
                    toastShow(`Uninstall failed: ${e.message}`, "error");
                  }
                },
              },
              "Uninstall"
            )
          )
        );
        grid.appendChild(card);
      }
      installedSection.appendChild(grid);
    }
    body.appendChild(installedSection);

    // ---------- Legacy skills section (from ~/.claude)
    const legacySection = el("section", { class: "skills-section" });
    legacySection.appendChild(
      el(
        "div",
        { class: "section-heading" },
        el("h3", {}, "Claude skills (legacy)"),
        el("span", { class: "muted fs-12" }, "SKILL.md files in your Claude install")
      )
    );
    const legacyData = await api("/v1/skills").catch(() => ({ skills: [], search_paths: [] }));
    if (!legacyData.skills?.length) {
      legacySection.appendChild(
        el(
          "div",
          { class: "empty" },
          el("i", { "data-lucide": "package-search", class: "empty-icon" }),
          el("div", { class: "empty-title" }, "No legacy skills found"),
          el(
            "div",
            { class: "empty-sub" },
            "Searched: " + (legacyData.search_paths || []).join(", ")
          )
        )
      );
    } else {
      const grid = el("div", { class: "grid-cards" });
      for (const s of legacyData.skills) {
        grid.appendChild(
          el(
            "div",
            { class: "card" },
            el("div", { class: "card-title" }, s.name),
            el("div", { class: "card-sub" }, s.description || "no description"),
            el("div", { class: "muted truncate", style: { fontSize: "11px" } }, s.path)
          )
        );
      }
      legacySection.appendChild(grid);
    }
    body.appendChild(legacySection);

    // ---------- Remote catalog section
    const catalogSection = el("section", { class: "skills-section" });
    catalogSection.appendChild(
      el(
        "div",
        { class: "section-heading" },
        el("h3", {}, "Skill catalog"),
        el("span", { class: "muted fs-12" }, "Set SKILLS_CATALOG_URL to enable remote browsing")
      )
    );
    const catalogData = await api("/v1/skills/catalog").catch(() => ({ skills: [], source: null }));
    if (!catalogData.skills?.length) {
      catalogSection.appendChild(
        el(
          "div",
          { class: "empty" },
          el("i", { "data-lucide": "globe", class: "empty-icon" }),
          el("div", { class: "empty-title" }, "No catalog available"),
          el(
            "div",
            { class: "empty-sub" },
            catalogData.source
              ? "Catalog at " + catalogData.source + " returned no skills"
              : "Configure SKILLS_CATALOG_URL to browse installable skills"
          )
        )
      );
    } else {
      const installedSlugs = new Set((localData.skills || []).map((s) => s.slug));
      const grid = el("div", { class: "grid-cards" });
      for (const s of catalogData.skills) {
        const alreadyInstalled = installedSlugs.has(s.slug);
        const installBtn = el(
          "button",
          {
            class: "btn btn-sm" + (alreadyInstalled ? " btn-ghost" : ""),
            disabled: alreadyInstalled || undefined,
            onclick: async () => {
              if (
                !confirm(
                  `Install "${s.name || s.slug}"?\n\nThis will download and execute code from:\n${s.tarball_url || s.url || catalogData.source}\n\nContinue?`
                )
              )
                return;
              installBtn.disabled = true;
              installBtn.textContent = "Installing…";
              try {
                await api(`/v1/skills/install/${encodeURIComponent(s.slug)}`, { method: "POST" });
                toastShow(`Skill "${s.slug}" installed`, "ok");
                setTimeout(renderSkills, 600);
              } catch (e) {
                toastShow(`Install failed: ${e.message}`, "error");
                installBtn.disabled = false;
                installBtn.textContent = "Install";
              }
            },
          },
          alreadyInstalled ? "Installed" : "Install"
        );
        const card = el(
          "article",
          { class: "card" },
          el("div", { class: "card-title" }, s.name || s.slug),
          el("div", { class: "card-sub" }, s.description || ""),
          el("div", { class: "mcp-card-actions" }, installBtn)
        );
        grid.appendChild(card);
      }
      catalogSection.appendChild(grid);
    }
    body.appendChild(catalogSection);
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
            el("i", { "data-lucide": "wand-2", style: { marginRight: "6px" } }), "Setup wizard"
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

    // Provider test-connection cards
    try {
      const upstreamData = await api("/v1/models/upstream");
      const providerIds = (upstreamData.providers || []).map((p) => p.provider);
      // Also include providers from configured refs that may not have cached models yet.
      const configuredProviderIds = (upstreamData.configured || []).map((c) => c.provider);
      const allProviderIds = [...new Set([...providerIds, ...configuredProviderIds])];
      if (allProviderIds.length > 0) {
        const providerGrid = el("div", { class: "grid-cards" });
        allProviderIds.sort().forEach((pid) => {
          const card = el("div", { class: "card" });
          card.appendChild(el("div", { class: "card-title" }, pid));

          // Status pill element — shared between button and pill views.
          const statusEl = el("span", { class: "pill", style: { display: "none" } });

          const testBtn = el(
            "button",
            {
              class: "btn btn-ghost btn-sm",
              type: "button",
              onclick: async () => {
                testBtn.disabled = true;
                testBtn.textContent = "Testing…";
                statusEl.style.display = "none";
                try {
                  const result = await api(`/v1/providers/${encodeURIComponent(pid)}/test`, {
                    method: "POST",
                  });
                  if (result.ok) {
                    statusEl.className = "pill pill-ok";
                    statusEl.textContent = `✓ ${result.duration_ms} ms`;
                  } else {
                    const errText = (result.error || "failed").slice(0, 80);
                    statusEl.className = "pill pill-error";
                    statusEl.textContent = `✗ ${errText}`;
                  }
                } catch (e) {
                  statusEl.className = "pill pill-error";
                  statusEl.textContent = `✗ ${String(e.message || e).slice(0, 80)}`;
                }
                testBtn.textContent = "Test connection";
                testBtn.disabled = false;
                statusEl.style.display = "";
                // Auto-fade the status pill after 8 s.
                setTimeout(() => {
                  statusEl.style.display = "none";
                }, 8000);
              },
            },
            "Test connection"
          );

          card.appendChild(
            el("div", { class: "row gap-2 align-center", style: { marginTop: "8px" } }, testBtn, statusEl)
          );
          providerGrid.appendChild(card);
        });
        body.appendChild(el("div", { class: "card-title", style: { marginTop: "16px" } }, "Provider connections"));
        body.appendChild(providerGrid);
      }
    } catch (e) {
      console.warn("provider list load failed", e);
    }

    // Preferences card
    try {
      const prefsData = await api("/v1/preferences");
      const prefsCard = el("div", { class: "card" });
      prefsCard.appendChild(el("div", { class: "card-title" }, "Long-term preferences"));
      const prefsList = el("div");
      prefsCard.appendChild(prefsList);

      function renderPrefsList(entries) {
        prefsList.innerHTML = "";
        if (!entries || entries.length === 0) {
          prefsList.appendChild(
            el("div", { class: "muted", style: { fontSize: "0.85rem", marginTop: "8px" } },
              "No preferences recorded — the agent will write here when you say " +
              "\"always…\" or \"never…\" in chat.")
          );
          return;
        }
        entries.forEach((pref) => {
          const row = el("div", { class: "row gap-2 align-center", style: { marginTop: "6px" } });
          row.appendChild(
            el("span", { class: "mono", style: { flex: "1", fontSize: "0.85rem" } },
              `${pref.key}: ${pref.value}`)
          );
          const delBtn = el("button", {
            class: "btn btn-ghost btn-sm",
            type: "button",
            onclick: async () => {
              if (!confirm(`Delete preference "${pref.key}"?`)) return;
              try {
                await api(`/v1/preferences/${encodeURIComponent(pref.key)}`, { method: "DELETE" });
                const updated = await api("/v1/preferences");
                renderPrefsList(updated.preferences || []);
              } catch (e) {
                alert("Delete failed: " + (e.message || e));
              }
            },
          }, "Delete");
          row.appendChild(delBtn);
          prefsList.appendChild(row);
        });
      }

      renderPrefsList(prefsData.preferences || []);
      body.appendChild(prefsCard);
    } catch (e) {
      console.warn("preferences load failed", e);
    }

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
                  el("i", { "data-lucide": "pencil" })
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
      tag.textContent = r.ok ? "online" : "degraded";
      tag.style.color = r.ok ? "var(--success)" : "var(--warning)";
    } catch {
      tag.textContent = "offline";
      tag.style.color = "var(--error)";
    }
  }

  // ============================================================ File preview panel

  // Per-session LRU registry tracking recent file accesses (max 20 entries).
  // Each entry: { path, kind: "read"|"write" }
  const _previewRegistry = {};

  function _previewLruKey(sessionId) {
    return `fcc:preview:lru:${sessionId}`;
  }

  function _previewLoadLru(sessionId) {
    if (_previewRegistry[sessionId]) return _previewRegistry[sessionId];
    try {
      const raw = localStorage.getItem(_previewLruKey(sessionId));
      _previewRegistry[sessionId] = raw ? JSON.parse(raw) : [];
    } catch {
      _previewRegistry[sessionId] = [];
    }
    return _previewRegistry[sessionId];
  }

  function _previewPush(sessionId, path, kind) {
    if (!sessionId || !path) return;
    const lru = _previewLoadLru(sessionId);
    // Remove existing entry for same path, then prepend.
    const idx = lru.findIndex((e) => e.path === path);
    if (idx !== -1) lru.splice(idx, 1);
    lru.unshift({ path, kind });
    if (lru.length > 20) lru.length = 20;
    try {
      localStorage.setItem(_previewLruKey(sessionId), JSON.stringify(lru));
    } catch {
      /* storage full — ignore */
    }
  }

  // Lazy Prism loader — only loads scripts on first preview open.
  let _prismLoaded = false;
  let _prismLoadPromise = null;

  function _loadPrism() {
    if (_prismLoaded) return Promise.resolve();
    if (_prismLoadPromise) return _prismLoadPromise;
    const base = "vendor/prism/";
    const scripts = [
      base + "prism.js",
      base + "prism-python.min.js",
      base + "prism-javascript.min.js",
      base + "prism-typescript.min.js",
      base + "prism-json.min.js",
      base + "prism-yaml.min.js",
      base + "prism-markup.min.js",
      base + "prism-css.min.js",
      base + "prism-sql.min.js",
      base + "prism-rust.min.js",
      base + "prism-go.min.js",
      base + "prism-bash.min.js",
      base + "prism-markdown.min.js",
      base + "prism-line-numbers.min.js",
    ];
    _prismLoadPromise = scripts
      .reduce((chain, src) => {
        return chain.then(
          () =>
            new Promise((resolve, reject) => {
              if (document.querySelector(`script[src="${src}"]`)) {
                resolve();
                return;
              }
              const s = document.createElement("script");
              s.src = src;
              s.onload = resolve;
              s.onerror = resolve; // non-fatal — highlighting will degrade
              document.head.appendChild(s);
            })
        );
      }, Promise.resolve())
      .then(() => {
        _prismLoaded = true;
      });
    return _prismLoadPromise;
  }

  // Singleton panel reference — only one panel exists at a time.
  let _previewPanel = null;
  let _previewSessionId = null;
  let _previewActiveTab = "current"; // "current" | "reads" | "writes"
  let _previewCurrentPath = null;

  function _getOrCreatePanel(sessionId) {
    const chatMain = document.querySelector(".chat-main");
    if (!chatMain) return null;

    if (_previewPanel && _previewSessionId === sessionId) {
      return _previewPanel;
    }

    // Build or re-parent the panel.
    if (_previewPanel) {
      _previewPanel.remove();
      _previewPanel = null;
    }

    _previewSessionId = sessionId;

    // Wrap messages + preview in a layout container if not already done.
    let layout = chatMain.querySelector(".chat-layout");
    if (!layout) {
      const messages = chatMain.querySelector(".messages");
      if (!messages) return null;
      layout = el("div", { class: "chat-layout" });
      messages.parentNode.insertBefore(layout, messages);
      layout.appendChild(messages);
    }

    const panel = el("aside", { class: "preview" });

    // Header row: close + path + lang badge + copy button.
    const pathLabel = el("div", { class: "preview-path" }, "");
    const langBadge = el("div", { class: "preview-lang-badge" }, "");
    const copyBtn = el(
      "button",
      {
        class: "btn btn-ghost btn-sm",
        type: "button",
        title: "Copy content",
        onclick: () => {
          const pre = panel.querySelector(".preview-body code");
          const text = pre ? pre.textContent : "";
          navigator.clipboard.writeText(text).catch(() => {});
          copyBtn.textContent = "✓";
          setTimeout(() => {
            copyBtn.textContent = "copy";
          }, 1200);
        },
      },
      "copy"
    );
    const closeBtn = el(
      "button",
      {
        class: "btn btn-ghost btn-sm",
        type: "button",
        title: "Close preview",
        onclick: () => {
          panel.classList.remove("preview-open");
          setTimeout(() => panel.remove(), 160);
          _previewPanel = null;
        },
      },
      el("i", { "data-lucide": "x" })
    );
    const header = el(
      "div",
      { class: "preview-header" },
      pathLabel,
      langBadge,
      copyBtn,
      closeBtn
    );
    panel.appendChild(header);

    // Tabs.
    const tabs = el("div", { class: "preview-tabs" });
    const makeTab = (id, label) => {
      const btn = el(
        "button",
        {
          class: "preview-tab" + (_previewActiveTab === id ? " active" : ""),
          type: "button",
          "data-tab": id,
          onclick: () => {
            _previewActiveTab = id;
            $$(".preview-tab", panel).forEach((t) =>
              t.classList.toggle("active", t.dataset.tab === id)
            );
            _renderPreviewTab(panel, sessionId, id, pathLabel, langBadge);
          },
        },
        label
      );
      return btn;
    };
    tabs.appendChild(makeTab("current", "Current file"));
    tabs.appendChild(makeTab("reads", "Recent reads"));
    tabs.appendChild(makeTab("writes", "Recent writes"));
    panel.appendChild(tabs);

    const body = el("div", { class: "preview-body" });
    panel.appendChild(body);

    layout.appendChild(panel);
    _previewPanel = panel;

    // Slide in.
    requestAnimationFrame(() => panel.classList.add("preview-open"));

    return panel;
  }

  function _renderPreviewTab(panel, sessionId, tabId, pathLabel, langBadge) {
    const body = panel.querySelector(".preview-body");
    if (!body) return;

    if (tabId === "current") {
      if (_previewCurrentPath) {
        _loadAndShowFile(panel, sessionId, _previewCurrentPath, pathLabel, langBadge);
      } else {
        body.innerHTML = "";
        body.appendChild(el("div", { class: "preview-empty" }, "No file selected"));
      }
      return;
    }

    const lru = _previewLoadLru(sessionId);
    const kind = tabId === "reads" ? "read" : "write";
    const filtered = lru.filter((e) => e.kind === kind);

    body.innerHTML = "";
    if (filtered.length === 0) {
      body.appendChild(
        el("div", { class: "preview-empty" }, `No recent ${kind}s`)
      );
      return;
    }

    const list = el("ul", { class: "preview-list" });
    filtered.forEach((entry) => {
      const item = el(
        "li",
        {
          class: "preview-list-item",
          onclick: () => {
            _previewCurrentPath = entry.path;
            _previewActiveTab = "current";
            $$(".preview-tab", panel).forEach((t) =>
              t.classList.toggle("active", t.dataset.tab === "current")
            );
            _loadAndShowFile(panel, sessionId, entry.path, pathLabel, langBadge);
          },
        },
        el("span", { class: "preview-item-kind" }, entry.kind === "read" ? "R" : "W"),
        el("span", {}, entry.path.split("/").pop()),
        el(
          "span",
          { class: "muted", style: "font-size:10px;margin-left:auto;" },
          entry.path
        )
      );
      list.appendChild(item);
    });
    body.appendChild(list);
  }

  async function _loadAndShowFile(panel, sessionId, path, pathLabel, langBadge) {
    const body = panel.querySelector(".preview-body");
    if (!body) return;

    body.innerHTML = "";
    body.appendChild(el("div", { class: "preview-loading" }, "Loading…"));

    try {
      await _loadPrism();
    } catch {
      /* non-fatal */
    }

    let data;
    try {
      data = await api(
        `/v1/preview/file?path=${encodeURIComponent(path)}&session_id=${encodeURIComponent(sessionId || "")}`
      );
    } catch (err) {
      body.innerHTML = "";
      body.appendChild(
        el(
          "div",
          { class: "preview-empty" },
          `Cannot load file: ${err.message || err}`
        )
      );
      return;
    }

    const lang = data.language || "plaintext";
    pathLabel.textContent = path;
    pathLabel.title = path;
    langBadge.textContent = lang;
    langBadge.style.display = lang === "plaintext" ? "none" : "";

    body.innerHTML = "";
    const pre = el("pre", { class: `language-${lang} line-numbers` });
    const code = el("code", { class: `language-${lang}` });
    code.textContent = data.content || "";
    pre.appendChild(code);
    body.appendChild(pre);

    // Highlight via Prism if available.
    if (
      typeof window !== "undefined" &&
      window.Prism &&
      typeof window.Prism.highlightElement === "function"
    ) {
      try {
        window.Prism.highlightElement(code);
        if (typeof window.Prism.plugins?.lineNumbers?.resize === "function") {
          window.Prism.plugins.lineNumbers.resize(pre);
        }
      } catch {
        /* non-fatal */
      }
    }
  }

  /**
   * Open the preview panel for a given file path + session.
   * Called by tool-block click handlers and the public surface.
   */
  function openPreviewPanel(path, sessionId) {
    if (!path) return;
    const panel = _getOrCreatePanel(sessionId || activeSessionId || "");
    if (!panel) return;

    _previewCurrentPath = path;
    _previewActiveTab = "current";
    $$(".preview-tab", panel).forEach((t) =>
      t.classList.toggle("active", t.dataset.tab === "current")
    );

    const pathLabel = panel.querySelector(".preview-path");
    const langBadge = panel.querySelector(".preview-lang-badge");
    _loadAndShowFile(
      panel,
      sessionId || activeSessionId || "",
      path,
      pathLabel,
      langBadge
    );
  }

  // ============================================================ Insights page
  async function renderInsights(range = "7d") {
    const view = $("#view");
    view.innerHTML = "";

    const RANGES = ["1h", "24h", "7d", "30d", "all"];
    const fmtUsd = (n) =>
      new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 4,
      }).format(n);
    const fmtInt = (n) =>
      new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);

    view.appendChild(
      pageHeader({
        title: "Insights",
        sub: "Rule-based reflection over the last window",
        actions: [
          el(
            "div",
            { class: "range-tabs" },
            ...RANGES.map((r) =>
              el(
                "button",
                {
                  class: "range-tab" + (r === range ? " active" : ""),
                  onclick: () => renderInsights(r),
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

    body.appendChild(el("div", { class: "empty" }, "Loading…"));

    let data;
    try {
      data = await api(`/v1/insights?range=${encodeURIComponent(range)}`);
    } catch (err) {
      body.innerHTML = "";
      body.appendChild(el("div", { class: "empty" }, `Failed to load insights: ${err.message}`));
      return;
    }

    body.innerHTML = "";
    const grid = el("div", { class: "insights-grid" });
    body.appendChild(grid);

    // ---- Tools card ----
    const toolsCard = el("div", { class: "insights-card" });
    toolsCard.appendChild(el("h2", { class: "card-title" }, "Tool calls"));
    if (!data.tools || data.tools.length === 0) {
      toolsCard.appendChild(
        el("p", { class: "muted" }, "No tool-call data for this window.")
      );
    } else {
      const tbl = el("table", { class: "insights-table" });
      tbl.appendChild(
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            el("th", {}, "Tool"),
            el("th", { class: "num" }, "Calls"),
            el("th", { class: "num" }, "Avg ms"),
            el("th", { class: "num" }, "Errors")
          )
        )
      );
      const tbody = el("tbody", {});
      (data.tools || []).forEach((t) => {
        const errRate = t.calls > 0 ? t.errors / t.calls : 0;
        const errClass = errRate > 0.1 ? "cell-warn" : "";
        tbody.appendChild(
          el(
            "tr",
            {},
            el("td", {}, t.name),
            el("td", { class: "num" }, fmtInt(t.calls)),
            el("td", { class: "num" }, fmtInt(t.avg_ms)),
            el("td", { class: `num ${errClass}` }, fmtInt(t.errors))
          )
        );
      });
      tbl.appendChild(tbody);
      toolsCard.appendChild(tbl);
    }
    grid.appendChild(toolsCard);

    // ---- Providers card ----
    const provCard = el("div", { class: "insights-card" });
    provCard.appendChild(el("h2", { class: "card-title" }, "Provider cost"));
    if (!data.providers || data.providers.length === 0) {
      provCard.appendChild(el("p", { class: "muted" }, "No usage data for this window."));
    } else {
      const tbl = el("table", { class: "insights-table" });
      tbl.appendChild(
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            el("th", {}, "Provider"),
            el("th", { class: "num" }, "Cost"),
            el("th", { class: "num" }, "Tokens")
          )
        )
      );
      const tbody = el("tbody", {});
      (data.providers || []).forEach((p) => {
        tbody.appendChild(
          el(
            "tr",
            {},
            el("td", {}, p.provider),
            el("td", { class: "num" }, fmtUsd(p.cost_usd)),
            el("td", { class: "num" }, fmtInt(p.tokens))
          )
        );
      });
      tbl.appendChild(tbody);
      provCard.appendChild(tbl);
    }
    grid.appendChild(provCard);

    // ---- Suggestions card ----
    const suggCard = el("div", { class: "insights-card" });
    suggCard.appendChild(el("h2", { class: "card-title" }, "Suggestions"));
    if (!data.suggestions || data.suggestions.length === 0) {
      suggCard.appendChild(
        el("p", { class: "muted" }, "No actionable suggestions for this window.")
      );
    } else {
      (data.suggestions || []).forEach((s) => {
        const card = el("div", { class: "suggestion-item" });
        card.appendChild(el("div", { class: "suggestion-title" }, s.title));
        card.appendChild(el("div", { class: "suggestion-body" }, s.body));

        if (s.action && s.action.type === "env_set") {
          const applyBtn = el(
            "button",
            {
              class: "btn-apply",
              onclick: async () => {
                applyBtn.disabled = true;
                applyBtn.textContent = "Applying…";
                try {
                  const key = s.action.key;
                  const envData = await api("/v1/env");
                  const existing = (envData.entries || []).find((e) => e.key === key);
                  const current = existing ? existing.value : "";
                  const newVal = current ? `${current},${s.action.hint}` : s.action.hint;
                  await api("/v1/env", {
                    method: "PUT",
                    body: JSON.stringify({ key, value: newVal }),
                  });
                  applyBtn.textContent = "Applied";
                  applyBtn.classList.add("applied");
                } catch (applyErr) {
                  applyBtn.disabled = false;
                  applyBtn.textContent = "Retry";
                  card.appendChild(
                    el("div", { class: "error-banner" }, `Error: ${applyErr.message}`)
                  );
                }
              },
            },
            "Apply"
          );
          card.appendChild(applyBtn);
        }
        suggCard.appendChild(card);
      });
    }
    grid.appendChild(suggCard);
  }

  // ============================================================ Memory / RAG index page
  async function renderMemory() {
    const view = $("#view");
    view.innerHTML = "";

    let status = null;
    let rescanInFlight = false;

    const statusEl = el("div", { class: "memory-status" }, "Loading…");
    const rescanBtn = el(
      "button",
      {
        class: "btn primary",
        onclick: async () => {
          if (rescanInFlight) return;
          rescanBtn.disabled = true;
          rescanBtn.textContent = "Rescanning…";
          try {
            await api("/v1/index/rescan", { method: "POST" });
          } catch (e) {
            /* ignore */
          }
          rescanBtn.textContent = "Rescan now";
          rescanBtn.disabled = false;
        },
      },
      "Rescan now"
    );

    function renderStatus(s) {
      if (!s) return;
      rescanInFlight = s.rescan_in_flight || false;
      rescanBtn.disabled = rescanInFlight;
      rescanBtn.textContent = rescanInFlight ? "Scanning…" : "Rescan now";

      const lastScan = s.last_scan_ms ? _relativeTime(s.last_scan_ms) : "never";
      const lastScanAbs = s.last_scan_ms
        ? new Date(s.last_scan_ms).toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "medium",
          })
        : "never";
      statusEl.innerHTML = "";
      statusEl.appendChild(
        el(
          "div",
          { class: "memory-grid" },
          el(
            "div",
            { class: "stat-card" },
            el("i", { "data-lucide": "file-text" }),
            el("div", { class: "stat-body" },
              el("div", { class: "stat-value" }, String(s.file_count ?? 0)),
              el("div", { class: "stat-label" }, "files indexed")
            )
          ),
          el(
            "div",
            { class: "stat-card" },
            el("i", { "data-lucide": "grid-3x3" }),
            el("div", { class: "stat-body" },
              el("div", { class: "stat-value" }, String(s.total_chunks ?? 0)),
              el("div", { class: "stat-label" }, "total chunks")
            )
          ),
          el(
            "div",
            { class: "stat-card" },
            el("i", { "data-lucide": "database" }),
            el("div", { class: "stat-body" },
              el("div", { class: "stat-value" },
                s.index_bytes
                  ? (s.index_bytes / 1024 / 1024).toFixed(1) + " MB"
                  : "0 MB"
              ),
              el("div", { class: "stat-label" }, "indexed bytes")
            )
          ),
          el(
            "div",
            { class: "stat-card", },
            el("i", { "data-lucide": "clock" }),
            el("div", { class: "stat-body" },
              el("div", { class: "stat-value", title: lastScanAbs }, lastScan),
              el("div", { class: "stat-label" }, "last scan")
            )
          )
        )
      );
      lucide.createIcons();
    }

    async function refresh() {
      try {
        status = await api("/v1/index/status");
        renderStatus(status);
      } catch (e) {
        statusEl.textContent = "Indexer not running — set MEMORY_INDEX_PATHS";
      }
    }

    const body = el("div", { class: "page-body" }, statusEl);
    view.appendChild(
      el(
        "div",
        { class: "page" },
        pageHeader({
          title: "Memory index",
          sub: "RAG over files, notes, and chat turns",
          actions: [rescanBtn],
        }),
        body
      )
    );

    await refresh();
    const interval = setInterval(refresh, 10_000);
    // stop polling when user navigates away
    const observer = new MutationObserver(() => {
      if (!view.contains(body)) {
        clearInterval(interval);
        observer.disconnect();
      }
    });
    observer.observe(view, { childList: true });
  }

  // ============================================================ Routines page

  const CRON_PRESETS = [
    { label: "Every 5 min", expr: "*/5 * * * *" },
    { label: "Hourly", expr: "0 * * * *" },
    { label: "Every morning at 9", expr: "0 9 * * *" },
    { label: "Every Monday", expr: "0 9 * * 1" },
    { label: "Daily midnight", expr: "0 0 * * *" },
  ];

  async function renderRoutines() {
    const view = $("#view");
    view.innerHTML = "";

    // Load upstream models for the drawer model-picker.
    const upstreamModels = await api("/v1/models/upstream").catch(() => ({
      providers: [],
    }));
    const modelOptions = [];
    for (const p of upstreamModels.providers || []) {
      for (const m of p.models || []) {
        modelOptions.push(`${p.provider}/${m}`);
      }
    }
    const defaultModel =
      modelOptions[0] || "deepseek/deepseek-v4-flash";

    let routines = [];
    let drawerOpen = false;
    let editTarget = null; // routine id being edited, or null for create

    // ---- drawer state
    let dName = "";
    let dDesc = "";
    let dTriggerType = "cron";
    let dCronExpr = "0 9 * * *";
    let dCronTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    let dModel = defaultModel;
    let dPayloadText = JSON.stringify({ model: defaultModel, messages: [] }, null, 2);
    let dEnabled = true;
    let dProjectId = null;
    let dPayloadValid = true;

    // ---- output viewer state (shown below history panel)
    let outputVisible = false;
    let outputJobId = null;
    const outputSection = el("div", { class: "job-output-panel", style: "display:none" });

    // ---- history panel state (shown below table)
    let historyRoutineId = null;
    let historyRuns = [];
    let historyTotal = 0;
    let historyOffset = 0;
    const HIST_LIMIT = 25;

    // ---- main refs
    const tbody = el("tbody");
    const histSection = el("div", { class: "routines-history", style: "display:none" });
    const drawerEl = el("div", { class: "drawer", style: "display:none" });
    const drawerOverlay = el("div", {
      class: "drawer-overlay",
      style: "display:none",
      onclick: closeDrawer,
    });

    function fmtNextFire(ms) {
      if (!ms) return "—";
      const diff = ms - Date.now();
      if (diff <= 0) return "overdue";
      const m = Math.floor(diff / 60000);
      const h = Math.floor(m / 60);
      const d = Math.floor(h / 24);
      if (d > 0) return `in ${d}d ${h % 24}h`;
      if (h > 0) return `in ${h}h ${m % 60}m`;
      return `in ${m}m`;
    }

    function fmtStatus(status) {
      const map = {
        pending: "pill-warn",
        running: "pill-accent",
        done: "pill-ok",
        error: "badge error",
        cancelled: "",
      };
      const cls = map[status] || "";
      return el("span", { class: cls || "muted" }, status || "—");
    }

    async function loadRoutines() {
      try {
        const data = await api("/v1/routines");
        routines = data.routines || [];
        renderTable();
      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" class="muted">Failed to load: ${e.message}</td></tr>`;
      }
    }

    function renderTable() {
      tbody.innerHTML = "";
      if (!routines.length) {
        tbody.appendChild(
          el(
            "tr",
            {},
            el(
              "td",
              { colspan: "6", class: "muted", style: "padding:24px;text-align:center" },
              "No routines yet. Click + New Routine to create one."
            )
          )
        );
        return;
      }
      routines.forEach((r) => {
        let triggerSummary = r.trigger_type || "—";
        try {
          const tc = JSON.parse(r.trigger_config || "{}");
          if (r.trigger_type === "cron" && tc.expression) triggerSummary = tc.expression;
          else if (r.trigger_type === "webhook") triggerSummary = "webhook";
          else if (r.trigger_type === "manual") triggerSummary = "manual";
        } catch (_) {}

        const nextChip = el(
          "span",
          { class: r.next_run_ms ? "pill pill-accent" : "muted" },
          fmtNextFire(r.next_run_ms)
        );

        const enabledBadge = el(
          "span",
          { class: r.enabled ? "badge success" : "badge" },
          r.enabled ? "enabled" : "disabled"
        );

        const runBtn = el(
          "button",
          {
            class: "btn btn-sm",
            title: "Run now",
            onclick: async (e) => {
              e.stopPropagation();
              runBtn.disabled = true;
              runBtn.textContent = "Run now";
              try {
                const res = await api(`/v1/routines/${r.id}/run`, { method: "POST" });
                toastShow("Routine fired — job " + (res.job?.id || ""), "ok");
                await loadRoutines();
                if (res.job?.id) {
                  if (historyRoutineId !== r.id) {
                    historyRoutineId = r.id;
                    historyOffset = 0;
                    histSection.style.display = "";
                  }
                  await loadHistory();
                  viewJobOutput(res.job.id);
                }
              } catch (err) {
                toastShow(`Run failed: ${err.message}`, "error");
                runBtn.disabled = false;
              }
            },
          },
          el("i", { "data-lucide": "play" })
        );

        // Kebab menu — compact actions dropdown
        const kebabBtn = el("button", {
          class: "icon-btn",
          title: "More actions",
          onclick: (e) => {
            e.stopPropagation();
            kebabMenu.style.display = kebabMenu.style.display === "none" ? "" : "none";
          },
        }, el("i", { "data-lucide": "ellipsis-vertical" }));

        const kebabMenu = el("div", {
          class: "kebab-menu",
          style: "display:none",
          onclick: (e) => e.stopPropagation(),
        },
          el("button", {
            class: "kebab-item",
            onclick: (e) => { e.stopPropagation(); toggleHistory(r.id); kebabMenu.style.display = "none"; },
          }, "History"),
          el("button", {
            class: "kebab-item",
            onclick: (e) => { e.stopPropagation(); openEditDrawer(r); kebabMenu.style.display = "none"; },
          }, "Edit"),
          el("button", {
            class: "kebab-item kebab-item-danger",
            onclick: async (e) => {
              e.stopPropagation();
              if (!confirm(`Delete routine "${r.name}"?`)) return;
              try {
                await api(`/v1/routines/${r.id}`, { method: "DELETE" });
                toastShow("Routine deleted", "ok");
                if (historyRoutineId === r.id) closeHistory();
                kebabMenu.style.display = "none";
                await loadRoutines();
              } catch (err) {
                toastShow(`Delete failed: ${err.message}`, "error");
              }
            },
          }, "Delete"),
        );

        // Close kebab when clicking outside
        document.addEventListener("click", function _closeKebab(e) {
          if (!kebabMenu.parentNode) {
            document.removeEventListener("click", _closeKebab);
            return;
          }
          if (!kebabMenu.contains(e.target) && e.target !== kebabBtn) {
            kebabMenu.style.display = "none";
          }
        });

        const actionsCell = el("td", {},
          el("div", { class: "row gap-1", style: "position:relative" },
            runBtn, kebabBtn, kebabMenu));

        const row = el(
          "tr",
          {},
          el("td", {}, el("strong", { class: "fs-13" }, r.name)),
          el("td", { class: "mono" }, triggerSummary),
          el("td", {}, nextChip),
          el("td", {}, enabledBadge),
          el(
            "td",
            { class: "muted", style: "min-width:90px" },
            r.last_run_ms ? fmtTime(r.last_run_ms) : "never"
          ),
          actionsCell
        );
        tbody.appendChild(row);
      });
    }

    // ---- history sub-panel
    async function toggleHistory(routineId) {
      if (historyRoutineId === routineId) {
        closeHistory();
        return;
      }
      historyRoutineId = routineId;
      historyOffset = 0;
      histSection.style.display = "";
      await loadHistory();
    }

    function closeHistory() {
      historyRoutineId = null;
      histSection.style.display = "none";
      histSection.innerHTML = "";
    }

    async function loadHistory() {
      if (!historyRoutineId) return;
      histSection.innerHTML = "Loading history…";
      try {
        const data = await api(
          `/v1/routines/${historyRoutineId}/runs?limit=${HIST_LIMIT}&offset=${historyOffset}`
        );
        historyRuns = data.runs || [];
        historyTotal = data.total || 0;
        renderHistory();
      } catch (e) {
        histSection.innerHTML = `<span class="muted">Failed: ${e.message}</span>`;
      }
    }

    function renderHistory() {
      histSection.innerHTML = "";
      const rtn = routines.find((r) => r.id === historyRoutineId);
      histSection.appendChild(
        el(
          "div",
          { class: "section-heading", style: "margin-top:16px" },
          el("h3", { class: "fs-13" }, `Run history — ${rtn ? rtn.name : historyRoutineId}`),
          el(
            "button",
            { class: "btn btn-sm ghost", onclick: closeHistory },
            "Close"
          )
        )
      );
      if (!historyRuns.length) {
        histSection.appendChild(el("p", { class: "muted" }, "No runs yet."));
        return;
      }
      const htable = el(
        "table",
        { class: "table" },
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            el("th", {}, "Run ID"),
            el("th", {}, "Status"),
            el("th", {}, "Job"),
            el("th", {}, "Started"),
            el("th", {}, "Finished")
          )
        ),
        el("tbody", {}, ...historyRuns.map((run) =>
          el(
            "tr",
            {},
            el("td", { class: "mono" }, run.id),
            el("td", {}, fmtStatus(run.status)),
            el(
              "td",
              { class: "mono" },
              run.job_id
                ? el(
                    "a",
                    {
                      href: "#",
                      onclick: (e) => {
                        e.preventDefault();
                        viewJobOutput(run.job_id);
                      },
                    },
                    run.job_id
                  )
                : "—"
            ),
            el("td", {}, fmtTime(run.started_at)),
            el("td", {}, run.finished_at ? fmtTime(run.finished_at) : "—")
          )
        ))
      );
      histSection.appendChild(htable);

      // pagination
      const totalPages = Math.ceil(historyTotal / HIST_LIMIT);
      const page = Math.floor(historyOffset / HIST_LIMIT) + 1;
      if (totalPages > 1) {
        histSection.appendChild(
          el(
            "div",
            { class: "row gap-2", style: "margin-top:8px" },
            el(
              "button",
              {
                class: "btn btn-sm ghost",
                disabled: historyOffset === 0,
                onclick: async () => {
                  historyOffset = Math.max(0, historyOffset - HIST_LIMIT);
                  await loadHistory();
                },
              },
              "← Prev"
            ),
            el("span", { class: "muted fs-12" }, `Page ${page} / ${totalPages}`),
            el(
              "button",
              {
                class: "btn btn-sm ghost",
                disabled: historyOffset + HIST_LIMIT >= historyTotal,
                onclick: async () => {
                  historyOffset += HIST_LIMIT;
                  await loadHistory();
                },
              },
              "Next →"
            )
          )
        );
      }
    }

    // ---- job output viewer
    async function viewJobOutput(jobId) {
      // Hide any existing output panel
      outputJobId = jobId;
      outputVisible = true;
      outputSection.style.display = "";
      outputSection.innerHTML =
        '<div class="job-output-header">' +
        '<span>Job ' + jobId + '</span>' +
        '<button class="btn btn-sm ghost" onclick="this.closest(\'.job-output-panel\').style.display=\'none\'">Close</button>' +
        '</div>' +
        '<div class="job-output-body">Loading…</div>';
      const bodyEl = outputSection.querySelector(".job-output-body");

      try {
        const res = await api(`/v1/jobs/${encodeURIComponent(jobId)}/output`);
        const status = res.status || "unknown";
        let output = "";

        // Build text from text_blocks
        if (res.text_blocks && res.text_blocks.length) {
          output += res.text_blocks.join("\n\n");
        }

        // Append tool calls
        if (res.tool_blocks && res.tool_blocks.length) {
          if (output) output += "\n\n";
          for (const tb of res.tool_blocks) {
            const name = tb.name || "?";
            let inputStr = tb.input || "";
            try {
              // Pretty-print tool input JSON if possible
              const parsed = JSON.parse(inputStr);
              inputStr = JSON.stringify(parsed, null, 2);
            } catch (_) { /* leave as-is */ }
            output += "[Tool: " + name + "]\n" + inputStr + "\n\n";
          }
        }

        if (!output.trim()) {
          output = "(no text output — job status: " + status + ")";
        }

        bodyEl.textContent = output;
      } catch (err) {
        bodyEl.textContent = "Failed to load output: " + (err.message || err);
      }
    }

    function closeJobOutput() {
      outputVisible = false;
      outputJobId = null;
      outputSection.style.display = "none";
      outputSection.innerHTML = "";
    }

    // ---- drawer: create / edit
    function resetDrawerState(r = null) {
      editTarget = r ? r.id : null;
      dName = r ? r.name : "";
      dDesc = r ? r.description || "" : "";
      dTriggerType = r ? (r.trigger_type || "cron") : "cron";
      dEnabled = r ? !!r.enabled : true;
      dProjectId = r ? (r.project_id || null) : null;
      try {
        const tc = r ? JSON.parse(r.trigger_config || "{}") : {};
        dCronExpr = tc.expression || "0 9 * * *";
        dCronTz = tc.tz || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      } catch (_) {
        dCronExpr = "0 9 * * *";
        dCronTz = "UTC";
      }
      try {
        const p = r ? JSON.parse(r.payload || "{}") : { model: "", messages: [] };
        dModel = p.model || defaultModel;
        dPayloadText = JSON.stringify(p, null, 2);
      } catch (_) {
        dModel = defaultModel;
        dPayloadText = JSON.stringify({ model: defaultModel, messages: [] }, null, 2);
      }
      dPayloadValid = true;
    }

    function openCreateDrawer() {
      resetDrawerState(null);
      renderDrawer();
      showDrawer();
    }

    function openEditDrawer(r) {
      resetDrawerState(r);
      renderDrawer();
      showDrawer();
    }

    function showDrawer() {
      drawerEl.style.display = "";
      drawerOverlay.style.display = "";
      drawerOpen = true;
    }

    function closeDrawer() {
      drawerEl.style.display = "none";
      drawerOverlay.style.display = "none";
      drawerOpen = false;
    }

    async function getCronPreview(expr, tz) {
      if (!expr) return null;
      try {
        const data = await api(
          `/v1/routines/preview-cron?expr=${encodeURIComponent(expr)}&tz=${encodeURIComponent(tz)}`
        );
        return data.fires || [];
      } catch (_) {
        return null;
      }
    }

    function renderDrawer() {
      drawerEl.innerHTML = "";
      const title = editTarget ? "Edit routine" : "New routine";

      // -- name
      const nameInput = el("input", {
        class: "form-input",
        value: dName,
        placeholder: "Routine name",
        oninput: (e) => { dName = e.target.value; },
      });

      // -- description
      const descInput = el("textarea", {
        class: "form-input",
        rows: "2",
        placeholder: "Description (optional)",
        oninput: (e) => { dDesc = e.target.value; },
      });
      descInput.value = dDesc;

      // -- trigger type tabs
      const triggerTypes = ["cron", "manual", "webhook"];
      const triggerTabs = el("div", { class: "tab-bar" });
      const cronPanel = el("div", { class: "trigger-panel" });
      const manualPanel = el("div", { class: "trigger-panel", style: "display:none" });
      const webhookPanel = el("div", { class: "trigger-panel", style: "display:none" });

      function switchTrigger(type) {
        dTriggerType = type;
        triggerTypes.forEach((t) => {
          const tab = triggerTabs.querySelector(`[data-tt="${t}"]`);
          if (tab) tab.classList.toggle("active", t === type);
        });
        cronPanel.style.display = type === "cron" ? "" : "none";
        manualPanel.style.display = type === "manual" ? "" : "none";
        webhookPanel.style.display = type === "webhook" ? "" : "none";
      }

      triggerTypes.forEach((t) => {
        const tab = el("button", {
          class: `tab-item${dTriggerType === t ? " active" : ""}`,
          dataset: { tt: t },
          onclick: () => switchTrigger(t),
        }, t);
        triggerTabs.appendChild(tab);
      });

      // -- cron panel
      const cronPreviewEl = el("div", { class: "cron-preview muted fs-12" }, "");

      async function updateCronPreview() {
        if (dTriggerType !== "cron") return;
        cronPreviewEl.textContent = "Checking…";
        const fires = await getCronPreview(dCronExpr, dCronTz);
        if (!fires) {
          cronPreviewEl.textContent = "Invalid expression";
          return;
        }
        const labels = fires.slice(0, 3).map((ms) => fmtTime(ms)).join(", ");
        cronPreviewEl.textContent = `Next: ${labels}`;
      }

      const presetSel = el("select", { class: "form-input" });
      presetSel.appendChild(el("option", { value: "" }, "— Presets —"));
      CRON_PRESETS.forEach((p) => {
        const opt = el("option", { value: p.expr }, p.label);
        presetSel.appendChild(opt);
      });
      presetSel.addEventListener("change", () => {
        if (!presetSel.value) return;
        dCronExpr = presetSel.value;
        cronInput.value = dCronExpr;
        presetSel.value = "";
        void updateCronPreview();
      });

      const cronInput = el("input", {
        class: "form-input mono",
        value: dCronExpr,
        placeholder: "cron expression",
        oninput: (e) => {
          dCronExpr = e.target.value;
          void updateCronPreview();
        },
      });
      const tzInput = el("input", {
        class: "form-input",
        value: dCronTz,
        placeholder: "IANA timezone (e.g. America/New_York)",
        oninput: (e) => { dCronTz = e.target.value; void updateCronPreview(); },
      });

      cronPanel.appendChild(el("label", { class: "form-label" }, "Presets"));
      cronPanel.appendChild(presetSel);
      cronPanel.appendChild(el("label", { class: "form-label", style: "margin-top:8px" }, "Cron expression"));
      cronPanel.appendChild(cronInput);
      cronPanel.appendChild(el("label", { class: "form-label", style: "margin-top:8px" }, "Timezone"));
      cronPanel.appendChild(tzInput);
      cronPanel.appendChild(cronPreviewEl);

      manualPanel.appendChild(el("p", { class: "muted fs-12" }, 'Triggered manually via the UI "Run now" button only.'));
      webhookPanel.appendChild(el("p", { class: "muted fs-12" }, 'Triggered via POST /v1/routines/{id}/trigger with X-Routine-Secret header.'));

      // -- model picker
      const modelSelect = el("select", { class: "form-input" });
      modelOptions.forEach((m) => {
        modelSelect.appendChild(
          el("option", { value: m, selected: m === dModel }, m)
        );
      });
      modelSelect.addEventListener("change", () => {
        dModel = modelSelect.value;
        // Sync the selected model into the payload JSON textarea.
        try {
          const p = JSON.parse(payloadInput.value || dPayloadText);
          p.model = dModel;
          payloadInput.value = JSON.stringify(p, null, 2);
          dPayloadText = payloadInput.value;
        } catch (_) {
          // If JSON is invalid, leave the textarea alone.
        }
      });

      // -- payload editor
      const payloadError = el("div", { class: "form-error", style: "display:none" }, "");
      const saveBtn = el("button", { class: "btn primary", onclick: saveRoutine }, editTarget ? "Save changes" : "Create");

      const payloadInput = el("textarea", {
        class: "form-input mono",
        rows: "8",
        placeholder: '{"model": "...", "messages": [...]}',
        onblur: () => {
          try {
            const parsed = JSON.parse(payloadInput.value);
            dPayloadText = payloadInput.value;
            // Sync model back from parsed payload
            if (parsed.model) dModel = parsed.model;
            dPayloadValid = true;
            payloadError.style.display = "none";
            saveBtn.disabled = false;
            // Update model select to match
            if (parsed.model) modelSelect.value = parsed.model;
          } catch (e) {
            dPayloadValid = false;
            payloadError.textContent = `Invalid JSON: ${e.message}`;
            payloadError.style.display = "";
            saveBtn.disabled = true;
          }
        },
      });
      payloadInput.value = dPayloadText;

      // -- enabled toggle
      const enabledChk = el("input", {
        type: "checkbox",
        id: "drawer-enabled",
        onchange: (e) => { dEnabled = e.target.checked; },
      });
      if (dEnabled) enabledChk.setAttribute("checked", "");

      async function saveRoutine() {
        // Re-validate payload on save
        try {
          JSON.parse(payloadInput.value);
          dPayloadText = payloadInput.value;
          dPayloadValid = true;
          payloadError.style.display = "none";
        } catch (e) {
          dPayloadValid = false;
          payloadError.textContent = `Invalid JSON: ${e.message}`;
          payloadError.style.display = "";
          saveBtn.disabled = true;
          return;
        }

        let payload;
        try {
          payload = JSON.parse(dPayloadText);
        } catch (_) {
          toastShow("Payload is not valid JSON", "error");
          return;
        }

        const triggerConfig = {};
        if (dTriggerType === "cron") {
          triggerConfig.expression = dCronExpr;
          triggerConfig.tz = dCronTz;
        }

        const body = {
          name: dName,
          description: dDesc,
          trigger_type: dTriggerType,
          trigger_config: triggerConfig,
          payload,
          enabled: dEnabled,
          project_id: dProjectId || null,
        };

        saveBtn.disabled = true;
        saveBtn.textContent = "Saving…";
        try {
          if (editTarget) {
            await api(`/v1/routines/${editTarget}`, {
              method: "PATCH",
              body: JSON.stringify(body),
            });
            toastShow("Routine updated", "ok");
          } else {
            await api("/v1/routines", {
              method: "POST",
              body: JSON.stringify(body),
            });
            toastShow("Routine created", "ok");
          }
          closeDrawer();
          await loadRoutines();
        } catch (err) {
          toastShow(`Save failed: ${err.message}`, "error");
          saveBtn.disabled = false;
          saveBtn.textContent = editTarget ? "Save changes" : "Create";
        }
      }

      drawerEl.appendChild(
        el(
          "div",
          { class: "drawer-inner" },
          el(
            "div",
            { class: "drawer-header" },
            el("h2", { class: "fs-15" }, title),
            el("button", { class: "ghost icon-btn", onclick: closeDrawer },
              el("i", { "data-lucide": "x" }))
          ),
          el("div", { class: "form-label" }, "Name"),
          nameInput,
          el("div", { class: "form-label", style: "margin-top:8px" }, "Description"),
          descInput,
          el("div", { class: "form-label", style: "margin-top:12px" }, "Trigger"),
          triggerTabs,
          el("div", { style: "margin-top:8px" }, cronPanel, manualPanel, webhookPanel),
          el("div", { class: "form-label", style: "margin-top:12px" }, "Model"),
          el("div", { class: "muted fs-12", style: "margin-bottom:4px" }, "Select the model that will execute this routine"),
          modelSelect,
          el("div", { class: "form-label", style: "margin-top:12px" }, "Payload (JSON)"),
          el("div", { class: "muted fs-12", style: "margin-bottom:4px" }, "Must include model and messages fields"),
          payloadInput,
          payloadError,
          el(
            "div",
            { class: "row gap-2 align-center", style: "margin-top:10px" },
            enabledChk,
            el("label", { for: "drawer-enabled", class: "fs-13" }, "Enabled")
          ),
          el(
            "div",
            { class: "row gap-2", style: "margin-top:16px" },
            saveBtn,
            el("button", { class: "btn ghost", onclick: closeDrawer }, "Cancel")
          )
        )
      );

      // kick off preview immediately if cron
      if (dTriggerType === "cron") void updateCronPreview();
    }

    // ---- template picker state
    let templates = [];
    const templateModalEl = el("div", { class: "drawer", style: "display:none" });
    const templateModalOverlay = el("div", {
      class: "drawer-overlay",
      style: "display:none",
      onclick: closeTemplateModal,
    });

    async function loadTemplates() {
      try {
        const data = await api("/v1/routines/templates");
        templates = data.templates || [];
      } catch (_) {
        templates = [];
      }
    }

    function closeTemplateModal() {
      templateModalEl.style.display = "none";
      templateModalOverlay.style.display = "none";
    }

    function openTemplateModal() {
      renderTemplateModal();
      templateModalEl.style.display = "";
      templateModalOverlay.style.display = "";
    }

    function renderTemplateModal() {
      templateModalEl.innerHTML = "";
      const grid = el("div", { class: "template-grid" });
      templates.forEach((tpl) => {
        const card = el(
          "div",
          {
            class: "template-card",
            onclick: () => { closeTemplateModal(); openFromTemplate(tpl); },
          },
          el("div", { class: "template-card-name fs-14" }, tpl.name || tpl.slug),
          el("div", { class: "template-card-desc muted fs-12" }, tpl.description || "")
        );
        grid.appendChild(card);
      });
      if (!templates.length) {
        grid.appendChild(el("p", { class: "muted fs-13" }, "No templates available."));
      }
      templateModalEl.appendChild(
        el(
          "div",
          { class: "drawer-inner" },
          el(
            "div",
            { class: "drawer-header" },
            el("h2", { class: "fs-15" }, "From template…"),
            el("button", { class: "ghost icon-btn", onclick: closeTemplateModal },
              el("i", { "data-lucide": "x" }))
          ),
          el("p", { class: "muted fs-12", style: "margin-bottom:12px" }, "Pick a starter routine and fill in the details."),
          grid
        )
      );
    }

    function openFromTemplate(tpl) {
      // Build default inputs from template
      const inputDefs = tpl.inputs || [];
      const inputValues = {};
      inputDefs.forEach((inp) => {
        inputValues[inp.name] = inp.default != null ? String(inp.default) : "";
      });

      renderTemplateForm(tpl, inputValues);
    }

    function renderTemplateForm(tpl, inputValues) {
      templateModalEl.innerHTML = "";
      const inputDefs = tpl.inputs || [];
      const formFields = [];
      const currentValues = Object.assign({}, inputValues);

      inputDefs.forEach((inp) => {
        const fieldInput = el("input", {
          class: "form-input",
          value: currentValues[inp.name] != null ? String(currentValues[inp.name]) : "",
          placeholder: inp.label || inp.name,
          oninput: (e) => { currentValues[inp.name] = e.target.value; },
        });
        formFields.push(
          el("div", { style: "margin-bottom:10px" },
            el("div", { class: "form-label" }, inp.label || inp.name),
            fieldInput
          )
        );
      });

      const errEl = el("div", { class: "form-error", style: "display:none" }, "");
      const createBtn = el(
        "button",
        {
          class: "btn primary",
          onclick: async () => {
            createBtn.disabled = true;
            createBtn.textContent = "Creating…";
            errEl.style.display = "none";
            try {
              await api("/v1/routines/from-template", {
                method: "POST",
                body: JSON.stringify({ slug: tpl.slug, inputs: currentValues }),
              });
              closeTemplateModal();
              toastShow("Routine created from template", "ok");
              await loadRoutines();
            } catch (err) {
              errEl.textContent = err.message || "Failed to create routine";
              errEl.style.display = "";
              createBtn.disabled = false;
              createBtn.textContent = "Create routine";
            }
          },
        },
        "Create routine"
      );

      templateModalEl.appendChild(
        el(
          "div",
          { class: "drawer-inner" },
          el(
            "div",
            { class: "drawer-header" },
            el("h2", { class: "fs-15" }, tpl.name || tpl.slug),
            el("button", {
              class: "ghost icon-btn",
              onclick: () => { templateModalEl.innerHTML = ""; openTemplateModal(); },
            }, "←")
          ),
          el("p", { class: "muted fs-12", style: "margin-bottom:12px" }, tpl.description || ""),
          ...formFields,
          errEl,
          el(
            "div",
            { class: "row gap-2", style: "margin-top:16px" },
            createBtn,
            el("button", { class: "btn ghost", onclick: closeTemplateModal }, "Cancel")
          )
        )
      );
    }

    // ---- layout
    const newBtn = el(
      "button",
      { class: "btn primary", onclick: openCreateDrawer },
      "+ New Routine"
    );

    const fromTemplateBtn = el(
      "button",
      {
        class: "btn",
        onclick: async () => {
          if (!templates.length) await loadTemplates();
          openTemplateModal();
        },
      },
      "From template…"
    );

    const table = el(
      "table",
      { class: "table routines-table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", {}, "Name"),
          el("th", {}, "Trigger"),
          el("th", {}, "Next fire"),
          el("th", {}, "Status"),
          el("th", {}, "Last run"),
          el("th", {}, "Actions")
        )
      ),
      tbody
    );

    const body = el("div", { class: "page-body" }, table, histSection, outputSection);

    const page = el(
      "div",
      { class: "page" },
      pageHeader({
        title: "Routines",
        sub: "Scheduled + manual agent jobs",
        actions: [fromTemplateBtn, newBtn],
      }),
      body,
      drawerOverlay,
      drawerEl,
      templateModalOverlay,
      templateModalEl
    );

    view.appendChild(page);
    await loadRoutines();

    // Refetch when tab gets focus
    const onFocus = () => { if (currentRoute === "routines") void loadRoutines(); };
    window.addEventListener("focus", onFocus);
    const observer = new MutationObserver(() => {
      if (!view.contains(body)) {
        window.removeEventListener("focus", onFocus);
        observer.disconnect();
      }
    });
    observer.observe(view, { childList: true });
  }

  // ============================================================ Boot
  // ============================================================ Daily journal banner

  // Default questions shown in the journal modal.
  const JOURNAL_QUESTIONS = [
    { id: "focus", prompt: "What's your focus today?" },
    { id: "remember", prompt: "Anything you want me to remember?" },
    { id: "mood", prompt: "How are you feeling? (mood / energy)" },
  ];

  async function checkJournalBanner() {
    // Only show on the chat route.
    if (currentRoute !== "chat") return;
    try {
      const me = await api("/v1/me");
      if (me.has_today_journal) return;
    } catch (_err) {
      return; // silently skip if /v1/me fails
    }
    // Don't show if user already dismissed for today.
    const dismissKey = `fcc:journal:dismiss:${new Date().toISOString().slice(0, 10)}`;
    if (localStorage.getItem(dismissKey)) return;

    const existing = document.querySelector(".journal-banner");
    if (existing) return; // already injected

    const banner = el(
      "div",
      { class: "journal-banner" },
      el("span", { class: "journal-banner-text" }, "5-min journal? answer 3 questions"),
      el(
        "button",
        {
          class: "journal-banner-btn primary",
          onclick: () => openJournalModal(banner, dismissKey),
        },
        "Start"
      ),
      el(
        "button",
        {
          class: "journal-banner-dismiss",
          title: "Skip today",
          onclick: async () => {
            // Write an empty stub so the banner won't reappear.
            try {
              const today = new Date().toISOString().slice(0, 10);
              await api("/v1/journal", {
                method: "POST",
                body: JSON.stringify({ date: today, answers: {} }),
              });
            } catch (_err) {
              // best-effort
            }
            localStorage.setItem(dismissKey, "1");
            banner.remove();
          },
        },
        "Skip today"
      )
    );

    // Insert before the chat-shell so it appears above the session list.
    const view = $("#view");
    const shell = view.querySelector(".chat-shell");
    if (shell) {
      view.insertBefore(banner, shell);
    }
  }

  function openJournalModal(banner, dismissKey) {
    const today = new Date().toISOString().slice(0, 10);
    const answers = {};
    const textareas = [];

    const fields = JOURNAL_QUESTIONS.map((q) => {
      const ta = el("textarea", {
        class: "journal-answer",
        placeholder: "…",
        rows: "3",
        oninput: (e) => {
          answers[q.id] = e.target.value;
        },
      });
      textareas.push(ta);
      return el(
        "div",
        { class: "journal-field" },
        el("label", { class: "journal-label" }, q.prompt),
        ta
      );
    });

    const errorEl = el("div", { class: "journal-error", style: "display:none" }, "");

    const modal = el(
      "div",
      { class: "modal-overlay", onclick: (e) => e.target === modal && modal.remove() },
      el(
        "div",
        { class: "modal-card journal-modal" },
        el("h2", { class: "modal-title" }, `Journal — ${today}`),
        ...fields,
        errorEl,
        el(
          "div",
          { class: "modal-actions" },
          el(
            "button",
            {
              class: "primary",
              onclick: async (e) => {
                const btn = e.currentTarget;
                btn.disabled = true;
                btn.textContent = "Saving…";
                errorEl.style.display = "none";
                try {
                  await api("/v1/journal", {
                    method: "POST",
                    body: JSON.stringify({ date: today, answers }),
                  });
                  localStorage.setItem(dismissKey, "1");
                  modal.remove();
                  banner.remove();
                  toastShow("Journal saved ✓", "ok");
                } catch (err) {
                  errorEl.textContent = `Error: ${err.message}`;
                  errorEl.style.display = "";
                  btn.disabled = false;
                  btn.textContent = "Save";
                }
              },
            },
            "Save"
          ),
          el(
            "button",
            { onclick: () => modal.remove() },
            "Cancel"
          )
        )
      )
    );

    document.body.appendChild(modal);
    if (textareas[0]) textareas[0].focus();
  }

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
    // Show the journal banner on startup (async, non-blocking).
    void checkJournalBanner();
  }
  boot();
})();
