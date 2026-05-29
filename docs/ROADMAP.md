# Roadmap

Companion's plan, kept honest. Top-level milestones live here; the live
working board is the
[GitHub Project — Companion Roadmap](https://github.com/users/telaaron/projects).

**Last updated**: 2026-05-28
**Current release**: v1.1.4 — see [CHANGELOG.md](../CHANGELOG.md)

---

## Now (in flight)

What's being actively worked on for the next minor release.

### Targeting v1.2.0 — UI polish + missing buttons

- [ ] Delete buttons on chat + project + routine rows (Tauri-WebKit
      `confirm()` is blocked; need a custom modal)
- [ ] Auto-rename session uses the actual title returned from
      `/v1/sessions/{id}/auto-rename`
- [ ] Mic button visibility refresh when `VOICE_NOTE_ENABLED` flips
- [ ] Settings → add a "Voice" card (so Whisper config no longer hides
      in Env vault)
- [ ] Model picker: poll `/v1/models/upstream` until cache is warm
- [ ] Env vault: per-row copy-to-clipboard button
- [ ] Tool-block file-preview side panel (click `⏺ Read(…)` → open
      preview)
- [ ] Memory CRUD (currently 405s — endpoint mismatch)
- [ ] Auto-updater: visible "Checking for updates" / "Downloading 12 of
      39 MB" indicator
- [ ] Splash screen for cold starts so the 25-30 s sidecar bootstrap is
      legible instead of blank

---

## Next

Probably v1.3 — anything below this line is still up for discussion.

- **Project polish**: color-tinted cards, per-project quick-chat button,
  pin/unpin from sidebar
- **Routines V2**: create-drawer with live cron preview, run history
  with output viewer
- **Range pickers** on Usage + Insights (24h / 7d / 30d / custom)
- **File-edits**: unified-diff viewer, per-project grouping, search
- **Root-files**: dynamic allow-list rooted in the active project's
  workspace
- **Auto-Insights**: rule-based suggestions from `usage_events` +
  `audit_log` (e.g. "you sent 80 % of your tokens to Opus — switch 60 %
  to Sonnet to save $X/mo")
- **Setup wizard** (Settings → Setup wizard button is still inert)

---

## Later

Bigger bets, less locked in.

### Global search (Cmd+K)

A single search bar over **everything** — sessions, messages, files in
your workspace, audit events, settings keys, pinned memories. Backend
already has FTS5 (`memory_fts`); we'd add a `GET /v1/search` that fans
out across kinds. Frontend: overlay component (`$lib/CommandPalette.svelte`)
with sections grouped by kind, click → navigate.

### Skills marketplace

Public skills catalog hosted in a separate `companion-skills` repo with a
`catalog.json` manifest. UI: search + install with one click. Optional
AI advisor: "I need something for X" → LLM filters the catalog and asks
clarifying questions. Compatible with `.claude/skills/*.md` so anything
that runs in Claude Code carries over.

### Image generation that actually works

The system prompt mentions an `imagine:` keyword, but there's no tool
behind it yet. Add an `Imagine(prompt)` tool that calls the configured
`IMAGE_GEN_PROVIDER`, stores the result, and renders inline in the chat
body.

### Multi-user / sharing

DB is already user-scoped (`user_id` columns landed in v1.0). What's
missing: an actual sign-in flow, per-user API tokens, shareable
project links, optional cloud sync of memory.

### Auto-updater for the bundled web UI

Right now a release ships a new SvelteKit bundle inside the frozen
`companion-bin`. We could decouple: ship `web/build/` as a separate
artefact that gets fetched lazily, so UI updates don't need a full Tauri
rebuild.

---

## Done

See [CHANGELOG.md](../CHANGELOG.md) for the canonical list. Highlights
since launch:

- ✅ SvelteKit dashboard rewrite (v1.1.0)
- ✅ Tauri auto-updater with signed releases (v1.1.0)
- ✅ Memory-leak hardening for the indexer + MCP stderr (v1.1.0)
- ✅ Multi-OS CI release matrix (v1.0.0)
- ✅ Background-safe agent jobs with SSE replay (v1.0.0)

---

## How to influence the roadmap

- **Up-vote** an item with a 👍 reaction on its issue
- **Propose** a new item via Discussions → Ideas
- **Pick up** an unassigned "Next" item — open a PR; we ship the same week

We don't promise dates. We do promise to keep this file honest.
