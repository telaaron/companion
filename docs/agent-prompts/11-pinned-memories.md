# 2.6 — Cross-session pinned memories

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Pin a turn (or chunk of chat) to a project as a "memory" so every future
chat in the same project sees it as system context.

## Files

- `api/datastore.py` — `project_memories` table:
  `(id, project_id, content, source_session_id, created_at, user_id)`.
  Helpers: `pin_memory(...)`, `list_memories(project_id)`,
  `delete_memory(id)`.
- `api/dashboard_routes.py` — `POST/GET/DELETE /v1/projects/{id}/memories`.
- `api/agent/jobs.py::_run_job` — when `project_id` is set, prepend
  pinned memories (joined as bullets) to the system prompt with a header
  like *"Memory you must respect for this project:"*.
- `api/ui_static/app.js` — context menu on assistant messages → "Pin to
  project memory". New "Memories" panel inside the project drawer.
- `tests/api/test_pinned_memories.py` (new) — pin, list, system-prompt
  contains the bullets, delete.

## Implementation plan

1. Migration: add the table + indexes `(project_id)` and
   `(project_id, created_at)`. Idempotent via `IF NOT EXISTS`.
2. Endpoints: simple CRUD. Validate content length (max 4000 chars).
3. System-prompt assembly: pull memories on every job start. Hardcoded
   header text. Skip if list is empty (don't pollute prompt with empty
   headers).
4. UI: right-click (or kebab menu) on assistant bubble → "Pin to project
   memory". Show toast on success.
5. Memories panel: list + inline edit + delete. Show created_at + source
   session deep link.

## Acceptance

- Pin *"the API key for staging is in 1Password"* → start a new chat in
  the same project → ask *"how do I get the staging key?"* → agent
  references the pin without being told.
- Delete the pin → next chat no longer references it.

## Risks

- System-prompt bloat over time. Soft-cap at 10 memories or 4000 chars
  combined; if exceeded, show a warning and stop accepting new pins.

## Verify

```bash
uv run pytest tests/api/test_pinned_memories.py -v
uv run pytest -v --tb=short
```
