# 6.3 — Routine template library

> Inherits shared context from [README.md](README.md). Read it first.
>
> **Depends on [21-routines-ui.md](21-routines-ui.md) being merged first.**

## Goal

A starter library of routines ("Daily news digest", "Refactor TODO
sweeper", "Inbox triage", "Standup-notes summarizer") that one click
installs into the user's routines list.

## Files

- `routines/` (new dir at repo root) — one `<slug>.yaml` template per
  starter routine. Schema:
  ```yaml
  slug: daily-news-digest
  name: Daily news digest
  description: At 8 AM, summarize top stories from configured RSS feeds.
  trigger:
    type: cron
    expression: "0 8 * * *"
    tz: Europe/Berlin
  payload:
    model: deepseek/deepseek-v4-flash
    max_tokens: 1500
    messages:
      - role: user
        content: |
          Read these feeds: {{feeds}}.
          Summarize the top 5 stories in 2 sentences each.
  inputs:
    - name: feeds
      label: RSS feed URLs (comma separated)
      type: string
      default: https://news.ycombinator.com/rss
  ```
- `api/dashboard_routes.py`:
  - `GET /v1/routines/templates` returns the parsed YAMLs.
  - `POST /v1/routines/from-template` `{slug, inputs}` materializes
    a routine by substituting `{{var}}` placeholders in the payload.
- `api/ui_static/app.js` — "From template…" picker in the routines
  drawer. Shows the input form derived from the YAML, previews the
  resulting payload, lets the user tweak before saving.
- `tests/api/test_routine_templates.py` (new) — substitution works,
  unknown slug → 404, missing required input → 422.

## Implementation plan

1. Ship 4 starter templates: daily news digest, refactor TODO sweeper,
   inbox triage, standup notes summarizer.
2. Template loader caches parsed YAMLs in memory; reload on
   `/v1/runtime/reload`.
3. `{{var}}` substitution is a simple string replace. Validate all
   required inputs are present before materializing.
4. UI: template grid above the empty-state of the routines drawer. Click
   → form → preview → "Create routine" → drops into the regular form
   with values prefilled.

## Acceptance

- Routines page → "From template…" → pick "Daily news digest" → fill in
  feeds → Create → routine appears in the list, fires next morning,
  produces a summary as a chat reply.
- All 4 starter templates can be installed without manual edits.

## Risks

- `{{var}}` collision with legitimate `{` in the payload — escape with
  `{{ "{{" }}` if needed (Jinja-style is overkill; document the limit).
- Template payloads shouldn't reference provider/model ids the user
  doesn't have configured. Validate against `/v1/models/upstream` before
  creation and warn.

## Verify

```bash
uv run pytest tests/api/test_routine_templates.py -v
uv run pytest -v --tb=short
```
