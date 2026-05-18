"""
Discord bot runner — entrypoint called from ``cli/entrypoints.py``.

This module contains only the async bootstrap loop. All ``api.*`` wiring
(datastore, jobs) is injected from the CLI layer to keep the ``messaging``
package decoupled from the HTTP / persistence layer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from .discord_bot import CompanionDiscordBot, _parse_csv_ids

# Type alias for the job-runner callback injected from the CLI layer.
JobRunner = Callable[[str, str, str, str], Awaitable[str]]
SessionUpsert = Callable[[str, str, str, str | None], Any]
SessionGet = Callable[[str], Any | None]


async def run_bot(
    *,
    token: str,
    allowed_guild_ids: str,
    allowed_channel_ids: str,
    default_model: str,
    user_id_for_jobs: str,
    job_runner: JobRunner,
    session_upsert: SessionUpsert,
    session_get: SessionGet,
) -> None:
    """Build and start the Companion Discord bot until KeyboardInterrupt."""
    bot = CompanionDiscordBot(
        allowed_guild_ids=_parse_csv_ids(allowed_guild_ids),
        allowed_channel_ids=_parse_csv_ids(allowed_channel_ids),
        default_model=default_model,
        user_id_for_jobs=user_id_for_jobs,
        job_runner=job_runner,
        session_upsert=session_upsert,
        session_get=session_get,
    )
    logger.info(
        "Starting Companion Discord bot (guilds={}, channels={}, model={}, user_id={})",
        allowed_guild_ids or "any",
        allowed_channel_ids or "any",
        default_model,
        user_id_for_jobs,
    )
    await bot.start(token)
