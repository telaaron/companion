"""Process-wide tool-call rate limiter shared across concurrent agent runs.

The per-request :class:`api.agent.rate_limit.ToolCallRateLimiter` caps a single
agent loop. This module exposes a singleton (per-process) shared limiter that
caps the *aggregate* tool-call rate across every concurrent agent request.
Both limits are checked independently — a tool call must pass both gates.
"""

from __future__ import annotations

import asyncio

from api.agent.rate_limit import ToolCallRateLimiter

_lock = asyncio.Lock()
_instance: ToolCallRateLimiter | None = None
_current_limit: int | None = None


def configure(limit_per_minute: int) -> ToolCallRateLimiter | None:
    """Return the shared limiter, recreating it if the cap changed.

    ``limit_per_minute <= 0`` returns ``None`` (limiter disabled). Callers
    pass that ``None`` straight through to the agent loop, which treats a
    missing limiter as no-op.
    """
    global _instance, _current_limit
    if limit_per_minute <= 0:
        _instance = None
        _current_limit = 0
        return None
    if _instance is None or _current_limit != limit_per_minute:
        _instance = ToolCallRateLimiter(limit_per_minute=limit_per_minute)
        _current_limit = limit_per_minute
    return _instance


def reset() -> None:
    """Drop the shared limiter — for tests."""
    global _instance, _current_limit
    _instance = None
    _current_limit = None


# `_lock` reserved for future use if we add CAS-style limiter rotation.
__all__ = ["configure", "reset"]


_ = _lock  # silence unused-name lint until lock-protected mutation lands
