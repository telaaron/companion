# Companion — Sub-Agent Handoff Prompts

One self-contained brief per major [ROADMAP.md](../../ROADMAP.md) item. Each
brief assumes a **fresh agent** with no memory of prior conversations. Copy a
brief into a new Claude Code session (or hand it to another agent runner) and
it should be enough to ship the item end-to-end.

## How to use a brief

1. Open the brief for the item you want shipped.
2. Paste it as the first message in a fresh agent session that has access to
   this repo.
3. The brief contains the goal, file paths, implementation plan, acceptance
   criteria, and verification commands. Anything outside the brief is fair
   game for the agent to read on its own.

## Shared context (every brief inherits this)

```
Project:    Companion (telaaron/companion)
Branch:     feat/agentic-os-phase-b
Stack:      Python 3.14, FastAPI + uvicorn, SQLite + FTS5, vanilla JS SPA
Pkg mgr:    uv (uv sync, uv run <cmd>)
Lint/type:  uv run ruff format / ruff check / ty check
Tests:      uv run pytest -v --tb=short   (1468+ baseline, all must stay green)
Server:     uv run companion-server (binds 127.0.0.1:8082 by default)
UI:         http://127.0.0.1:8082/ui/
Auth:       Authorization: Bearer $ANTHROPIC_AUTH_TOKEN
```

### Hard rules every agent must follow

- **No `# type: ignore` or `# ty: ignore`.** CI greps for them and fails.
- **`uv run ruff format --check` must pass.** Run `uv run ruff format` before
  committing.
- **Tests cannot regress** — `uv run pytest` must stay green.
- **No bare Claude model ids** in production code (e.g. `claude-3-5-sonnet-20241022`).
  Use prefixed gateway ids like `deepseek/deepseek-v4-flash` or
  `open_router/anthropic/claude-3.5-sonnet`.
- **One feature per PR.** Branch off `feat/agentic-os-phase-b` as
  `feat/<short-slug>`. Open the PR back to that branch.
- **Touch only the files listed**, plus tests + obvious wiring. Refactors are
  out of scope — file the bigger cleanup as a follow-up.
- **No half-implementations.** If acceptance criteria can't be met, document
  the blocker in the PR description and stop — don't merge a stub.

### Repository tour (the 60-second version)

```
api/                    FastAPI app + agent loop
  app.py                ASGI entry, mounts routers + static UI
  routes.py             Anthropic-compatible /v1/messages proxy
  dashboard_routes.py   Internal /v1/* endpoints for the UI
  runtime.py            Startup/shutdown, MCP bootstrap, etc.
  datastore.py          SQLite schema + helpers (sessions, jobs, usage…)
  pricing.py            $/Mtok pricing snapshot
  agent/
    agent_loop.py       Multi-turn loop, tool dispatch
    jobs.py             Background asyncio jobs + SSE pump
    mcp.py              JSON-RPC MCP client + auto-discovery
    plugins.py          YAML plugin loader
    extras/             search.py, etc.
  ui_static/
    app.js              SPA (vanilla JS, no build step)
    styles.css          Dark-first theme, CSS custom properties
    index.html          shell

config/settings.py      pydantic-settings (use validation_alias=dict-form)
core/model_router.py    splits provider/<model> gateway ids
plugins/                YAML plugin specs (obsidian, mcp_servers, …)
tests/                  pytest suite (~1468 tests; xdist parallel)
docs/                   user-facing docs
ROADMAP.md              source of truth for what ships next
```

### Verification ritual (run before every PR)

```bash
uv run ruff format --check
uv run ruff check
uv run ty check
uv run pytest -v --tb=short
```

CI runs all four. If any fail locally, fix before pushing.

---

## Index

| # | Phase | Title | Brief |
|---|-------|-------|-------|
| 1.1 | Polish | Per-chat usage actually populates | [01-usage-populates.md](01-usage-populates.md) |
| 1.3 | Polish | File preview side panel | [02-file-preview-panel.md](02-file-preview-panel.md) |
| 1.4 | Polish | Slack / Discord notifier for finished long jobs | [03-job-notifier.md](03-job-notifier.md) |
| 1.5 | Polish | Auto-name verification + retry | [04-auto-name-retry.md](04-auto-name-retry.md) |
| 1.6 | Polish | Provider "Test connection" buttons | [05-provider-test-buttons.md](05-provider-test-buttons.md) |
| 2.1 | Big | Memory + RAG over notes / vault / files | [06-rag-indexer.md](06-rag-indexer.md) |
| 2.2 | Big | Skill marketplace | [07-skill-marketplace.md](07-skill-marketplace.md) |
| 2.3 | Big | Multi-user (light) | [08-multi-user.md](08-multi-user.md) |
| 2.4 | Big | Tauri desktop wrap | [09-tauri-wrap.md](09-tauri-wrap.md) |
| 2.5 | Big | Voice mode | [10-voice-mode.md](10-voice-mode.md) |
| 2.6 | Big | Cross-session pinned memories | [11-pinned-memories.md](11-pinned-memories.md) |
| 3.1 | Self-improve | Insights page | [12-insights-page.md](12-insights-page.md) |
| 3.2 | Self-improve | Daily journal | [13-daily-journal.md](13-daily-journal.md) |
| 3.3 | Self-improve | Self-modification with confirmation | [14-self-modification.md](14-self-modification.md) |
| 4.2 | Distribution | Plugin registry | [15-plugin-registry.md](15-plugin-registry.md) |
| 4.3 | Distribution | Discord bot wrapper | [16-discord-bot.md](16-discord-bot.md) |
| 5.1 | Remote | Auth layer (Bearer + Cloudflare Access JWT) | [17-auth-layer.md](17-auth-layer.md) |
| 5.2 | Remote | Cloudflare Tunnel quickstart | [18-cloudflare-tunnel.md](18-cloudflare-tunnel.md) |
| 5.3 | Remote | Tailscale + headless server image | [19-tailscale-headless.md](19-tailscale-headless.md) |
| 6.1 | Routines | Scheduler + datastore | [20-routine-scheduler.md](20-routine-scheduler.md) |
| 6.2 | Routines | Routines UI | [21-routines-ui.md](21-routines-ui.md) |
| 6.3 | Routines | Routine template library | [22-routine-templates.md](22-routine-templates.md) |
