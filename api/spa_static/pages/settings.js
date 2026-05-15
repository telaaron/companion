export function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Settings</h1>
        <p>API keys, agent mode, denylists, rate limits, integrations. Apply hot-reloads.</p>
      </div>
      <a class="ghost-button" href="/admin" target="_blank" rel="noopener">Open in new tab ↗</a>
    </div>

    <div class="card" style="padding:0; overflow:hidden; height: calc(100vh - 220px); min-height: 480px;">
      <iframe
        src="/admin"
        style="width:100%; height:100%; border:0; background: var(--bg); color-scheme: dark;"
        title="Settings form"
      ></iframe>
    </div>
  `;
}
