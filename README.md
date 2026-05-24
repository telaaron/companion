<div align="center">

# ✦ companion

**Your own AI workstation. Runs locally. Works with any provider.**

One install. One URL. Chat, code, edit files, run commands — all with the
model of your choice (DeepSeek, OpenRouter, Claude via API, …).

[Download](#download) · [Install from source](#install-in-3-commands) · [First run](#first-run) · [What it can do](#what-it-can-do) · [Roadmap](#roadmap)

</div>

---

## Download

Pre-built desktop apps are published on the
[**Releases page**](https://github.com/telaaron/companion/releases/latest) —
double-click to install. No Python, no terminal, no setup.

| Platform | File |
|----------|------|
| macOS (Apple Silicon) | `Companion-*-aarch64-apple-darwin.dmg` |
| Windows 10/11 (x64) | `Companion-*-x86_64-pc-windows-msvc.exe` |
| Linux (x64) | `Companion-*-x86_64-unknown-linux-gnu.AppImage` |

See [docs/installing.md](docs/installing.md) for first-launch + Gatekeeper notes.

---

## Why

Most AI tools lock you into one provider and one chat box. This is the
opposite:

* **Use any model** — DeepSeek, OpenRouter, NVIDIA NIM, Z.ai, OpenAI, Claude.
  Switch with one click.
* **Real workspace** — the AI can read, edit, run shell commands inside a
  folder you choose. Sandbox keeps it contained.
* **One URL** — http://127.0.0.1:8082/ui — chat, projects, usage, file
  edits, audit log, settings, all in one place.
* **Background-safe** — start a long task, close the tab, come back later.
  The job kept running.

It's free, runs on your laptop, and the data never leaves your machine.

---

## Install in 3 commands

Requires **macOS or Linux**, **Python 3.14**, and **[uv](https://github.com/astral-sh/uv)**.

```bash
# 1. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone + install
git clone https://github.com/telaaron/companion.git
cd companion
uv python install 3.14 && uv sync

# 3. Start the server
uv run companion-server
```

Open **http://127.0.0.1:8082/ui** in your browser.

---

## First run

The dashboard opens to the **Setup wizard**. Four steps:

| Step | What |
|---|---|
| 1. Welcome | Read what's about to happen |
| 2. Provider | Pick DeepSeek / OpenRouter / NVIDIA / Z.ai. Paste API key. |
| 3. Agent mode | Tick "enable" + pick a folder ("Browse…" → Desktop / Documents / your repo) |
| 4. Done | First test — try "What's in this folder?" |

That's it.

> **Tip:** the wizard writes to `~/.config/companion/.env` and reloads
> live — no restart needed.

---

## What it can do

### Chat with any AI

Pick the provider in the dropdown above the chat. Each conversation is
saved locally. Sessions auto-name themselves after the first reply.

### Files & shell

When agent mode is on, the AI can:

* **Read** any file in your workspace
* **Write / Edit** files (changes show as a diff in the dashboard)
* **Run shell commands** (`mkdir`, `git status`, `gh repo list`, anything)
* **Search** with `grep` / `glob`
* **Browse directories** with `ls`

All inside the folder you chose. No path can escape it.

### Projects

Group related conversations under a project. Each project gets:

* **A workspace** (the folder the AI works in)
* **A brief** ("You're working on my SvelteKit news site, …") — sent
  with every chat in this project
* **A color**

Sessions in the sidebar are grouped by project. Search across all of them.

### Cloud CLIs

Set tokens in **Settings → API tokens (cloud)**:

| Token | What it unlocks |
|---|---|
| `GITHUB_TOKEN` | "Push my changes to a new branch" |
| `VERCEL_TOKEN` | "Deploy this to a preview" |
| `SUPABASE_*` | Manage Supabase projects |

The AI calls `gh`, `vercel`, `supabase` via the shell tool with the token
in the env.

### Background jobs

Start a long task ("read all 200 markdown notes and summarize"). Close
the tab. Open the dashboard tomorrow. The job ran to completion. Stream
catches up via SSE replay.

### Plugins

Drop a YAML in `plugins/`:

```yaml
name: my-vault
kind: obsidian_vault
vault_path: /Users/me/Documents/Obsidian Vault
```

Restart. The AI now has `ObsidianMyVaultList`, `…Read`, `…Append` tools
to read and write your notes.

---

## Pages

| Sidebar | What it shows |
|---|---|
| ◇ Chat | Conversations. Sidebar groups by project, search across all. |
| ▣ Projects | Create / edit projects. Workspace picker, system prompt. |
| ↗ Usage | Token spend, request volume, per-provider breakdown |
| ≡ File edits | Every Write/Edit the AI did, with click-to-expand diff |
| ◐ Audit log | Tool-call audit, env edits, system events |
| ⚿ Env vault | All env vars editable inline. Secrets masked. |
| ✎ Root files | AGENTS.md / CLAUDE.md / README.md inline editor |
| ★ Skills | Skill library (browse) |
| ⚙ Settings | Live config + capabilities + setup wizard button |

---

## Use it from Claude Code CLI

Already have the Claude Code CLI installed? Set it to talk to your local
proxy:

```bash
ds   # alias for: launch Claude Code through this proxy
```

Or manually:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8082 \
ANTHROPIC_AUTH_TOKEN=$(grep ANTHROPIC_AUTH_TOKEN ~/.config/companion/.env | cut -d= -f2) \
claude
```

Same conversation history, same workspace, same tools — but on the
terminal.

---

## Common tasks

**Switch model on the fly**
> Settings → Models → MODEL → click ✎ → paste `open_router/anthropic/claude-3.5-sonnet` → Save

**Connect GitHub**
> Settings → API tokens (cloud) → GITHUB_TOKEN → click ✎ → paste `ghp_…` → Save
> Then in chat: *"Push my changes to a new branch called feat/x"*

**Create a project**
> Projects → + new → Name + workspace path (Browse…) + brief → Create

**Stop a runaway response**
> Click the red **■ Stop** button in the assistant message

**See what the AI changed**
> File edits → click any row → unified diff shows additions and deletions

---

## Configuration

All settings live in `~/.config/companion/.env`. Edit them through
**Env vault** in the dashboard or directly in the file.

Most-asked vars:

| Var | Default | What |
|---|---|---|
| `MODEL` | `deepseek/deepseek-v4-flash` | Default chat model |
| `AGENT_MODE_ENABLED` | `false` | Turn on the workspace tools |
| `AGENT_DEFAULT_WORKSPACE` | (empty) | Folder the AI can touch |
| `AGENT_BASH_DENYLIST` | (empty) | Block dangerous shell commands |
| `ANTHROPIC_AUTH_TOKEN` | (empty) | Token to authenticate the dashboard |

The full list is in [`.env.example`](.env.example).

---

## How it works

```
Your browser ─┐
              │
Your terminal ─┼─→  http://127.0.0.1:8082  ─→  Provider (DeepSeek/OpenRouter/…)
              │         │
Claude Code ─┘          │
                        ↓
                   Your workspace
                   (sandboxed)
```

The proxy speaks the Anthropic Messages API, so any Anthropic-compatible
client works. The dashboard, the agent loop, the audit log are all
served by the same Python process.

For the deeper architecture: see [`AGENTS.md`](AGENTS.md) and the
`api/agent/` package.

---

## Roadmap

What's already built ✓

- ✓ Multi-provider routing (DeepSeek, OpenRouter, NIM, Z.ai, …)
- ✓ Server-side agent loop with workspace sandbox
- ✓ Background jobs that survive tab close
- ✓ Project workspaces with shared briefs
- ✓ Live capabilities scanner + setup wizard
- ✓ Cloud CLI passthrough (gh, vercel, supabase)
- ✓ File edit diff renderer
- ✓ Plugin loader (Obsidian vault)
- ✓ Memory + RAG search across past chats

What's next 🔜

- 🔜 MCP server passthrough (Linear, Notion, …)
- 🔜 Slack / Discord notifications when a long job finishes
- 🔜 Skill marketplace (install/upgrade Anthropic-style skills)
- 🔜 Mobile-friendly chat view

---

## Stop the server

```bash
kill $(lsof -ti:8082)
```

Want it as a launchd / systemd service? Open an issue.

---

## Built with

Python · FastAPI · uv · SQLite · pytest · loguru · ruff · ty · vanilla JS

Originally a fork of [Alishahryar1/companion](https://github.com/Alishahryar1/companion) —
massive hat tip for the foundation. Everything in `api/agent/`,
`core/tools/`, the SPA shell, plugins, and the dashboard is the next
generation.

---

## License

MIT. Use it, fork it, ship it.
