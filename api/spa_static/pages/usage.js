export function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Usage</h1>
        <p>Token spend, request volume, tool-call breakdown.</p>
      </div>
    </div>
    <div class="empty-state">
      <div class="glyph">↗</div>
      <h3>Analytics endpoint not wired yet</h3>
      <p>
        Aggregated usage is logged via <span class="code-inline">trace_event</span>
        but not yet rolled up. Roadmap: parse the structured log file at
        <span class="code-inline">logs/trace.jsonl</span> and surface daily / per-model totals here.
      </p>
    </div>
  `;
}
