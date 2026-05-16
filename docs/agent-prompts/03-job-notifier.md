# 1.4 — Slack / Discord notifier for finished long jobs

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Jobs that take longer than `NOTIFIER_MIN_SECONDS` (default 30) post a
message to a configured Slack channel and/or Discord webhook when they
finish, with a deep link back to the chat.

## Files

- `api/agent/notifier.py` (new):
  - `async notify(job_id: str) -> None` — POSTs to
    `Settings.slack_webhook_url` and/or `Settings.discord_webhook_url`.
  - Payload: title (= job name / first user message), model, duration,
    final-200-chars of the assistant reply, link
    `{Settings.public_base_url}/ui/#chat/{session_id}`.
  - Wrap in `try/except`; log failures, never raise.
- `api/agent/jobs.py::_run_job` — on terminal status, if at least one
  webhook is set and `duration > NOTIFIER_MIN_SECONDS`, fire
  `await notifier.notify(job.id)`.
- `config/settings.py` — add `slack_webhook_url`, `discord_webhook_url`,
  `notifier_min_seconds` (int, default 30), `public_base_url` (str,
  default `http://127.0.0.1:8082`). Use `validation_alias=dict-form`.
- `api/admin_config.py` — surface in the wizard.
- `tests/api/test_notifier.py` (new) — uses `httpx.MockTransport` to
  verify the request body matches the Slack/Discord schema.

## Implementation plan

1. Add the four settings. Document with `description=` on each Field.
2. Implement `notifier.notify` using `httpx.AsyncClient` with 5 s timeout.
   Slack payload: `{"text": "...", "blocks": [...]}`. Discord payload:
   `{"content": "...", "embeds": [...]}`.
3. Call site in `_run_job`: compute `duration_ms` from the job's
   `started_at` / `finished_at`. Gate on threshold.
4. Tests: mock both webhooks, run a fake long job, assert one POST per
   configured webhook with the expected shape.

## Acceptance

- Long job (`sleep 35` via Bash tool) completes → Slack channel + Discord
  channel both receive a message with the chat deep link.
- Short job (< threshold) completes → no webhook fired.
- Webhook returns 500 → job still marked `done`, error logged.

## Risks

- A misconfigured webhook URL must not break the job. Wrap in `try/except`.
- Spam: never notify on `cancelled` status.

## Verify

```bash
uv run pytest tests/api/test_notifier.py -v
uv run pytest -v --tb=short
```
