# 1.5 — Auto-name verification + retry

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Every session is renamed within ~5 s of the first reply. No "untitled"
cards in the sidebar.

## Files

- `api/dashboard_routes.py` — new `POST /v1/sessions/{id}/auto-rename`
  that does the rename server-side with a fixed system prompt. Uses
  `httpx` directly, not the agent loop (single round-trip, no tools).
- `api/ui_static/app.js::maybeRenameSessionAsync` — replace with a call
  to the new endpoint; add one retry after a 3 s delay if the first call
  returns a falsy / unparseable title. Log retries to console.
- `tests/api/test_auto_rename.py` (new) — fake provider returns a known
  string, endpoint returns it, sidebar reflects update on next list.

## Implementation plan

1. Add the endpoint. Body: `{first_user_message: str, first_assistant_message: str}`.
   Build a 2-message conversation with the system prompt:
   *"Summarize this exchange as a 3-6 word title. Output the title only,
   no quotes, no punctuation."* Call the configured chat provider with
   `max_tokens=40`. Parse the response, strip whitespace + quotes, cap
   length to 60 chars. Persist via `datastore.update_session(title=...)`.
   Return `{title}`.
2. Update `maybeRenameSessionAsync` in `app.js` to POST to the endpoint,
   then refresh the sidebar. On falsy response, wait 3 s and retry once.
3. Add tests with a `_FakeProvider` returning a fixed title.

## Acceptance

- Open 10 fresh sessions, send one message each → all titled within 5 s.
- Upstream returns gibberish → title falls back to the truncated first
  user message (existing `deriveSessionTitle`) — never stays "untitled".
- Retry path covered by a test that fails the first call.

## Risks

- Don't tie the rename to the streaming job lifecycle — it must work
  independently so a slow upstream doesn't block the rename.

## Verify

```bash
uv run pytest tests/api/test_auto_rename.py -v
uv run pytest -v --tb=short
```
