# 3.2 — Daily journal

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Each morning, Companion asks the user 2-3 short questions ("What's your
focus today? Anything you want me to remember?"), writes the answers to
the configured Obsidian vault (or `~/journal.md`), and references the
journal in future chats.

## Files

- `api/agent/journal.py` (new) — daily-banner scheduler. When the user
  loads the dashboard for the first time on day N and no journal entry
  exists for N yet, surface a banner via a new flag on `GET /v1/me`.
- `api/dashboard_routes.py`:
  - `GET /v1/me` returns `{user_id, has_today_journal: bool, ...}`.
  - `POST /v1/journal` `{date: YYYY-MM-DD, answers: dict}` writes the
    entry to the vault.
  - `GET /v1/journal?date=...` returns the entry as markdown.
- `plugins/journal.yaml` — new plugin kind binding the vault target.
  Schema: `{kind: "journal", vault_root: "...", subpath: "Companion/Journal"}`.
- `api/ui_static/app.js` — when `has_today_journal=false`, show a slim
  banner above the chat list: *"5-min journal? answer 3 questions"*.
  Click → modal with the 3 questions → submit → POST → banner dismisses
  for the day.
- `tests/api/test_journal.py` (new) — POST writes to a tmp vault path,
  GET reads it back, the banner flag flips.

## Implementation plan

1. Define the entry markdown format:
   ```
   ---
   date: 2026-05-16
   ---
   ## Focus today
   ...
   ## Remember
   ...
   ## Mood
   ...
   ```
2. Default questions are configurable via `Settings.journal_questions`
   (list of `{id, prompt, type}`). Default ships with focus/remember/mood.
3. Banner-state computation: read `vault_root/Companion/Journal/{today}.md`
   existence. No DB row needed.
4. UI: modal with one textarea per question + submit. Show a "Skip today"
   button that creates an empty stub so the banner doesn't reappear.
5. Search integration (already works via the indexer): once entries land
   in the vault, the existing Search tool finds them. Document in the
   journal user docs.

## Acceptance

- Day N+1 morning, dashboard shows the banner. Submit answers → entry
  appears at `vault/Companion/Journal/2026-05-16.md`.
- Later in the same day → no banner (entry exists).
- Chat: *"what did I plan yesterday?"* → agent calls Search → quotes
  the prior day's entry.

## Risks

- Don't nag. Banner is dismissible for the day even without filling it
  in.
- Vault path must use the existing Obsidian plugin's resolver to avoid
  iCloud-path quirks (`com~apple~CloudDocs`).

## Verify

```bash
uv run pytest tests/api/test_journal.py -v
uv run pytest -v --tb=short
```
