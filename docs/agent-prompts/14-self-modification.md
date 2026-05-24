# 3.3 — Self-modification with confirmation

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

When the user expresses a durable preference ("you should always ask
before deleting files"), the agent calls `PreferenceSet(key, value)`,
which:
1. Writes the rule into `~/.config/companion/preferences.md`
   (a file prepended to every system prompt).
2. Echoes a one-line confirmation of what was recorded.
3. Persists across sessions and chats.

## Files

- `api/agent/extras/preferences.py` (new):
  - `PreferenceSet(key: str, value: str)` tool — writes a Markdown bullet
    `- **{key}**: {value}` into the prefs file. If `key` already exists,
    update in place (not append-then-duplicate).
  - `PreferenceDelete(key: str)` tool.
  - `PreferenceList()` tool.
- `api/agent/agent_loop.py` — load prefs file at the start of every
  agent loop and prepend its contents to the system prompt with a
  header *"Long-term user preferences (always respect):"*.
- Identity prompt extended: *"When the user expresses a long-term
  preference (e.g. 'always ask before X', 'always answer in German'),
  call PreferenceSet to record it. Always summarise what you recorded
  in one short sentence."*
- `config/settings.py` — `preferences_path` (default
  `~/.config/companion/preferences.md`).
- `api/dashboard_routes.py` — `GET /v1/preferences`, `DELETE /v1/preferences/{key}`
  so users can audit + remove from the UI.
- `api/ui_static/app.js` — Settings page → "Preferences" card listing
  current entries with a delete button each.
- `tests/agent/test_preferences.py` (new).

## Implementation plan

1. Implement the prefs file as a simple Markdown list. Parser converts
   `- **key**: value` lines into a dict. Writer round-trips the dict
   back. Atomic write (tmpfile + rename).
2. Register the three tools in the existing extras registry.
3. Inject the prefs into the system prompt in `agent_loop`. If empty,
   skip the header — don't pollute the prompt.
4. UI: list + per-row delete. Confirm before delete.
5. Tests: set a pref via the tool, observe file contents; re-set the
   same key, observe in-place update; delete via API.

## Acceptance

- User: *"Companion, always answer in German."* → agent calls
  `PreferenceSet(key="language", value="Always respond in German")` →
  next chat in a new session starts in German.
- User: *"Stop answering in German."* → agent calls `PreferenceDelete`
  → German behavior gone in the next chat.
- Preferences card in Settings shows current rules with delete buttons.

## Risks

- Don't let the model write arbitrary content into the prefs file — keep
  the schema strict (`key`, `value` only). Both fields validated as
  strings, max 200 chars each.
- File path must not be settable from the chat — `preferences_path` is
  env-only.

## Verify

```bash
uv run pytest tests/agent/test_preferences.py -v
uv run pytest -v --tb=short
```
