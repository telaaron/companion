# 3.1 — Insights page

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

`/ui` → Insights renders a daily reflection over the audit log + usage
events: most-used tools, tools that erroneded most, provider cost
breakdown, and actionable suggestion cards.

## Files

- `api/agent/insights.py` (new) — `compute_insights(since_ts)` queries
  `audit_log` + `usage_events` and returns a typed `Insights` dict:
  ```python
  {
    "tools": [{"name": str, "calls": int, "avg_ms": int, "errors": int}],
    "providers": [{"provider": str, "cost_usd": float, "tokens": int}],
    "suggestions": [{"id": str, "title": str, "body": str, "action": dict | None}],
  }
  ```
- `api/dashboard_routes.py` — `GET /v1/insights?range=7d` returns the
  dict. Cache 5 min in process memory.
- `api/ui_static/app.js` — new "Insights" page with three cards (tools,
  providers, suggestions). Each suggestion card has an optional
  one-click "Apply" that PUTs the env change via `/v1/settings`.
- `cli/insight_cron.py` (optional v2) — nightly run writes a snapshot
  to `~/.cache/free-claude-code/insights/YYYY-MM-DD.json`.
- `tests/agent/test_insights.py` (new) — seed audit_log + usage_events,
  assert the computed shape.

## Implementation plan

1. Implement `compute_insights`. Use raw SQL aggregations (already-indexed
   `audit_log.created_at`, `usage_events.created_at`).
2. Suggestions heuristics (rule-based v1):
   - If a Bash command repeats > 20× in 7 d → suggest a pin.
   - If a provider has > $5 cost and a cheaper one is configured →
     suggest routing default to the cheaper one.
   - If a tool's error rate > 10% → suggest denylist tweak.
3. Cache: `functools.lru_cache(maxsize=8)` keyed by `range` won't work
   because results are time-sensitive — use a TTLCache (`cachetools`) or
   simple dict with `expires_at`.
4. UI: cards in a 3-col grid (collapses to 1-col below 900 px). Number
   tickers use `Intl.NumberFormat`. Charts not needed for v1; numbers
   only.

## Acceptance

- Visit Insights → see real numbers across last 7 d.
- At least one suggestion card renders when the audit log has enough
  data to trigger a rule.
- Clicking "Apply" on a suggestion that targets a setting actually
  updates `.env` (via the existing admin-config writer).

## Risks

- Aggregation queries on large `audit_log` tables can be slow — add
  composite indexes `(created_at, tool)` and `(created_at, provider)`.
- Suggestion text quality is rule-based for v1; LLM-powered suggestions
  are a v2 follow-up.

## Verify

```bash
uv run pytest tests/agent/test_insights.py -v
uv run pytest -v --tb=short
```
