# 6.2 — Routines UI

> Inherits shared context from [README.md](README.md). Read it first.
>
> **Depends on [20-routine-scheduler.md](20-routine-scheduler.md) being merged first.**

## Goal

`/ui` → Routines page with full CRUD, "Run now" button, per-routine run
history, and a cron-expression preview ("Next: in 3 h 12 m").

## Files

- `api/dashboard_routes.py`:
  - `GET /v1/routines`
  - `POST /v1/routines`
  - `PATCH /v1/routines/{id}`
  - `DELETE /v1/routines/{id}`
  - `POST /v1/routines/{id}/run` (manual fire)
  - `GET /v1/routines/{id}/runs` (history with pagination)
- `api/ui_static/app.js` — new "Routines" page registered with the
  page-router. Components:
  - Table of routines with status, trigger summary, next-fire chip.
  - Drawer-form for create / edit: name, description, trigger picker
    (cron / manual / webhook), payload editor (textarea with JSON
    schema validation), project select, enabled toggle.
  - Cron preview chip — call `/v1/routines/preview-cron?expr=...&tz=...`
    server-side that returns next 5 fire times (use `croniter`).
  - "Run now" button on each row → POST → toast → link to the new job.
- `api/ui_static/styles.css` — table styling matching existing tables;
  drawer reuses `.drawer` styles.
- `tests/api/test_routines_endpoints.py` (new).

## Implementation plan

1. Endpoints in `dashboard_routes.py`. All routes require the existing
   `require_api_key` dep and scope by `user_id`.
2. UI page module: keep state local to the page (no global router
   state). Routines list refetched on focus.
3. Trigger picker: tabs (cron / manual / webhook). Cron tab includes
   common presets ("Every morning at 9", "Every Monday", "Hourly") that
   set the underlying expression.
4. Payload editor: textarea with a JSON-schema-validated parse on blur;
   bad JSON disables the save button with an inline error.
5. Run history table per routine, paginated 25 at a time.

## Acceptance

- Build a routine end-to-end from the UI: cron `*/5 * * * *`, payload
  `{model: ..., messages: [...]}`, enabled.
- Wait 5 min → row updates with `last_run_ms` + a new run appears in the
  history.
- "Run now" fires immediately, deep-links to the spawned job in the
  Jobs page.

## Risks

- Don't accept arbitrary JSON in `payload` — validate against the same
  schema the jobs endpoint expects (`model`, `messages`, optional
  `max_tokens`, `project_id`).
- Disable the page entirely if `RoutineScheduler` is not running (read
  status from `/v1/runtime/status`).

## Verify

```bash
uv run pytest tests/api/test_routines_endpoints.py -v
uv run pytest -v --tb=short
```
