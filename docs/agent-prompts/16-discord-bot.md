# 4.3 — Discord bot wrapper

> Inherits shared context from [README.md](README.md). Read it first.

## Goal

Run Companion as a Discord bot. Mention `@Companion` in a channel →
reply in-thread using the same agent loop and tools.

## Files

- `messaging/discord_bot.py` — already exists for the upstream fork; port
  to use the new jobs API instead of the legacy direct-agent path.
- `messaging/discord_bot_runner.py` (new) — entrypoint for
  `companion discord-bot`. Starts the bot, registers slash commands.
- `cli/entrypoints.py` — add the new `discord-bot` console script.
- `config/settings.py` — `discord_bot_token`, `discord_allowed_guild_ids`
  (CSV), `discord_allowed_channel_ids` (CSV), `discord_user_id_for_jobs`
  (which Companion user_id to bill jobs to; defaults to `"default"`).
- `tests/messaging/test_discord_bot.py` (new) — uses `discord.py`'s
  test harness or a mock to verify command routing.

## Implementation plan

1. Use `discord.py` (already a transitive dep — verify) or `nextcord`.
2. On `on_message`:
   - Ignore unless the bot is mentioned or it's a DM.
   - Resolve guild + channel against the allowlist; ignore if disallowed.
   - Find-or-create a Companion session keyed by `(channel_id, thread_id)`.
   - POST to `/v1/sessions/{id}/jobs` with the message text.
   - Stream the SSE; aggregate text into Discord messages (chunk at
     1900 chars to stay under the 2000 limit). Use a thread per chat
     to keep history organized.
3. Slash commands: `/companion new` starts a fresh session in a thread,
   `/companion model <id>` switches model for the current thread.
4. Tests with mocked Discord client.

## Acceptance

- Mention `@Companion summarize this paper https://...` in an allowed
  channel → bot opens a thread, replies in chunks within ~10 s.
- Same chat continues if you reply in the thread.
- Calls outside the allowlist are silently ignored.

## Risks

- 2000-char chunking can split mid-code-block. Track open ``` fences
  and re-open them at the start of the next chunk.
- Don't expose Bash / Write tools to public Discord channels unless the
  caller is in a per-server "trusted" list (config-driven).

## Verify

```bash
uv run pytest tests/messaging/test_discord_bot.py -v
uv run pytest -v --tb=short
```
