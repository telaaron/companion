# 6.1 — Routine scheduler + datastore

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Persist routines in SQLite and fire them on schedule via an in-process
scheduler. Each fire enqueues a normal background agent job that shows
up in the existing Jobs UI.

## Files

- `api/datastore.py`:
  - `routines` table:
    `(id, name, description, trigger_type, trigger_config (json),
    payload (json), enabled, project_id, user_id, last_run_ms,
    next_run_ms, created_at)`.
  - `routine_runs` table:
    `(id, routine_id, job_id, status, started_at, finished_at)`.
  - Helpers: `create_routine`, `update_routine`, `list_routines`,
    `delete_routine`, `record_routine_run`.
- `api/agent/routines.py` (new):
  - `RoutineScheduler` class. Tick loop runs every 30 s. On each tick:
    - List enabled routines with `next_run_ms <= now`.
    - For each, compute `next_run_ms` from the cron expression (use
      `croniter`), enqueue a job via `api/agent/jobs.enqueue(...)` with
      payload + `metadata={"source": f"routine:{id}"}`, then update
      `last_run_ms` + `next_run_ms`.
  - Trigger types: `cron`, `manual`, `webhook`.
- `api/dashboard_routes.py`:
  - `POST /v1/routines/{id}/trigger` — webhook trigger with shared
    secret in `trigger_config.secret`.
- `api/runtime.py` — `RoutineScheduler.start()` in `startup()`,
  `.stop()` in `shutdown()`.
- `pyproject.toml` — add `croniter` to dependencies.
- `tests/agent/test_routines.py` (new) — create a cron routine with
  `next_run_ms` in the past, tick once, assert a job was enqueued and
  `next_run_ms` advanced.

## Implementation plan

1. Migrations: idempotent `CREATE TABLE IF NOT EXISTS`. Bump
   `PRAGMA user_version`.
2. Scheduler implementation: single asyncio task, `await asyncio.sleep(30)`
   between ticks. Catch + log all exceptions per routine so one bad cron
   doesn't kill the loop.
3. Misfire policy: if `now - next_run_ms > 5 min` (machine slept), fire
   once immediately, then resume normal cadence. Otherwise just fire and
   advance.
4. Webhook: `POST /v1/routines/{id}/trigger` with header
   `X-Routine-Secret`; constant-time compare against
   `trigger_config.secret`.
5. Tests: monkeypatch `time.time()` + `croniter` to control ticks.

## Acceptance

- Create a routine via the SQL helper: `{name: "daily journal",
  trigger_type: "cron", trigger_config: {"expression": "0 9 * * *",
  "tz": "Europe/Berlin"}, payload: {model: "deepseek/deepseek-v4-flash",
  messages: [{"role":"user","content":"Run the morning journal."}]}}`.
- At 09:00 server-local time, a job appears in `/v1/jobs` with
  `metadata.source = "routine:<id>"`.
- Webhook trigger fires immediately when called with the right secret.

## Risks

- Don't double-fire on a server restart that lands inside the same
  minute as a cron tick — guard with `last_run_ms`.
- Cron expressions in the wrong tz silently fire at the wrong time —
  always store + use the explicit `tz` field.

## Verify

```bash
uv run pytest tests/agent/test_routines.py -v
uv run pytest -v --tb=short
```
