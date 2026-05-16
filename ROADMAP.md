# Companion · Roadmap

> Detailed enough that a fresh AI agent can pick any item and ship it in one
> session. Each item lists **goal**, **why**, **files**, **acceptance**, and
> **risks**.

Last updated: 2026-05-16.
Branch: `feat/agentic-os-phase-b` on `telaaron/companion`.

---

## Phase 1 — Polish + correctness (this quarter)

### 1.1 Per-chat usage actually populates
**Goal**: `chat-usage` pill in the chat header shows real token + cost
totals, not `0 tok · $0.000`.

**Why**: backend endpoint `/v1/sessions/{sid}/usage` is wired and the UI
calls it, but the background-jobs path (`api/agent/jobs.py`) never calls
`datastore.record_usage_event(...)`, so the table stays empty for every
job-routed turn (= every chat reply since we switched the chat to jobs).

**Files**:
- `api/agent/jobs.py::_run_job` — after `async for chunk in run_agent_streaming`
  finishes, extract `usage` from the final `message_delta` event (or sum
  per-turn `usage` from the collector).
- `api/datastore.py::record_usage_event` — already exists, takes `kind`,
  `provider`, `model`, `input_tokens`, `output_tokens`, `cost_usd`,
  `duration_ms`, `project_id`, `session_id`, `request_id`, `metadata`.
- `api/pricing.py::pricing_snapshot` — already maps model→$/Mtok.

**Acceptance**: open a chat, send 3 messages, refresh — pill shows real
non-zero totals. `/v1/sessions/{sid}/usage` returns matching numbers.

**Risks**:
- The usage events emitted by upstream are not always integers (some
  providers return floats or omit fields). Gracefully default to 0.
- Cost calc requires the upstream model id, not the gateway prefix.
  Re-route the model through `model_router` before lookup.

---

### 1.2 MCP server passthrough (Linear, Notion, Slack, Jira)
**Goal**: drop a YAML like

```yaml
name: linear
kind: mcp_server
command: npx
args: -y @modelcontextprotocol/server-linear
env:
  LINEAR_API_KEY: $LINEAR_API_KEY
```

into `plugins/`, restart, and every Linear tool the MCP server exposes
becomes available to the model.

**Why**: the user already mentioned MCP is the "general" interface for
extending Companion. Manually building one Obsidian-style adapter per
service doesn't scale.

**Files**:
- `api/agent/plugins.py::load_all` — add a `mcp_server` branch that
  spawns the subprocess + JSON-RPC handshake.
- New `api/agent/mcp.py`:
  - `MCPClient` class: starts subprocess with stdio JSON-RPC, sends
    `initialize`, lists tools via `tools/list`, dispatches `tools/call`.
  - Long-lived connection per plugin, with reconnect on EOF.
  - Convert each MCP tool into a `ToolSpec` with executor that wraps
    `mcp.call_tool(name, input)` and returns a `ToolResult`.
- `plugins/README.md` — document `mcp_server` kind fully (env passthrough,
  command, args, dynamic tool registration).

**Acceptance**:
1. Add `plugins/linear.yaml` with the official Linear MCP server.
2. Restart proxy. Log line `PLUGIN: registered 12 MCP tools from linear`.
3. Chat: "List my Linear issues assigned to me" → model calls
   `linear_list_my_issues` → real Linear data in the response.

**Risks**:
- MCP subprocess crash needs handling — auto-restart with backoff.
- Stdin/stdout JSON-RPC framing is delicate (one JSON per line vs.
  Content-Length headers — both exist in the wild). Use the official
  Python SDK if available: `pip install mcp` (Anthropic's `model-context-protocol` package).
- Some MCP tools have schemas Anthropic-style won't accept verbatim
  (e.g. `oneOf`). Sanitise schemas during conversion.

---

### 1.3 File preview side panel
**Goal**: when the agent reads or writes a file, the dashboard opens a
collapsible right-rail panel showing the file's current contents with
syntax highlighting. Like Claude Code's "you just modified this" view.

**Why**: tool blocks today are collapsed text. Reviewing 50 small edits
across 8 files needs a richer surface.

**Files**:
- `api/ui_static/app.js`:
  - Add `openPreviewPanel(path)` that injects a `<aside class="preview">`
    next to `.messages`.
  - Wire from each Read/Write/Edit tool block (click → open).
  - Inside: tabs for Recent reads, Recent writes, Current open file.
  - Fetch via `GET /v1/preview/file?path=<>&session_id=<>` (new endpoint).
- `api/dashboard_routes.py`:
  - New `GET /v1/preview/file?path=...` returns content + language hint.
  - Path resolved through `Workspace.resolve` so traversal is blocked.
- `api/ui_static/styles.css`:
  - `.preview` panel with sticky header, language badge, line-numbered
    `<pre>`, copy button.
- Library: vendor Prism.js (`prism.js` + `prism-tomorrow.css`) for
  syntax highlighting — vanilla, no build step.

**Acceptance**: chat with the model "edit my README.md and add a section
about X". Side panel opens, shows the updated file with syntax highlighting,
the line that was edited is gently highlighted.

**Risks**:
- Path security: only allow paths inside the active session's resolved
  workspace.
- File size limit (refuse > 1 MB to avoid loading binaries).

---

### 1.4 Slack / Discord notifier for finished long jobs
**Goal**: jobs that take > 30 s send a message to a configured Slack
channel / Discord webhook when they finish, with a deep link back to
the chat.

**Why**: today the user has to keep the tab open or wait. Long agentic
tasks (writing a deploy script, refactoring across 30 files) should
notify on completion.

**Files**:
- `api/agent/jobs.py::_run_job` — on terminal status, if
  `Settings.notifier_*` is set + job duration > threshold, call
  `api/agent/notifier.py::notify(job)`.
- New `api/agent/notifier.py`:
  - `notify(job_id)` — POSTs to Slack webhook + Discord webhook based on
    env vars `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`.
  - Builds payload: title, model, duration, last-200-chars of reply,
    link `http://127.0.0.1:8082/ui/#chat/{session_id}`.
- `config/settings.py` — three new env vars + thresholds.
- `api/admin_config.py` — surface in the wizard.

**Acceptance**: long job completes → Slack channel gets `"Companion job
'rewrite onboarding' done in 4m12s — click to read"`.

**Risks**:
- Webhook failure must not break the job. Wrap in `try/except` + log.
- Spam guard: only notify if `duration > NOTIFIER_MIN_SECONDS` (default 30).

---

### 1.5 Auto-name verification + retry
**Goal**: every session is renamed within ~5 s of first reply. No
"untitled" cards in the sidebar.

**Why**: currently `maybeRenameSessionAsync` works but silently fails
when the upstream rejects or returns an unparseable response.

**Files**:
- `api/ui_static/app.js::maybeRenameSessionAsync` — add 1 retry after
  3 s delay. Log retry to console.
- `api/dashboard_routes.py` — new `POST /v1/sessions/{id}/auto-rename`
  that does the rename server-side instead of via the chat path (cleaner,
  one round-trip).
- Server-side rename calls the configured chat provider directly with
  a fixed system prompt. Uses `httpx` not the agent loop.

**Acceptance**: open 10 fresh sessions. After each reply, title becomes
a 3-6 word summary within 5 s. No retries needed in the common path.

---

### 1.6 Settings — provider-specific test buttons
**Goal**: each provider card in Settings has a "Test connection" button
that does a 1-token completion and reports OK/error with the upstream
response.

**Why**: today users add an API key and don't know if it works until
they try a real chat.

**Files**:
- `api/dashboard_routes.py` — new `POST /v1/providers/{id}/test`. Calls
  `provider.stream_response` with a fixed `{"role":"user","content":"hi"}`,
  `max_tokens=4`. Returns `{ok: true/false, duration_ms, error}`.
- `api/ui_static/app.js::renderSettings` — wire a button on each provider
  capability card. Show inline spinner + result.

**Acceptance**: paste a bad key → "Test" button red, with the upstream
401 message. Paste good key → green check, model id echoed back.

---

## Phase 2 — Big features (next quarter)

### 2.1 Memory + RAG over user's notes / vault / files
**Goal**: AI can search across:
- Past chat turns (already indexed in `memory_fts`).
- All files in `AGENT_DEFAULT_WORKSPACE` (chunked + indexed on first
  scan, refreshed via filesystem watcher).
- Obsidian vaults registered as plugins.

When the user asks "remind me what I wrote about pricing last month",
the AI finds and quotes the source.

**Why**: Companion has no long-term memory beyond the current session.
Without RAG, every conversation starts cold.

**Files**:
- `api/agent/indexer.py` (new) — scans configured directories on startup
  + watches via `watchdog`, chunks files (~800 token windows), indexes
  into `memory_fts`. Embedding-free for v1 (FTS5 BM25 ranking is enough).
- `api/agent/extras/search.py` (existing) — extend to filter by `kind`
  (already supported), add `path_prefix` filter for workspace-scoped
  search.
- `api/dashboard_routes.py` — new `POST /v1/index/rescan` to trigger
  full rescan from the UI.
- New page `/ui` → "Memory" — shows index size, top tags, last scan time,
  rescan button.
- `config/settings.py` — `MEMORY_INDEX_PATHS` (comma-separated).

**Acceptance**:
1. Set `MEMORY_INDEX_PATHS=~/Documents/Obsidian Vault,~/projects`.
2. Restart. Watch log `INDEXER: scanned 1247 files in 8.4 s`.
3. Chat: "find my notes mentioning Vercel cold starts". AI calls
   `Search(query="Vercel cold starts")`, returns 5 hits, quotes the
   most relevant one.

**Risks**:
- Disk size: FTS5 over 100k files can hit 500 MB. Cap with `INDEXER_MAX_BYTES`.
- Watchdog on macOS needs `fsevents` extras (`watchdog[watchmedo]`).
- Embedding-free FTS misses semantic search. Phase 2.1.b: add optional
  `sentence-transformers` local embeddings + cosine ranking.

---

### 2.2 Skill marketplace
**Goal**: browse a curated catalog of Anthropic-style skills (PDF, Excel,
PowerPoint, Docx, image-gen, …) inside `/ui` → Skills, click "Install"
to make them available to the model.

**Why**: today Skills is read-only. Companion should match Claude Code's
skill experience.

**Files**:
- New `skills/` directory at repo root. Each skill = a folder with
  `SKILL.md` (description + when to use) + scripts + assets.
- `api/agent/extras/skills.py` — registers `SkillRun(name, ...)` and
  `SkillList()` tools. SkillRun looks up `skills/{name}` and exposes its
  scripts to the model.
- `api/dashboard_routes.py` — `GET /v1/skills/catalog` returns curated
  index from a hosted JSON (e.g. github.com/anthropic-skills). Local
  installs go to `skills/`.
- New page module — Skills page shows local + remote, install button
  downloads + extracts.

**Acceptance**: Skills page → "PDF" → Install → confirmation → model
can now answer "extract page 5 of this PDF" by calling the PDF skill.

**Risks**:
- Skills are arbitrary code. Sandbox script execution OR mark unsigned
  skills as warn-on-install.
- Catalog freshness: cache for 24 h, allow force refresh.

---

### 2.3 Multi-user (light)
**Goal**: each device or browser session gets its own `user_id` tied to
projects + sessions. Two laptops can hit the same proxy without seeing
each other's chats.

**Why**: today the proxy is single-user. Sharing the Mac via Tailscale
or a tunnel mixes everyone's data.

**Files**:
- `api/datastore.py` — add `user_id` column to projects + sessions +
  jobs. Migration on startup (sqlite ALTER TABLE).
- `api/dependencies.py` — derive `user_id` from header
  `x-companion-user` OR from the `ANTHROPIC_AUTH_TOKEN` if multi-token.
- All list endpoints (`/v1/projects`, `/v1/sessions`, …) — filter by the
  caller's user_id.
- `config/settings.py` — `COMPANION_USERS` env var (CSV of token=user_id
  pairs).

**Acceptance**:
- Alice opens dashboard with `x-companion-user: alice` → sees only her
  sessions.
- Bob with `x-companion-user: bob` → sees his.
- The agent loop dispatches each user's tools to their own
  workspace (per-user `AGENT_DEFAULT_WORKSPACE_<user>`).

**Risks**:
- Don't break single-user mode. When `COMPANION_USERS` is empty, treat
  the whole DB as one bucket (current behaviour).
- Migration must be idempotent.

---

### 2.4 Web companion (Tauri / desktop wrap)
**Goal**: package the dashboard as a native macOS / Windows / Linux
binary that bundles the proxy. Users get a `Companion.app` icon, click
it, dashboard opens, no terminal.

**Why**: friends won't install Python + uv + clone a repo. Drag-to-Applications is the bar.

**Files**:
- New `tauri/` directory. Tauri config that:
  - Spawns `fcc-server` on app start (bundled Python via `pyoxidizer`
    or `briefcase`).
  - Opens the dashboard in a wkwebview at `http://127.0.0.1:8082/ui/`.
  - Tray icon with start/stop + open-dashboard.
- GitHub Actions workflow — `.github/workflows/release.yml` cross-compiles
  for macOS arm64 / x86 + Linux x86 + Windows x86. Tags drive a release.

**Acceptance**: download `Companion-1.0.0-arm64.dmg`, drag to Applications,
launch, dashboard opens, no terminal seen.

**Risks**:
- Python bundling is the hard part. `briefcase` works for simple apps;
  uv-managed projects need wrangling.
- Codesigning for macOS distribution (Apple Developer account required
  for non-warning installs).

---

### 2.5 Voice mode
**Goal**: hold spacebar in chat input → speak → Whisper transcribes → AI
replies → reply spoken back via macOS `say` or ElevenLabs.

**Why**: long prompts are tedious to type. Voice is faster for
ideation.

**Files**:
- `api/dashboard_routes.py` — new `POST /v1/transcribe` accepts audio
  blob, returns text. Uses local Whisper (`whisper.cpp`) or routed to
  NVIDIA NIM transcription endpoint.
- `api/ui_static/app.js` — `MediaRecorder` capture on hold-spacebar.
  POST to `/v1/transcribe`, insert text into chat input.
- Optional `POST /v1/tts` for the reply (`say` shell command or
  ElevenLabs API).

**Acceptance**: hold space → red mic indicator → speak "summarize my
last 3 chats about NurEine" → release → text appears → reply streams
back → audio plays.

---

### 2.6 Cross-session memory threads
**Goal**: pin a turn (or chunk of chat) to a project as a "memory" so
future chats in the same project always see it as system context.

**Why**: shared_context on Project is one big blob. Real
workflows need atomic memories ("the OAuth callback is at /auth/cb",
"the Linear ticket convention is COMP-XYZ").

**Files**:
- `api/datastore.py` — new `project_memories` table:
  `(id, project_id, content, source_session_id, created_at)`.
- `api/dashboard_routes.py` — CRUD endpoints.
- `api/agent/jobs.py::_run_job` — prepend project_memories (joined as
  bullets) to the system prompt when a project_id is set.
- UI: long-press on any assistant message → "Pin to project memory".

**Acceptance**: pin "the API key for staging is in 1Password" → start
new chat in same project → AI references it without being told again.

---

## Phase 3 — Self-improvement loop (third quarter)

### 3.1 Insights page (`/ui` → Insights)
**Goal**: every 24 h the proxy reflects on its own audit log + usage
events and publishes:
- Most-used tools (e.g. "Bash: 412 calls, avg 1.3 s").
- Tools that errored most (suggests denylist tweaks).
- Provider cost breakdown.
- Suggestion cards ("You ran `git push` 47 times. Pin a 'deploy'
  shortcut?").

**Why**: Companion should improve itself based on usage. Without
visibility, the user can't tune it.

**Files**:
- `api/agent/insights.py` — runs nightly (via `cli/insight_cron.py` or
  a startup background task). Queries `audit_log` + `usage_events`,
  writes summary to `~/.cache/free-claude-code/insights/YYYY-MM-DD.json`.
- New page module in `api/ui_static/app.js`.
- Suggestions can include a one-click "apply" that PUTs the env change.

**Acceptance**: visit Insights → see "you used 1.2M tokens this week,
$3.42 spent, top tool: Bash. Did you know: clicking ★ pins a chat to
favorites?"

---

### 3.2 Companion as a daily journal
**Goal**: Companion proactively asks 2-3 short questions every morning
("What's your focus today? Anything you want me to remember?"), writes
the answers to the configured Obsidian vault (or `~/journal.md`), and
references the journal when relevant.

**Why**: capture the user's intent + decisions over time without manual
note-taking.

**Files**:
- `api/agent/journal.py` — schedules a daily 8 AM prompt (via
  `cron` or APScheduler). When the user opens the dashboard for the
  first time that day, a banner appears: "5-min journal? answer 3
  questions". Writes to vault on submit.
- New plugin kind `journal` in `plugins/journal.yaml` to bind the
  vault target.

**Acceptance**: morning of day N+1, dashboard shows the journal prompt.
Reply saved to `Vault/Companion/Journal/2026-05-16.md`. Later asking
"what did I plan yesterday?" surfaces yesterday's entry via Search.

---

### 3.3 Self-modification with confirmation
**Goal**: when the user says "you should always ask before deleting
files", the AI:
1. Edits its own system prompt (system-prompt is stored in
   `Settings.companion_extra_prompt`).
2. Asks the user to confirm the change.
3. Persists to env.

**Why**: humans drift their preferences. Companion should learn the
user's evolving rules without code edits.

**Files**:
- `api/agent/extras/preferences.py` — new tool `PreferenceSet(key, value)`
  that writes into a `~/.config/free-claude-code/preferences.md` file
  prepended to every system prompt.
- Identity prompt extended: "When the user expresses a long-term
  preference (e.g. 'always ask before X'), call PreferenceSet to record
  it. Always summarise what you recorded."

**Acceptance**: "Companion, please always answer in German" → AI calls
`PreferenceSet(key='language', value='Always respond in German')` → next
chat starts in German.

---

## Phase 4 — Distribution + community

### 4.1 Public landing page
**Goal**: a Vercel-hosted site at `companion.dev` (or similar) with:
- 30-second screencast.
- "Install in 3 commands" copy block.
- Comparison table vs Claude Code, ChatGPT, etc.
- GitHub stars + Discord link.

**Files**: separate `landing/` directory + Astro / Next site.

---

### 4.2 Plugin registry
**Goal**: `companion plugins install linear` downloads + verifies a
signed plugin YAML from a community-curated index hosted at
`github.com/telaaron/companion-plugins`.

**Files**:
- `cli/entrypoints.py` — new `plugins` subcommand.
- A `companion-plugins` repo with `index.json` and signed `.yaml` files.

---

### 4.3 Discord bot wrapper
**Goal**: run Companion as a Discord bot. Mention `@Companion` → reply
in the channel. Same agent loop, same tools.

**Files**:
- `messaging/discord_bot.py` already exists for the upstream fork — port
  to use the new jobs API.

---

## How to pick an item

1. Read the goal + why + files.
2. Create a branch off `feat/agentic-os-phase-b`:
   `git checkout -b feat/<short-name>`.
3. Build until acceptance criteria pass.
4. Add tests under `tests/` matching existing patterns.
5. Run `uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest`.
6. Open PR to `feat/agentic-os-phase-b` (or merge to `main` if confident).

## Tracking

This file is the source of truth. Cross items off as they ship. Reorder
to taste. Anything not listed here is fair game — but if it changes the
user experience, add it here first.
