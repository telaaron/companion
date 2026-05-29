# Roadmap

Companion's plan, kept honest. Top-level milestones live here; the live
working board is the
[GitHub Project — Companion Roadmap](https://github.com/users/telaaron/projects).

**Last updated**: 2026-05-29
**Current release**: v1.1.4 — see [CHANGELOG.md](../CHANGELOG.md)

---

## Now (in flight)

What's being actively worked on for the next minor release.

### Targeting v1.2.0

All planned v1.2.0 polish has landed — see **Done**. Next batch is being
pulled from **Next** below.

---

## Next

Probably v1.3 — anything below this line is still up for discussion.

- **Project polish**: color-tinted cards, per-project quick-chat button,
  pin/unpin from sidebar
- **Routines V2**: create-drawer with live cron preview, run history
  with output viewer
- **File-edits**: unified-diff viewer, per-project grouping, search
- **Root-files**: dynamic allow-list rooted in the active project's
  workspace
- **Auto-Insights**: rule-based suggestions from `usage_events` +
  `audit_log` (e.g. "you sent 80 % of your tokens to Opus — switch 60 %
  to Sonnet to save $X/mo")

---

## Later

Bigger bets, less locked in.

### Skills marketplace

Public skills catalog hosted in a separate `companion-skills` repo with a
`catalog.json` manifest. UI: search + install with one click. Optional
AI advisor: "I need something for X" → LLM filters the catalog and asks
clarifying questions. Compatible with `.claude/skills/*.md` so anything
that runs in Claude Code carries over.

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

- ✅ Settings → dedicated Voice card
- ✅ Env vault → per-row copy-to-clipboard button
- ✅ Brand splash screen for cold-start bootstrap
- ✅ Global search (Cmd+K) — `CommandPalette.svelte` + `GET /v1/search`
  over sessions, messages, files, audit, memories
- ✅ Image generation — `Imagine(prompt)` tool + inline render in the
  chat body
- ✅ Delete buttons on chat / project / routine rows (custom modal,
  Tauri-WebKit `confirm()` workaround)
- ✅ Auto-rename sessions via `/v1/sessions/{id}/auto-rename`
- ✅ Tool-block file-preview side panel (`⏺ Read(…)` → slide-in)
- ✅ Model-picker polling against `/v1/models/upstream`
- ✅ Voice input (mic toggle) + Setup wizard with a Voice step
- ✅ Range pickers on Usage (24h / 7d / 30d)
- ✅ Auto-updater download-progress indicator (Rust side)
- ✅ Pinned-memory CRUD (delete endpoint wired)
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
