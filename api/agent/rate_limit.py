"""Per-request tool-call rate limiter for the agent loop.

A single request may run many tool calls back-to-back. Without a limit, a
runaway model could exhaust local compute or trigger upstream costs through
repeated small requests. This module provides a sliding-window counter
scoped to one request — instances are not shared between requests because
each agent run gets its own limiter.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

WINDOW_SECONDS = 60.0


@dataclass(slots=True)
class ToolCallRateLimiter:
    """Sliding-window limiter: at most ``limit_per_minute`` ``acquire`` per 60s."""

    limit_per_minute: int
    _hits: deque[float] = field(default_factory=deque)

    def acquire(self) -> bool:
        """Record one tool call. Return True if permitted, False if exceeded.

        ``limit_per_minute <= 0`` disables the limiter (always allows).
        """
        if self.limit_per_minute <= 0:
            return True
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS
        while self._hits and self._hits[0] < cutoff:
            self._hits.popleft()
        if len(self._hits) >= self.limit_per_minute:
            return False
        self._hits.append(now)
        return True

    def current_usage(self) -> int:
        """Number of hits currently inside the active window."""
        if self.limit_per_minute <= 0:
            return 0
        cutoff = time.monotonic() - WINDOW_SECONDS
        while self._hits and self._hits[0] < cutoff:
            self._hits.popleft()
        return len(self._hits)
