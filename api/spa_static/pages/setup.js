export function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Setup wizard</h1>
        <p>Connect a provider, enable agent mode, toggle integrations — in 4 steps.</p>
      </div>
    </div>
    <div class="card" style="padding:0; overflow:hidden; height: calc(100vh - 220px); min-height: 540px;">
      <iframe
        src="/admin/onboarding"
        style="width:100%; height:100%; border:0; background: var(--bg); color-scheme: dark;"
        title="Onboarding wizard"
      ></iframe>
    </div>
  `;
}
