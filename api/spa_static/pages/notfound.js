export function render(container) {
  container.innerHTML = `
    <div class="empty-state" style="margin-top: 80px;">
      <div class="glyph">404</div>
      <h3>Page not found</h3>
      <p>
        That route does not exist. Try the <a href="/app/" class="code-inline">home</a>
        or the <a href="/app/settings" class="code-inline">settings</a>.
      </p>
    </div>
  `;
}
