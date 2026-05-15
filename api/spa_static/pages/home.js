import { api, navigateTo, utilEscapeHtml as h } from "/app/assets/shell.js";

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Home</h1>
        <p>Live snapshot of what's connected, what needs attention, and what to add next.</p>
      </div>
      <a class="ghost-button primary" href="/app/setup">Run setup wizard</a>
    </div>

    <section>
      <div class="section-heading">
        <h2>Capabilities</h2>
        <span class="muted" id="capCount">scanning…</span>
      </div>
      <div class="grid grid-3" id="capGrid"></div>
    </section>

    <section>
      <div class="section-heading">
        <h2>Quick actions</h2>
      </div>
      <div class="grid grid-3">
        <a class="card" href="/app/chat">
          <h3>◈ Open chat</h3>
          <p>Talk to the active model. Tools dispatch through the agent loop when enabled.</p>
        </a>
        <a class="card" href="/app/audit">
          <h3>◉ Watch audit log</h3>
          <p>Live tail of every tool invocation with input + result preview.</p>
        </a>
        <a class="card" href="/app/settings">
          <h3>⚙ Settings</h3>
          <p>API keys, agent mode, denylists, rate limits, and integrations.</p>
        </a>
      </div>
    </section>
  `;

  try {
    const cap = await api("/admin/api/capabilities");
    renderCapabilities(cap);
  } catch (err) {
    container.querySelector("#capGrid").innerHTML = `
      <div class="empty-state">
        <div class="glyph">!</div>
        <h3>Could not load capabilities</h3>
        <p>${h(err.message)}</p>
      </div>
    `;
  }
}

function renderCapabilities(cap) {
  const grid = document.querySelector("#capGrid");
  const count = document.querySelector("#capCount");
  const groups = [
    { id: "active", label: "Active" },
    { id: "inactive", label: "Needs attention" },
    { id: "suggested", label: "Suggested" },
  ];
  let total = 0;
  grid.innerHTML = "";
  groups.forEach((group) => {
    const items = cap[group.id] || [];
    total += items.length;
    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = `cap-card ${group.id}`;
      card.innerHTML = `
        <header>
          <span class="status-pill ${pillClass(group.id)}">${group.label}</span>
          <strong>${h(item.title)}</strong>
        </header>
        <p>${h(item.summary)}</p>
      `;
      if (item.cta_field) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ghost-button";
        btn.textContent = item.cta_label || "Open";
        btn.addEventListener("click", () =>
          navigateTo(`/app/settings#field-${item.cta_field}`),
        );
        card.appendChild(btn);
      }
      grid.appendChild(card);
    });
  });
  count.textContent = `${total} item${total === 1 ? "" : "s"}`;
  if (total === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <div class="glyph">◇</div>
        <h3>No capabilities yet</h3>
        <p>Run the setup wizard to connect a provider and unlock the rest.</p>
      </div>
    `;
  }
}

function pillClass(group) {
  return { active: "ok", inactive: "warn", suggested: "accent" }[group] || "";
}
