/* Free Claude Code · onboarding wizard.
 *
 * 5-step flow: welcome → provider+key → agent mode → integrations → done.
 * All writes go through the existing /admin/api/config/{validate,apply} endpoints
 * so the wizard stays in sync with the main admin form.
 */

const STEPS = [
  { id: "welcome", title: "Welcome" },
  { id: "provider", title: "Provider" },
  { id: "agent", title: "Agent" },
  { id: "integrations", title: "Tools" },
  { id: "done", title: "Done" },
];

const PROVIDERS = [
  {
    id: "deepseek",
    label: "DeepSeek",
    field: "DEEPSEEK_API_KEY",
    model: "deepseek/deepseek-v4-flash",
    blurb: "Cheapest broad-capability default. Great first pick.",
  },
  {
    id: "open_router",
    label: "OpenRouter",
    field: "OPENROUTER_API_KEY",
    model: "open_router/anthropic/claude-3.5-sonnet",
    blurb: "Single key, every model. Pay-as-you-go.",
  },
  {
    id: "nvidia_nim",
    label: "NVIDIA NIM",
    field: "NVIDIA_NIM_API_KEY",
    model: "nvidia_nim/z-ai/glm4.7",
    blurb: "Free tier + GLM models hosted by NVIDIA.",
  },
  {
    id: "zai",
    label: "Z.ai Coding Plan",
    field: "ZAI_API_KEY",
    model: "zai/glm-4.6",
    blurb: "Subscription with high quotas, code-tuned models.",
  },
];

const state = {
  step: 0,
  provider: null,
  apiKey: "",
  agentEnabled: true,
  workspace: "",
  bashDenylist: "rm,sudo",
  enableWebTools: true,
  configFields: null,
};

const byId = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${text}`);
  }
  return response.json();
}

async function loadConfig() {
  const config = await api("/admin/api/config");
  state.configFields = new Map(config.fields.map((f) => [f.key, f]));
  if (!state.workspace) {
    const ws = state.configFields.get("AGENT_DEFAULT_WORKSPACE");
    if (ws && ws.value) state.workspace = ws.value;
  }
}

function setStatus(message, level = "") {
  const el = byId("status");
  el.textContent = message;
  el.className = `wizard-status ${level}`.trim();
}

function renderStepper() {
  const stepper = byId("stepper");
  stepper.innerHTML = "";
  STEPS.forEach((step, idx) => {
    const node = document.createElement("div");
    node.className = "step";
    if (idx === state.step) node.classList.add("current");
    if (idx < state.step) node.classList.add("done");
    node.textContent = step.title;
    stepper.appendChild(node);
  });
}

function renderWelcome() {
  return `
    <h2>Welcome to Free Claude Code</h2>
    <p class="lead">
      A universal proxy that lets any AI provider speak the Anthropic API,
      with optional server-side tool execution. This 4-step wizard will get
      you running in under a minute.
    </p>
    <div class="wizard-summary">
      <strong>What you'll set up:</strong><br />
      1. Pick a chat provider and paste an API key<br />
      2. (Optional) enable agent mode + workspace<br />
      3. (Optional) turn on local web tools<br />
      4. Done — start chatting via <kbd>POST /v1/messages</kbd>
    </div>
  `;
}

function renderProvider() {
  const cards = PROVIDERS.map((p) => {
    const active = state.provider === p.id;
    return `
      <label class="wizard-toggle" style="${active ? "border-color:var(--accent,#6c7cff);" : ""}">
        <input type="radio" name="provider" value="${p.id}" ${active ? "checked" : ""} />
        <div>
          <strong>${p.label}</strong>
          <span>${p.blurb}</span>
        </div>
      </label>
    `;
  }).join("");
  return `
    <h2>Pick your default provider</h2>
    <p class="lead">Choose where requests get routed. You can add more later from the admin form.</p>
    ${cards}
    <label>API key</label>
    <input id="apiKeyInput" type="password" placeholder="sk-..." value="${escapeHtml(state.apiKey)}" />
    <p class="field-hint">Stored in <code>~/.config/free-claude-code/.env</code> and never logged.</p>
  `;
}

function renderAgent() {
  return `
    <h2>Agent mode</h2>
    <p class="lead">
      When enabled, requests with no client-side tools route through a
      multi-turn loop with local Read/Write/Edit/Bash/Glob/Grep/LS tools.
      The model can browse + edit files, run shell commands, all sandboxed
      to one workspace directory.
    </p>
    <label class="wizard-toggle">
      <input type="checkbox" id="agentEnabledInput" ${state.agentEnabled ? "checked" : ""} />
      <div>
        <strong>Enable agent mode</strong>
        <span>Recommended. Off = pure passthrough proxy.</span>
      </div>
    </label>
    <label>Workspace path</label>
    <input id="workspaceInput" type="text" placeholder="/Users/you/projects" value="${escapeHtml(state.workspace)}" />
    <p class="field-hint">Sandbox root for all file operations. Tools cannot escape this directory.</p>
    <label>Bash denylist (comma-separated)</label>
    <input id="denylistInput" type="text" placeholder="rm,sudo,curl|sh" value="${escapeHtml(state.bashDenylist)}" />
    <p class="field-hint">Substring match against shell commands. Defends against prompt injection.</p>
  `;
}

function renderIntegrations() {
  return `
    <h2>Integrations</h2>
    <p class="lead">
      Optional capabilities the proxy can serve. Toggle anything; you can
      change it later from the admin form.
    </p>
    <label class="wizard-toggle">
      <input type="checkbox" id="webToolsInput" ${state.enableWebTools ? "checked" : ""} />
      <div>
        <strong>Web search + fetch</strong>
        <span>Local handling of Anthropic <code>web_search</code> / <code>web_fetch</code> tools.</span>
      </div>
    </label>
    <div class="wizard-summary">
      <strong>Coming soon (roadmap):</strong><br />
      + Obsidian vault read/write<br />
      + MCP server passthrough<br />
      + Image generation provider routing<br />
      + Skill marketplace (drop YAML in <kbd>plugins/</kbd>)
    </div>
  `;
}

function renderDone() {
  const provider = PROVIDERS.find((p) => p.id === state.provider);
  return `
    <h2>You're ready</h2>
    <p class="lead">Configuration applied. Try it out:</p>
    <div class="wizard-summary">
curl -N http://127.0.0.1:8082/v1/messages \\<br />
&nbsp;&nbsp;-H "x-api-key: $YOUR_TOKEN" \\<br />
&nbsp;&nbsp;-H "content-type: application/json" \\<br />
&nbsp;&nbsp;-d '{<br />
&nbsp;&nbsp;&nbsp;&nbsp;"model":"${provider ? provider.model : "deepseek/deepseek-v4-flash"}",<br />
&nbsp;&nbsp;&nbsp;&nbsp;"max_tokens":256,<br />
&nbsp;&nbsp;&nbsp;&nbsp;"messages":[{"role":"user","content":"Hello"}]<br />
&nbsp;&nbsp;}'
    </div>
    <p class="lead" style="margin-top:1.5rem;">
      Need to tweak something? Open the
      <a href="/admin" style="color:var(--accent,#6c7cff)">admin form</a>
      anytime.
    </p>
  `;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

function captureCurrentStep() {
  if (state.step === 1) {
    const sel = document.querySelector('input[name="provider"]:checked');
    state.provider = sel ? sel.value : null;
    state.apiKey = byId("apiKeyInput")?.value || "";
  } else if (state.step === 2) {
    state.agentEnabled = byId("agentEnabledInput")?.checked || false;
    state.workspace = byId("workspaceInput")?.value || "";
    state.bashDenylist = byId("denylistInput")?.value || "";
  } else if (state.step === 3) {
    state.enableWebTools = byId("webToolsInput")?.checked || false;
  }
}

function validateStep() {
  if (state.step === 1) {
    if (!state.provider) return "Pick a provider to continue.";
    if (!state.apiKey || state.apiKey.length < 4) return "Enter the API key for that provider.";
  }
  if (state.step === 2 && state.agentEnabled && !state.workspace) {
    return "Set a workspace path for agent mode (or disable it).";
  }
  return null;
}

function buildPayload() {
  const provider = PROVIDERS.find((p) => p.id === state.provider);
  const values = {};
  if (provider) {
    values[provider.field] = state.apiKey;
    values.MODEL = provider.model;
  }
  values.AGENT_MODE_ENABLED = String(state.agentEnabled);
  if (state.workspace) values.AGENT_DEFAULT_WORKSPACE = state.workspace;
  if (state.bashDenylist) values.AGENT_BASH_DENYLIST = state.bashDenylist;
  values.ENABLE_WEB_SERVER_TOOLS = String(state.enableWebTools);
  return { values };
}

async function applyConfig() {
  setStatus("Validating…");
  const payload = buildPayload();
  const validation = await api("/admin/api/config/validate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!validation.valid) {
    setStatus(`Validation failed: ${validation.errors.join("; ")}`, "error");
    return false;
  }
  setStatus("Applying…");
  const result = await api("/admin/api/config/apply", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!result.applied) {
    setStatus(`Apply failed: ${result.errors?.join("; ") || "unknown"}`, "error");
    return false;
  }
  if (result.restart?.required) {
    setStatus("Saved. Server restart pending — settings apply on next request.", "ok");
  } else {
    setStatus("Saved. Settings live now.", "ok");
  }
  return true;
}

function render() {
  renderStepper();
  const body = byId("stepBody");
  const renderers = [renderWelcome, renderProvider, renderAgent, renderIntegrations, renderDone];
  body.innerHTML = renderers[state.step]();
  byId("backButton").disabled = state.step === 0;
  byId("nextButton").textContent =
    state.step === STEPS.length - 1
      ? "Open admin"
      : state.step === STEPS.length - 2
        ? "Apply →"
        : "Next →";
  setStatus("");
}

async function next() {
  captureCurrentStep();
  const error = validateStep();
  if (error) {
    setStatus(error, "error");
    return;
  }
  if (state.step === STEPS.length - 2) {
    const ok = await applyConfig().catch((err) => {
      setStatus(err.message, "error");
      return false;
    });
    if (!ok) return;
  }
  if (state.step === STEPS.length - 1) {
    window.location.href = "/admin";
    return;
  }
  state.step += 1;
  render();
}

function back() {
  captureCurrentStep();
  if (state.step === 0) return;
  state.step -= 1;
  render();
}

async function init() {
  byId("nextButton").addEventListener("click", () => {
    next().catch((err) => setStatus(err.message, "error"));
  });
  byId("backButton").addEventListener("click", back);
  try {
    await loadConfig();
  } catch (err) {
    setStatus(`Could not load existing config: ${err.message}`, "error");
  }
  render();
}

init();
