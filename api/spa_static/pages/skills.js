export function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Skills</h1>
        <p>Reusable plugin packages — Anthropic-style skills, MCP servers, vault connectors.</p>
      </div>
    </div>
    <div class="empty-state">
      <div class="glyph">★</div>
      <h3>Skill marketplace coming soon</h3>
      <p>
        Roadmap: drop a YAML in <span class="code-inline">plugins/</span> to register an MCP
        server, an Obsidian vault, or an image-generation provider. Each skill exposes a tool
        the agent loop can dispatch alongside the built-ins.
      </p>
    </div>
  `;
}
