# 1.6 — Provider "Test connection" buttons

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Each provider card in Settings has a "Test connection" button that does a
1-token completion and reports OK / error with the upstream message.

## Files

- `api/dashboard_routes.py` — `POST /v1/providers/{id}/test`. Calls
  `provider.stream_response` with
  `[{"role": "user", "content": "hi"}], max_tokens=4`. Returns
  `{ok: bool, duration_ms: int, error: str | None, echo_model: str | None}`.
  5 s timeout.
- `api/ui_static/app.js::renderSettings` — add a button to each provider
  card. Inline spinner during request, green check on success (with
  duration), red X on failure (with truncated error).
- `tests/api/test_provider_test_endpoint.py` (new) — fake provider returns
  text → endpoint returns ok; raises → endpoint returns ok=false with
  error.

## Implementation plan

1. Endpoint loads the provider via the same DI hook (`get_provider`).
   Wraps the call in a per-call asyncio timeout. Catches all exceptions,
   converts to `{ok: false, error: str(e)}` — never 500.
2. UI: button renders next to the API-key input. Disable while in flight.
   After response, swap the button to a status pill: `✓ 142 ms` or
   `✗ 401 invalid api key` (truncated to 80 chars).
3. Status pill auto-fades back to the button after 8 s so users can re-test.

## Acceptance

- Paste a known-bad key → click Test → red pill with the upstream 401
  text.
- Paste a known-good key → click Test → green pill with duration_ms.
- Network unreachable → red pill with the network error string.

## Risks

- Some providers don't accept `max_tokens=4`. Bump to `max_tokens=16` if
  a provider rejects.

## Verify

```bash
uv run pytest tests/api/test_provider_test_endpoint.py -v
uv run pytest -v --tb=short
```
