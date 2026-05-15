export function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Projects</h1>
        <p>Per-workspace agent runs, scoped tool denylists, isolated memory.</p>
      </div>
    </div>
    <div class="empty-state">
      <div class="glyph">▤</div>
      <h3>Project model coming soon</h3>
      <p>
        Each project will pin a workspace path, system prompt, allowed tool set,
        and capability budget. Listed under
        <span class="code-inline">~/Documents/free-claude-code/projects/&lt;slug&gt;</span>.
      </p>
    </div>
  `;
}
