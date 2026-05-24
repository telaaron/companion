# 1.1 — Per-chat usage actually populates

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

The `.chat-usage` pill in the chat header shows real token + cost totals
instead of `0 tok · $0.000`. Backend endpoint `/v1/sessions/{sid}/usage`
already exists and the UI already calls it — but the background-jobs path
(every chat reply since the migration to jobs) never records usage events,
so the table stays empty.

## Files

- `api/agent/jobs.py` — instrument `_run_job` to extract usage from the
  upstream `message_delta` / final-message event and call
  `datastore.record_usage_event(...)` once per turn.
- `api/datastore.py::record_usage_event` — already exists; takes
  `kind, provider, model, input_tokens, output_tokens, cost_usd,
  duration_ms, project_id, session_id, request_id, metadata`.
- `api/pricing.py::pricing_snapshot` — already maps model → $/Mtok.
- `core/model_router.py` — split provider/model so pricing lookup uses the
  upstream model id, not the gateway prefix.
- `tests/api/test_agent_jobs_usage.py` (new) — assert usage row appears
  after a job completes.

## Implementation plan

1. In `_run_job`, accumulate usage from each agent turn. The upstream
   stream emits `message_delta` events whose `usage` block carries
   `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
   `cache_read_input_tokens`. Sum them across all turns of the job.
2. On terminal status (`done` / `error`), call `record_usage_event(...)`
   with `kind="chat"`, the resolved model, and the totals.
3. Compute `cost_usd` via `pricing_snapshot()` keyed by the **upstream**
   model id (not the gateway prefix). Default to `0.0` if unknown.
4. Pass `session_id` and `project_id` straight from the job's metadata.
5. Floats / missing fields from upstream → default to 0 with `int(...)`
   coercion. Do not crash the job on a malformed `usage` block.
6. Add a unit test using the fake provider pattern in
   `tests/api/test_e2e_agent_mode.py`. Run a job, then query
   `datastore.session_usage(session_id)` and assert the totals match.

## Acceptance

- Send 3 messages in a chat → refresh → pill shows non-zero `tok` and
  `$0.x` totals.
- `GET /v1/sessions/{sid}/usage` returns matching numbers.
- `uv run pytest tests/api/test_agent_jobs_usage.py -v` passes.
- Full suite stays green (1468+ tests).

## Risks

- Provider responses vary: some emit `usage` only on the final
  `message_delta`, others incrementally. Sum defensively.
- Cost calc keyed on the gateway id silently returns 0. Always re-route
  through `model_router.split()` first.

## Verify

```bash
uv run ruff format --check && uv run ruff check && uv run ty check
uv run pytest -v --tb=short
uv run companion-server &
# open http://127.0.0.1:8082/ui/, send 3 messages, observe pill updates
```
