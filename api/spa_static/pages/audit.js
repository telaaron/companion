import { utilEscapeHtml as h } from "/app/assets/shell.js";

const LOG_CANDIDATES = [
  "/admin/api/audit/recent",
];

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Audit log</h1>
        <p>Every tool invocation, with input summary, result preview, and duration.</p>
      </div>
      <button id="auditRefresh" class="ghost-button" type="button">Refresh</button>
    </div>
    <div id="auditTable"></div>
  `;
  container.querySelector("#auditRefresh").addEventListener("click", () => load(container));
  await load(container);
}

async function load(container) {
  const target = container.querySelector("#auditTable");
  target.innerHTML = "<div class=\"spinner\"></div>";
  for (const path of LOG_CANDIDATES) {
    try {
      const response = await fetch(path);
      if (response.ok) {
        const events = await response.json();
        renderEvents(target, events);
        return;
      }
    } catch {
      /* try next */
    }
  }
  target.innerHTML = `
    <div class="empty-state">
      <div class="glyph">◉</div>
      <h3>Audit endpoint not wired yet</h3>
      <p>
        Tool calls are logged structured via <span class="code-inline">trace_event</span>
        (event <span class="code-inline">api.agent_loop.tool_use</span>). Roadmap: read
        <span class="code-inline">logs/trace.jsonl</span> server-side and stream recent
        events here via SSE.
      </p>
    </div>
  `;
}

function renderEvents(target, events) {
  if (!Array.isArray(events) || events.length === 0) {
    target.innerHTML = `
      <div class="empty-state">
        <div class="glyph">◯</div>
        <h3>No tool calls yet</h3>
        <p>Send a request via the Chat page or the proxy directly.</p>
      </div>
    `;
    return;
  }
  const rows = events
    .map(
      (e) => `
      <tr>
        <td>${h(e.timestamp || "")}</td>
        <td><span class="code-inline">${h(e.tool || "")}</span></td>
        <td>${h(e.input_summary || "")}</td>
        <td class="num">${h(String(e.duration_ms ?? ""))}</td>
        <td>${e.is_error ? '<span class="status-pill error">error</span>' : '<span class="status-pill ok">ok</span>'}</td>
      </tr>
    `,
    )
    .join("");
  target.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Time</th><th>Tool</th><th>Input</th><th>ms</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}
