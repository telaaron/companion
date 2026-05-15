export function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1>Chat</h1>
        <p>Send a request to the proxy and see the streamed response — useful for sanity-checking agent mode.</p>
      </div>
    </div>

    <section class="card" style="gap: 12px;">
      <div class="form-row">
        <label for="chatModel">Model</label>
        <input id="chatModel" type="text" value="deepseek/deepseek-v4-flash" />
      </div>
      <div class="form-row">
        <label for="chatPrompt">Prompt</label>
        <textarea id="chatPrompt" rows="4" placeholder="Ask the proxy anything…"></textarea>
      </div>
      <div class="form-row">
        <label for="chatToken">API token (optional, x-api-key)</label>
        <input id="chatToken" type="password" placeholder="freecc" />
      </div>
      <div style="display:flex; gap: 8px; align-items:center;">
        <button id="chatSend" class="ghost-button primary" type="button">Send</button>
        <span id="chatStatus" class="muted"></span>
      </div>
    </section>

    <section class="card" style="min-height: 240px;">
      <h3>Stream</h3>
      <pre id="chatOutput" style="margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;white-space:pre-wrap;color:var(--text);max-height:60vh;overflow:auto;"></pre>
    </section>
  `;

  const output = container.querySelector("#chatOutput");
  const statusEl = container.querySelector("#chatStatus");
  container.querySelector("#chatSend").addEventListener("click", async () => {
    output.textContent = "";
    statusEl.textContent = "sending…";
    try {
      const model = container.querySelector("#chatModel").value.trim();
      const prompt = container.querySelector("#chatPrompt").value.trim();
      const token = container.querySelector("#chatToken").value.trim();
      if (!prompt) {
        statusEl.textContent = "prompt required";
        return;
      }
      const headers = { "content-type": "application/json" };
      if (token) headers["x-api-key"] = token;
      const response = await fetch("/v1/messages", {
        method: "POST",
        headers,
        body: JSON.stringify({
          model,
          max_tokens: 512,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      if (!response.ok) {
        statusEl.textContent = `error ${response.status}`;
        output.textContent = await response.text();
        return;
      }
      statusEl.textContent = "streaming…";
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        output.textContent += decoder.decode(value, { stream: true });
        output.scrollTop = output.scrollHeight;
      }
      statusEl.textContent = "done";
    } catch (err) {
      statusEl.textContent = `error: ${err.message}`;
    }
  });
}
