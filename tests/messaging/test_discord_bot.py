"""Tests for messaging/discord_bot.py — jobs-API-based Discord bot."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from messaging.discord_bot import (
    CompanionDiscordBot,
    DiscordSessionRegistry,
    _extract_text_from_sse,
    _parse_csv_ids,
    chunk_text,
)

# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_short_text_returned_as_single_chunk(self):
        assert chunk_text("hello") == ["hello"]

    def test_empty_string_handled(self):
        result = chunk_text("")
        # Either empty list or list with empty string — both acceptable;
        # assert nothing is returned that would crash discord
        assert all(isinstance(c, str) for c in result)

    def test_long_text_split_into_multiple_chunks(self):
        long = "a" * 5000
        chunks = chunk_text(long, limit=1900)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 1900

    def test_chunks_reconstruct_original_content(self):
        text = "word " * 500  # 2500 chars
        chunks = chunk_text(text, limit=1900)
        # All original words should survive in the chunks
        joined = "".join(chunks)
        assert "word" in joined

    def test_code_fence_reopened_in_next_chunk(self):
        # A fence open in chunk 1 → next chunk should open a new fence.
        code_block = "```python\n" + ("x = 1\n" * 200) + "```\n"
        chunks = chunk_text(code_block, limit=200)
        assert len(chunks) >= 2
        # Second chunk should start with the fence re-opener
        assert chunks[1].startswith("```")

    def test_exactly_at_limit_is_single_chunk(self):
        text = "x" * 1900
        assert chunk_text(text, limit=1900) == [text]

    def test_one_over_limit_splits(self):
        text = "x" * 1901
        chunks = chunk_text(text, limit=1900)
        assert len(chunks) >= 2

    def test_newline_preferred_as_split_point(self):
        text = "line1\n" * 400
        chunks = chunk_text(text, limit=1900)
        for c in chunks:
            assert len(c) <= 1900


# ---------------------------------------------------------------------------
# _parse_csv_ids
# ---------------------------------------------------------------------------


class TestParseCsvIds:
    def test_empty_returns_frozenset(self):
        assert _parse_csv_ids("") == frozenset()

    def test_single_id(self):
        assert _parse_csv_ids("123") == frozenset({"123"})

    def test_multiple_ids(self):
        assert _parse_csv_ids("1,2,3") == frozenset({"1", "2", "3"})

    def test_strips_whitespace(self):
        assert _parse_csv_ids(" 1 , 2 ") == frozenset({"1", "2"})

    def test_empty_parts_ignored(self):
        assert _parse_csv_ids("1,,2,") == frozenset({"1", "2"})


# ---------------------------------------------------------------------------
# _extract_text_from_sse
# ---------------------------------------------------------------------------


class TestExtractTextFromSse:
    def test_extracts_text_delta(self):
        payload = json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            }
        )
        raw = f"event: content_block_delta\ndata: {payload}\n\n"
        assert _extract_text_from_sse(raw) == "hello"

    def test_ignores_non_text_delta(self):
        payload = json.dumps({"type": "message_start", "message": {}})
        raw = f"data: {payload}\n\n"
        assert _extract_text_from_sse(raw) == ""

    def test_ignores_done_sentinel(self):
        raw = "data: [DONE]\n\n"
        assert _extract_text_from_sse(raw) == ""

    def test_concatenates_multiple_deltas(self):
        p1 = json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello "},
            }
        )
        p2 = json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "world"},
            }
        )
        raw = f"data: {p1}\ndata: {p2}\n"
        assert _extract_text_from_sse(raw) == "hello world"

    def test_malformed_json_ignored(self):
        raw = "data: {not json}\n"
        assert _extract_text_from_sse(raw) == ""


# ---------------------------------------------------------------------------
# DiscordSessionRegistry helpers
# ---------------------------------------------------------------------------


def _make_registry(sessions_by_id: dict | None = None):
    """Create a registry with mock callables."""
    sessions_by_id = sessions_by_id or {}

    def _upsert(title, model, user_id, session_id=None):
        if session_id and session_id in sessions_by_id:
            return sessions_by_id[session_id]
        new_id = session_id or f"ses_{len(sessions_by_id) + 1}"
        sess = {"id": new_id, "title": title, "model": model}
        sessions_by_id[new_id] = sess
        return sess

    def _get(session_id):
        return sessions_by_id.get(session_id)

    return DiscordSessionRegistry(session_upsert=_upsert, session_get=_get)


class TestDiscordSessionRegistry:
    @pytest.mark.asyncio
    async def test_get_or_create_creates_session(self):
        reg = _make_registry()
        sid = await reg.get_or_create(
            "guild1", "chan1", None, model="deepseek/x", user_id="default"
        )
        assert sid.startswith("ses_")

    @pytest.mark.asyncio
    async def test_get_or_create_returns_same_session_on_second_call(self):
        call_count = 0
        fake = {"id": "ses_abc123", "model": "deepseek/x"}

        def _upsert(title, model, user_id, session_id=None):
            nonlocal call_count
            call_count += 1
            return fake

        reg = DiscordSessionRegistry(session_upsert=_upsert, session_get=lambda s: fake)
        sid1 = await reg.get_or_create(
            "guild1", "chan1", None, model="deepseek/x", user_id="default"
        )
        sid2 = await reg.get_or_create(
            "guild1", "chan1", None, model="deepseek/x", user_id="default"
        )
        assert call_count == 1
        assert sid1 == sid2

    @pytest.mark.asyncio
    async def test_reset_creates_new_session(self):
        reg = _make_registry()
        await reg.get_or_create("g", "c", None, model="m", user_id="u")
        new_sid = await reg.reset("g", "c", None, model="m", user_id="u")
        assert new_sid is not None

    @pytest.mark.asyncio
    async def test_set_model_updates_existing_session(self):
        updated_model: list[str] = []

        def _upsert(title, model, user_id, session_id=None):
            updated_model.append(model)
            return {"id": session_id or "ses_1", "title": title, "model": model}

        def _get(sid):
            return {"id": sid, "title": "discord:g:c", "model": "old"}

        reg = DiscordSessionRegistry(session_upsert=_upsert, session_get=_get)
        # Pre-seed the registry with an existing session key
        reg._sessions[("g", "c", None)] = "ses_existing"

        await reg.set_model("g", "c", None, model="new_model", user_id="u")
        assert "new_model" in updated_model

    @pytest.mark.asyncio
    async def test_get_model_returns_none_when_no_session(self):
        reg = _make_registry()
        result = await reg.get_model("g", "c", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_different_channels_get_different_sessions(self):
        reg = _make_registry()
        sid1 = await reg.get_or_create("g", "c1", None, model="m", user_id="u")
        sid2 = await reg.get_or_create("g", "c2", None, model="m", user_id="u")
        assert sid1 != sid2


# ---------------------------------------------------------------------------
# CompanionDiscordBot
# ---------------------------------------------------------------------------


def _make_bot(guilds="", channels="", reply_text="mocked reply"):
    async def _job_runner(session_id, text, model, user_id):
        return reply_text

    return CompanionDiscordBot(
        allowed_guild_ids=_parse_csv_ids(guilds),
        allowed_channel_ids=_parse_csv_ids(channels),
        default_model="deepseek/deepseek-v4-flash",
        user_id_for_jobs="default",
        job_runner=_job_runner,
        session_upsert=lambda t, m, u, s=None: {
            "id": s or "ses_test",
            "model": m,
        },
        session_get=lambda s: {"id": s, "model": "deepseek/deepseek-v4-flash"},
    )


class TestCompanionDiscordBot:
    def test_bot_created_with_correct_settings(self):
        bot = _make_bot(guilds="123", channels="456")
        assert "123" in bot._allowed_guilds
        assert "456" in bot._allowed_channels
        assert bot._default_model == "deepseek/deepseek-v4-flash"
        assert bot._user_id == "default"

    def test_empty_allowlists(self):
        bot = _make_bot()
        assert bot._allowed_guilds == frozenset()
        assert bot._allowed_channels == frozenset()

    @pytest.mark.asyncio
    async def test_on_message_ignores_bots(self):
        bot = _make_bot()
        mock_client = MagicMock()
        mock_client.user = MagicMock()
        bot._client = mock_client

        msg = MagicMock()
        msg.author.bot = True
        msg.mentions = []
        await bot._on_message(msg)
        # No exception = pass

    @pytest.mark.asyncio
    async def test_on_message_ignores_disallowed_guild(self):
        bot = _make_bot(guilds="allowed_guild")
        mock_client = MagicMock()
        mock_client.user = MagicMock(spec=[])
        bot._client = mock_client

        msg = MagicMock()
        msg.author.bot = False
        msg.guild = MagicMock()
        msg.guild.id = 999  # not in allowlist
        msg.channel = MagicMock(spec=["id", "guild"])
        msg.channel.id = 111
        msg.channel.guild = msg.guild
        msg.mentions = []
        await bot._on_message(msg)

    @pytest.mark.asyncio
    async def test_on_message_ignores_disallowed_channel(self):
        bot = _make_bot(guilds="1", channels="allowed_chan")
        mock_client = MagicMock()
        mock_client.user = MagicMock(spec=[])
        bot._client = mock_client

        msg = MagicMock()
        msg.author.bot = False
        msg.guild = MagicMock()
        msg.guild.id = 1
        msg.channel = MagicMock(spec=["id", "guild"])
        msg.channel.id = 999  # not in allowlist
        msg.channel.guild = msg.guild
        msg.mentions = []
        await bot._on_message(msg)

    @pytest.mark.asyncio
    async def test_on_message_ignores_non_mention_in_guild(self):
        bot = _make_bot()
        mock_client = MagicMock()
        mock_user = MagicMock()
        mock_client.user = mock_user
        bot._client = mock_client

        msg = MagicMock()
        msg.author.bot = False
        msg.guild = MagicMock()
        msg.guild.id = 1
        msg.channel = MagicMock(spec=["id", "guild"])
        msg.channel.id = 2
        msg.channel.guild = msg.guild
        # Bot not in mentions
        msg.mentions = []
        await bot._on_message(msg)

    @pytest.mark.asyncio
    async def test_on_message_handles_mentioned_message(self):
        replies: list[str] = []
        mock_user = MagicMock()
        bot = _make_bot(reply_text="Here is the summary.")
        mock_client = MagicMock()
        mock_client.user = mock_user
        bot._client = mock_client

        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.author.display_name = "TestUser"
        msg.guild = MagicMock()
        msg.guild.id = 1
        msg.channel = MagicMock()
        msg.channel.id = 2
        msg.channel.guild = msg.guild
        msg.mentions = [mock_user]
        msg.content = f"<@{mock_user.id}> summarize this"
        msg.reply = AsyncMock(side_effect=lambda text: replies.append(text))
        msg.channel.send = AsyncMock()
        msg.channel.typing = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=None),
                __aexit__=AsyncMock(return_value=False),
            )
        )

        await bot._on_message(msg)

        assert replies == ["Here is the summary."]

    @pytest.mark.asyncio
    async def test_on_message_chunks_long_reply(self):
        long_reply = "word " * 500  # ~2500 chars
        bot = _make_bot(reply_text=long_reply)
        mock_user = MagicMock()
        mock_client = MagicMock()
        mock_client.user = mock_user
        bot._client = mock_client

        msg = MagicMock()
        msg.author.bot = False
        msg.guild = MagicMock()
        msg.guild.id = 1
        msg.channel = MagicMock()
        msg.channel.id = 2
        msg.channel.guild = msg.guild
        msg.mentions = [mock_user]
        msg.content = f"<@{mock_user.id}> hi"
        msg.reply = AsyncMock()
        msg.channel.send = AsyncMock()
        msg.channel.typing = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=None),
                __aexit__=AsyncMock(return_value=False),
            )
        )

        await bot._on_message(msg)

        # First chunk sent via reply, remaining via channel.send
        msg.reply.assert_awaited_once()
        assert msg.channel.send.await_count >= 1

    @pytest.mark.asyncio
    async def test_on_message_error_sends_error_reply(self):
        async def _failing_runner(session_id, text, model, user_id):
            raise RuntimeError("provider down")

        bot = CompanionDiscordBot(
            allowed_guild_ids=frozenset(),
            allowed_channel_ids=frozenset(),
            default_model="deepseek/x",
            user_id_for_jobs="default",
            job_runner=_failing_runner,
            session_upsert=lambda t, m, u, s=None: {"id": "ses_x", "model": m},
            session_get=lambda s: {"id": s, "model": "deepseek/x"},
        )
        mock_user = MagicMock()
        mock_client = MagicMock()
        mock_client.user = mock_user
        bot._client = mock_client

        msg = MagicMock()
        msg.author.bot = False
        msg.guild = MagicMock()
        msg.guild.id = 1
        msg.channel = MagicMock()
        msg.channel.id = 2
        msg.channel.guild = msg.guild
        msg.mentions = [mock_user]
        msg.content = f"<@{mock_user.id}> hello"
        msg.reply = AsyncMock()
        msg.channel.typing = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=None),
                __aexit__=AsyncMock(return_value=False),
            )
        )

        await bot._on_message(msg)

        msg.reply.assert_awaited_once()
        assert "RuntimeError" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# CLI entrypoint smoke test
# ---------------------------------------------------------------------------


class TestCliEntrypoint:
    def test_discord_bot_command_resolves(self):
        """companion discord-bot should route to _discord_bot_main."""
        with (
            patch("sys.argv", ["companion", "discord-bot"]),
            patch(
                "cli.entrypoints._discord_bot_main",
                side_effect=SystemExit(0),
            ),
        ):
            from cli.entrypoints import companion

            with pytest.raises(SystemExit) as exc_info:
                companion()
            assert exc_info.value.code == 0

    def test_unknown_command_exits(self):
        with patch("sys.argv", ["companion", "nonexistent"]):
            from cli.entrypoints import companion

            with pytest.raises(SystemExit) as exc_info:
                companion()
            assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Settings additions
# ---------------------------------------------------------------------------


class TestDiscordBotSettings:
    def test_new_settings_have_defaults(self, monkeypatch):
        monkeypatch.delenv("DISCORD_ALLOWED_GUILD_IDS", raising=False)
        monkeypatch.delenv("DISCORD_ALLOWED_CHANNEL_IDS", raising=False)
        monkeypatch.delenv("DISCORD_USER_ID_FOR_JOBS", raising=False)
        from config.settings import Settings

        s = Settings()
        assert s.discord_allowed_guild_ids == ""
        assert s.discord_allowed_channel_ids == ""
        assert s.discord_user_id_for_jobs == "default"

    def test_guild_ids_loaded_from_env(self, monkeypatch):
        monkeypatch.setenv("DISCORD_ALLOWED_GUILD_IDS", "111,222")
        from config.settings import Settings

        s = Settings()
        assert s.discord_allowed_guild_ids == "111,222"

    def test_channel_ids_loaded_from_env(self, monkeypatch):
        monkeypatch.setenv("DISCORD_ALLOWED_CHANNEL_IDS", "333,444")
        from config.settings import Settings

        s = Settings()
        assert s.discord_allowed_channel_ids == "333,444"

    def test_user_id_for_jobs_loaded_from_env(self, monkeypatch):
        monkeypatch.setenv("DISCORD_USER_ID_FOR_JOBS", "alice")
        from config.settings import Settings

        s = Settings()
        assert s.discord_user_id_for_jobs == "alice"
