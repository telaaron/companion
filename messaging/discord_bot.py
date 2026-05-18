"""
Discord Bot — Jobs API integration.

Routes Discord messages through the Companion jobs API so replies survive
tab-closes and network hiccups.  The bot uses discord.py slash commands for
``/companion new`` and ``/companion model <id>``, and responds to @mentions
and DMs via ``on_message``.

All ``api.*`` dependencies are injected via callables so the ``messaging``
package stays decoupled from the HTTP/persistence layer.

Chunking strategy:
- Discord has a 2000-char hard limit.  We chunk at 1900 chars to leave head-room.
- We track open code fences (``` blocks) and re-open them at the start of the
  next chunk so Discord renders them correctly.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# Type aliases for injected dependencies (no api.* import needed)
# ---------------------------------------------------------------------------

# Callable: (session_id, text, model, user_id) -> assistant reply text
JobRunner = Callable[[str, str, str, str], Awaitable[str]]

# Callable: (title, model, user_id, session_id|None) -> {"id": str, "model": str, ...}
SessionUpsert = Callable[[str, str, str, str | None], Any]

# Callable: (session_id) -> {"id": str, "model": str, "title": str, ...} | None
SessionGet = Callable[[str], Any | None]

# ---------------------------------------------------------------------------
# Chunking helpers (pure, no discord dependency)
# ---------------------------------------------------------------------------

CHUNK_SIZE = 1900
_CODE_FENCE_RE = re.compile(r"```(\w*)")


def _count_open_fences(text: str) -> int:
    """Return the number of unclosed ``` fences in *text*."""
    count = 0
    for m in _CODE_FENCE_RE.finditer(text):
        # A plain ``` with no language tag closes the previous fence.
        if m.group(1):
            count += 1
        else:
            # Could be an opener with no lang or a closer — toggle.
            count = max(0, count - 1) if count > 0 else count + 1
    return count


def chunk_text(text: str, limit: int = CHUNK_SIZE) -> list[str]:
    """Split *text* into chunks ≤ *limit* chars, preserving code fences.

    When a split falls inside an open code fence the next chunk is prefixed
    with `` ``` `` so Discord continues rendering the block correctly.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    open_fences = 0

    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        # Find a natural break point within the limit.
        slice_ = text[:limit]
        # Prefer splitting on a newline near the end.
        split_at = slice_.rfind("\n")
        if split_at < limit // 2:
            split_at = limit

        chunk = text[:split_at]
        text = text[split_at:].lstrip("\n")

        if open_fences > 0:
            chunk += "\n```"

        chunks.append(chunk)
        open_fences = _count_open_fences(chunk)

        if open_fences > 0 and text:
            text = "```\n" + text

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# SSE / event-stream parsing helpers
# ---------------------------------------------------------------------------


def _extract_text_from_sse(raw: str) -> str:
    """Extract assistant text from raw SSE data produced by the jobs pipeline."""
    text_parts: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except json.JSONDecodeError:
            continue
        ev_type = ev.get("type", "")
        if ev_type == "content_block_delta":
            delta = ev.get("delta", {})
            if delta.get("type") == "text_delta":
                text_parts.append(delta.get("text", ""))
    return "".join(text_parts)


# ---------------------------------------------------------------------------
# Session registry (maps channel_id → Companion session_id)
# ---------------------------------------------------------------------------


class DiscordSessionRegistry:
    """Thread-safe in-memory map of ``(guild_id, channel_id, thread_id)`` → session_id.

    All persistence calls are delegated to injected callables so this class
    has zero imports from ``api.*``.
    """

    def __init__(
        self,
        *,
        session_upsert: SessionUpsert,
        session_get: SessionGet,
    ) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[tuple[str, str, str | None], str] = {}
        self._session_upsert = session_upsert
        self._session_get = session_get

    async def get_or_create(
        self,
        guild_id: str,
        channel_id: str,
        thread_id: str | None,
        *,
        model: str,
        user_id: str,
    ) -> str:
        """Return the existing session id or create a new one."""
        key = (guild_id, channel_id, thread_id)
        async with self._lock:
            if key in self._sessions:
                return self._sessions[key]
            title = f"discord:{guild_id}:{channel_id}"
            if thread_id:
                title += f":{thread_id}"
            session = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._session_upsert(title, model, user_id, None)
            )
            sid = session["id"]
            self._sessions[key] = sid
            logger.info("Discord: created session {} for key {}", sid, key)
            return sid

    async def reset(
        self,
        guild_id: str,
        channel_id: str,
        thread_id: str | None,
        *,
        model: str,
        user_id: str,
    ) -> str:
        """Force-create a new session (e.g. /companion new)."""
        key = (guild_id, channel_id, thread_id)
        async with self._lock:
            title = f"discord:{guild_id}:{channel_id}"
            if thread_id:
                title += f":{thread_id}"
            session = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._session_upsert(title, model, user_id, None)
            )
            sid = session["id"]
            self._sessions[key] = sid
            logger.info("Discord: reset session {} for key {}", sid, key)
            return sid

    async def get_model(
        self,
        guild_id: str,
        channel_id: str,
        thread_id: str | None,
    ) -> str | None:
        """Return the current model for a channel, or None."""
        key = (guild_id, channel_id, thread_id)
        async with self._lock:
            sid = self._sessions.get(key)
        if not sid:
            return None
        session = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._session_get(sid)
        )
        return session.get("model") if session else None

    async def set_model(
        self,
        guild_id: str,
        channel_id: str,
        thread_id: str | None,
        *,
        model: str,
        user_id: str,
    ) -> str:
        """Update the model for the current session (or create one)."""
        key = (guild_id, channel_id, thread_id)
        async with self._lock:
            sid = self._sessions.get(key)
        if sid:
            session = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._session_get(sid)
            )
            title = (session or {}).get("title") or f"discord:{guild_id}:{channel_id}"
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._session_upsert(title, model, user_id, sid)
            )
            return sid
        return await self.get_or_create(
            guild_id, channel_id, thread_id, model=model, user_id=user_id
        )


# ---------------------------------------------------------------------------
# Allowlist helpers
# ---------------------------------------------------------------------------


def _parse_csv_ids(raw: str) -> frozenset[str]:
    """Parse a comma-separated string of IDs into a frozenset."""
    if not raw or not raw.strip():
        return frozenset()
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


# ---------------------------------------------------------------------------
# Main bot class
# ---------------------------------------------------------------------------


class CompanionDiscordBot:
    """Discord bot that routes messages through the Companion jobs API.

    All ``api.*`` dependencies are provided via injected callables so this
    class has zero direct imports from the persistence / HTTP layer.
    """

    def __init__(
        self,
        *,
        allowed_guild_ids: frozenset[str],
        allowed_channel_ids: frozenset[str],
        default_model: str,
        user_id_for_jobs: str,
        job_runner: JobRunner,
        session_upsert: SessionUpsert,
        session_get: SessionGet,
    ) -> None:
        self._allowed_guilds = allowed_guild_ids
        self._allowed_channels = allowed_channel_ids
        self._default_model = default_model
        self._user_id = user_id_for_jobs
        self._job_runner = job_runner
        self._sessions = DiscordSessionRegistry(
            session_upsert=session_upsert,
            session_get=session_get,
        )
        self._client: Any = None  # set in build_client()
        self._tree: Any = None

    # ------------------------------------------------------------------
    # Discord client factory
    # ------------------------------------------------------------------

    def build_client(self) -> Any:
        """Build and return a configured discord.Client."""
        try:
            import discord
        except ImportError as exc:
            raise ImportError(
                "discord.py is required. Install it with: pip install discord.py"
            ) from exc

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        client = discord.Client(intents=intents)
        tree = discord.app_commands.CommandTree(client)

        # Register event handlers
        client.event(self._on_ready)
        client.event(self._on_message)

        # Register slash commands
        self._register_slash_commands(tree)

        self._client = client
        self._tree = tree
        return client

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _on_ready(self) -> None:
        assert self._client is not None
        logger.info("Discord bot connected as {}", self._client.user)
        try:
            synced = await self._tree.sync()
            logger.info("Discord: synced {} slash commands", len(synced))
        except Exception as exc:
            logger.warning("Discord: slash command sync failed: {}", exc)

    async def _on_message(self, message: Any) -> None:
        """Handle incoming messages — respond to @mentions and DMs."""
        assert self._client is not None
        if message.author.bot:
            return

        # Resolve channel and guild
        is_dm = not hasattr(message.channel, "guild") or message.guild is None
        guild_id = str(message.guild.id) if not is_dm else "dm"
        channel_id = str(message.channel.id)

        # Allowlist check (skip for DMs if allowlists are empty)
        if not is_dm:
            if self._allowed_guilds and guild_id not in self._allowed_guilds:
                return
            if self._allowed_channels and channel_id not in self._allowed_channels:
                return

        # Only respond to @mentions or DMs
        mentioned = self._client.user in message.mentions
        if not is_dm and not mentioned:
            return

        # Strip the mention prefix from the message content
        content = message.content
        if mentioned:
            content = re.sub(r"<@!?\d+>\s*", "", content).strip()
        if not content:
            return

        thread_id = (
            str(message.channel.id) if hasattr(message.channel, "parent_id") else None
        )

        session_id = await self._sessions.get_or_create(
            guild_id,
            channel_id,
            thread_id,
            model=self._default_model,
            user_id=self._user_id,
        )
        model = (
            await self._sessions.get_model(guild_id, channel_id, thread_id)
            or self._default_model
        )

        # Acknowledge receipt with a typing indicator
        async with message.channel.typing():
            try:
                reply_text = await self._job_runner(
                    session_id, content, model, self._user_id
                )
            except Exception as exc:
                logger.error("Discord bot: job failed: {}", exc)
                await message.reply(
                    f"Sorry, something went wrong: {type(exc).__name__}"
                )
                return

        if not reply_text:
            reply_text = "(no response)"

        chunks = chunk_text(reply_text)
        first = True
        for chunk in chunks:
            if first:
                await message.reply(chunk)
                first = False
            else:
                await message.channel.send(chunk)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    def _register_slash_commands(self, tree: Any) -> None:
        """Register /companion subcommands on *tree*."""

        bot = self  # capture for closures

        @tree.command(name="companion", description="Companion bot controls")
        async def companion_cmd(interaction: Any, action: str, value: str = "") -> None:
            """Dispatch /companion <action> [value]."""
            channel_id = str(interaction.channel_id)
            guild_id = str(interaction.guild_id) if interaction.guild_id else "dm"
            thread_id = None
            if interaction.channel and hasattr(interaction.channel, "parent_id"):
                thread_id = channel_id

            if action == "new":
                session_id = await bot._sessions.reset(
                    guild_id,
                    channel_id,
                    thread_id,
                    model=bot._default_model,
                    user_id=bot._user_id,
                )
                await interaction.response.send_message(
                    f"Started a fresh session (`{session_id[:8]}…`). "
                    "Previous conversation history cleared.",
                    ephemeral=True,
                )

            elif action == "model":
                new_model = value.strip()
                if not new_model:
                    current = (
                        await bot._sessions.get_model(guild_id, channel_id, thread_id)
                        or bot._default_model
                    )
                    await interaction.response.send_message(
                        f"Current model: `{current}`", ephemeral=True
                    )
                    return
                if "/" not in new_model:
                    await interaction.response.send_message(
                        "Model must be in `provider/model` format "
                        "(e.g. `deepseek/deepseek-v4-flash`).",
                        ephemeral=True,
                    )
                    return
                await bot._sessions.set_model(
                    guild_id,
                    channel_id,
                    thread_id,
                    model=new_model,
                    user_id=bot._user_id,
                )
                await interaction.response.send_message(
                    f"Model switched to `{new_model}` for this thread.",
                    ephemeral=True,
                )

            else:
                await interaction.response.send_message(
                    f"Unknown action `{action}`. Available: `new`, `model`.",
                    ephemeral=True,
                )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, token: str) -> None:
        """Build and start the Discord client."""
        client = self.build_client()
        await client.start(token)
