# 2.3 — Multi-user (light)

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Each device or browser session gets its own `user_id` tied to projects +
sessions + jobs. Two laptops hitting the same proxy can't see each
other's chats.

## Files

- `api/datastore.py` — add `user_id TEXT` column to `projects`,
  `sessions`, `agent_jobs`, `audit_log`, `usage_events`. Migration runs
  on startup (`PRAGMA user_version` + idempotent ALTER TABLE).
- `api/dependencies.py` — `get_current_user_id(request)` derives the user
  from header `x-companion-user`, or maps from the Bearer token via
  `Settings.companion_users` (CSV of `token=user_id`), or falls back to
  `"default"`.
- All list/create endpoints in `api/dashboard_routes.py` — accept
  `user_id` from DI, scope every query.
- `api/agent/jobs.py` — write `user_id` on insert; ignore foreign jobs in
  the list endpoint.
- `config/settings.py` — `companion_users` (str CSV, default `""`).
- `tests/api/test_multi_user.py` (new) — Alice and Bob can't see each
  other's data.

## Implementation plan

1. SQLite migration: read `PRAGMA user_version`. If < target, run
   `ALTER TABLE projects ADD COLUMN user_id TEXT DEFAULT 'default'` (and
   for the other tables). Set the new `user_version`. Idempotent on
   re-run.
2. Indexes: add `(user_id, updated_at)` for sessions + projects.
3. DI: `Annotated[str, Depends(get_current_user_id)]` injected into every
   endpoint. Use it in the WHERE clauses of list queries and in INSERT.
4. Default-mode behaviour: when `companion_users` is empty and no header,
   the user_id is `"default"` — existing single-user data is preserved as
   that one bucket.
5. Per-user workspace root: optional `AGENT_DEFAULT_WORKSPACE_<user_id>`
   env override; falls back to the shared one.
6. Tests: Alice (with `x-companion-user: alice`) creates a session; Bob
   lists sessions → empty. Audit log per-user. Migration is no-op on
   re-run.

## Acceptance

- Alice and Bob simultaneously hit the same proxy, see only their own
  sessions and projects.
- Audit log + usage events are scoped per user.
- Existing single-user data still works under the `"default"` bucket.

## Risks

- Breaking existing deployments. The migration MUST default everything
  to `"default"` and remain a no-op when `companion_users` is empty.
- Sidebar header should display the current user — wire it from the new
  `/v1/me` endpoint (also new in this PR).

## Verify

```bash
uv run pytest tests/api/test_multi_user.py -v
uv run pytest -v --tb=short
```
