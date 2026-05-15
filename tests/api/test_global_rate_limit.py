"""Tests for the process-wide tool-call rate limiter (R7)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from api.agent.agent_loop_streaming import run_agent_streaming
from api.agent.global_rate_limit import configure, reset
from api.agent.rate_limit import ToolCallRateLimiter
from api.models.anthropic import Message, MessagesRequest
from core.tools.workspace import Workspace


@pytest.fixture(autouse=True)
def _reset_global_limiter():
    reset()
    yield
    reset()


def test_configure_returns_none_when_disabled() -> None:
    assert configure(0) is None


def test_configure_returns_singleton_for_same_limit() -> None:
    a = configure(5)
    b = configure(5)
    assert a is b
    assert isinstance(a, ToolCallRateLimiter)


def test_configure_replaces_when_limit_changes() -> None:
    a = configure(5)
    b = configure(10)
    assert a is not b
    assert b is not None
    assert b.limit_per_minute == 10


def _event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _text_turn(text: str) -> list[str]:
    return [
        _event(
            "message_start",
            {"type": "message_start", "message": {"id": "m", "model": "x"}},
        ),
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event(
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]


def _tool_use_turn(tool_input: dict[str, Any], tool_id: str) -> list[str]:
    return [
        _event(
            "message_start",
            {"type": "message_start", "message": {"id": "m", "model": "x"}},
        ),
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Read",
                    "input": {},
                },
            },
        ),
        _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(tool_input),
                },
            },
        ),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event(
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]


class _FakeProvider:
    def __init__(self, turns: list[list[str]]) -> None:
        self._turns = turns

    def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        events = self._turns.pop(0)

        async def _gen() -> AsyncIterator[str]:
            for ev in events:
                yield ev

        return _gen()


def _request() -> MessagesRequest:
    return MessagesRequest(
        model="x",
        max_tokens=64,
        messages=[Message(role="user", content="go")],
    )


@pytest.mark.asyncio
async def test_global_limiter_blocks_after_quota_exhausted(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    # Pre-exhaust the global limiter so the very first tool call hits it.
    global_limiter = configure(1)
    assert global_limiter is not None
    assert global_limiter.acquire() is True
    # Per-request limiter stays generous; only the global one should reject.
    provider = _FakeProvider(
        [
            _tool_use_turn({"file_path": "f.txt"}, "t1"),
            _text_turn("done"),
        ]
    )
    chunks: list[str] = [
        chunk
        async for chunk in run_agent_streaming(
            _request(),
            provider,
            Workspace.create(tmp_path),
            tool_call_limit_per_minute=100,
            global_limiter=global_limiter,
        )
    ]
    body = "".join(chunks)
    assert "scope=global" in body
