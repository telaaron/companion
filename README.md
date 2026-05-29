<div align="center">

# Companion

**Your own AI workstation. Self-hosted. Any provider.**

One install. One URL. Chat, code, edit files, run commands — all with the
model of your choice.

[![Release](https://img.shields.io/github/v/release/telaaron/companion?sort=semver&display_name=tag&label=latest)](https://github.com/telaaron/companion/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/telaaron/companion/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/telaaron/companion/actions/workflows/tests.yml)
[![Discussions](https://img.shields.io/github/discussions/telaaron/companion)](https://github.com/telaaron/companion/discussions)
[![Stars](https://img.shields.io/github/stars/telaaron/companion?style=social)](https://github.com/telaaron/companion/stargazers)

[Download](#download) · [Quickstart](#quickstart) · [Features](#features) · [Docs](#docs) · [Roadmap](docs/ROADMAP.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## Download

Pre-built desktop apps live on the
[Releases page](https://github.com/telaaron/companion/releases/latest) —
double-click to install. No Python, no terminal, no setup.

| Platform | File |
|---|---|
| macOS (Apple Silicon) | `Companion_*_aarch64.dmg` |
| Windows 10/11 (x64) | `Companion_*_x64-setup.exe` |
| Linux (x64) | `companion_*_amd64.AppImage` |

See [docs/installing.md](docs/installing.md) for Gatekeeper notes on
macOS. The bundled auto-updater silently rolls forward whenever a new
release ships, so you only ever install once.

---

## Why

Most AI tools lock you into one vendor and one chat box. Companion is the
opposite:

- **Use any model** — DeepSeek, OpenRouter, NVIDIA NIM, Z.ai, Anthropic,
  any OpenAI-compatible endpoint. Switch with one click.
- **Real workspace** — the model can read, edit, and run shell commands
  inside a folder you choose. Sandboxed by default.
- **One URL** — `http://127.0.0.1:8082/ui/` — chat, projects, usage,
  file edits, audit log, settings, all in one place.
- **Background-safe jobs** — start a long task, close the tab, come
  back. The agent kept running.
- **Local-first** — your conversations + workspace stay on your machine.
  No analytics, no account, no telemetry.

Free, MIT-licensed, runs on your laptop.

---

## Quickstart

### From the installer (recommended)

1. Download from [Releases](https://github.com/telaaron/companion/releases/latest)
2. Double-click the DMG / EXE / AppImage
3. Open Companion
4. Run the **Setup wizard** in Settings to plug in a provider API key

### From source

You need **Python 3.14** + **[uv](https://github.com/astral-sh/uv)**.

```bash
# 1. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone + install
git clone https://github.com/telaaron/companion.git
cd companion
uv sync --dev
uv python install 3.14

# 3. Run
uv run companion-server
```

Open <http://127.0.0.1:8082/ui/> in your browser.

For frontend development with hot-reload, see
[CONTRIBUTING.md → Day-to-day](CONTRIBUTING.md#day-to-day).

---

## Features

| Area | What you can do |
|---|---|
| **Chat** | Multi-turn conversations with SSE streaming, markdown rendering, tool-block cards, copy + regenerate, project-aware system prompts. |
| **Projects** | Group sessions, pin memories, scope file access to a workspace folder, colour-tag for sidebar grouping. |
| **Models** | DeepSeek, OpenRouter, NVIDIA NIM, Z.ai, OpenAI-compatible, Anthropic via API. Per-session model picker. |
| **Tools** | Read, Write, Edit, Glob, Grep, LS, Bash. Sandboxed inside the project workspace; denylist + rate-limits enforced. |
| **MCP** | Pass-through to any MCP server (Obsidian, Firecrawl, custom). Tools surface automatically in the agent loop. |
| **Voice** | Browser-side recording → backend Whisper transcription → composer. |
| **Routines** | Cron-scheduled agent runs with template library and run history. |
| **File edits** | Every file the agent touches gets recorded; click through to a side-by-side preview. |
| **Usage** | Per-day / week / month cost rollups grouped by provider + model. |
| **Audit log** | Every config change, env upsert, and tool call gets logged. |
| **Memory** | Long-term preferences the agent reads on every session boot. |
| **Skills** | Companion-side equivalent of Claude Code skills. Local + catalog (WIP). |
| **Insights** | Usage-pattern suggestions for cost savings (WIP). |

---

## Architecture

```
┌────────────────────┐
│ Tauri desktop app  │  ← signed installer, auto-updater
│  (Rust + WebView)  │
└────────┬───────────┘
         │ spawns
         ▼
┌────────────────────┐       ┌─────────────────────┐
│ companion-bin      │──────▶│ SQLite + FTS5       │
│  (PyInstaller of   │       │ ~/Library/.../db    │
│   FastAPI + agent) │       └─────────────────────┘
└────────┬───────────┘
         │ serves
         ▼
┌────────────────────┐
│ SvelteKit /ui      │  ← Svelte 5 runes, lucide icons, marked
└────────────────────┘
```

- **Backend**: FastAPI proxy of the Anthropic-Messages API, multi-provider
  router, agent loop with sandboxed tool dispatch.
- **Frontend**: SvelteKit static SPA mounted at `/ui` by the FastAPI app.
- **Desktop shell**: Tauri 2 bundles the PyInstaller-frozen sidecar and
  the SvelteKit build into a single signed installer per OS.

---

## Docs

| Where | What |
|---|---|
| [README.md](README.md) | You're here. |
| [docs/installing.md](docs/installing.md) | Gatekeeper, AppImage permissions, first-launch tips. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What's planned. Up-vote with 👍. |
| [docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md) | Versioning, channels, hot-fix flow. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, commit style, PR flow. |
| [SECURITY.md](SECURITY.md) | How to report an issue privately. |
| [CHANGELOG.md](CHANGELOG.md) | Every version's diff. |
| [docs/agent-prompts/](docs/agent-prompts/) | Self-contained briefs per feature area. |

---

## Community

- **Discussions** — <https://github.com/telaaron/companion/discussions>
  (Q&A, ideas, showcase)
- **Issues** — <https://github.com/telaaron/companion/issues> (bugs,
  actionable features)
- **Reddit** — <https://reddit.com/r/companion-app> (community-run)

---

## Sponsor

Companion is free + MIT-licensed. If it saves you time, a donation
covers cloud-API credits for testing new models and build-time for the
next release.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-pink?logo=github)](https://github.com/sponsors/telaaron)
[![Buy a Ko-fi](https://img.shields.io/badge/Ko--fi-Buy_a_coffee-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/telaaron)

---

## License

[MIT](LICENSE) © 2026 Aaron Pfutzner and Companion contributors.
