export function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>File edits</h1>
        <p>Recent Write / Edit operations performed by the agent loop.</p>
      </div>
    </div>
    <div class="empty-state">
      <div class="glyph">≡</div>
      <h3>File-edit history coming soon</h3>
      <p>
        Each Write / Edit invocation already emits an audit event with byte counts.
        Roadmap: surface diffs + workspace tree here, with rollback per operation.
      </p>
    </div>
  `;
}
