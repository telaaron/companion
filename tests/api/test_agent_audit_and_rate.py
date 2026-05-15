"""Tests for agent-loop audit emission + per-request tool-call rate limiter."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from api.agent.agent_loop_streaming import run_agent_streaming
from api.agent.rate_limit import ToolCallRateLimiter
from api.models.anthropic import Message, MessagesRequest
from core.tools.workspace import Workspace


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


def _tool_use_turn(
    tool_name: str, tool_input: dict[str, Any], *, tool_id: str = "toolu_a"
) -> list[str]:
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
                    "name": tool_name,
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


# ---------------------- Rate limiter unit tests ----------------------


def test_rate_limiter_allows_under_limit() -> None:
    limiter = ToolCallRateLimiter(limit_per_minute=3)
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is True


def test_rate_limiter_rejects_when_window_full() -> None:
    limiter = ToolCallRateLimiter(limit_per_minute=2)
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is False


def test_rate_limiter_zero_disables() -> None:
    limiter = ToolCallRateLimiter(limit_per_minute=0)
    for _ in range(1000):
        assert limiter.acquire() is True


def test_rate_limiter_window_evicts_old_hits(monkeypatch) -> None:
    fake_now = [1000.0]

    def _monotonic() -> float:
        return fake_now[0]

    monkeypatch.setattr(time, "monotonic", _monotonic)
    limiter = ToolCallRateLimiter(limit_per_minute=2)
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is False
    fake_now[0] += 61
    assert limiter.acquire() is True


def test_rate_limiter_current_usage_reports_window_size() -> None:
    limiter = ToolCallRateLimiter(limit_per_minute=5)
    limiter.acquire()
    limiter.acquire()
    assert limiter.current_usage() == 2


# ---------------------- Audit + rate-limit in streaming loop ----------------------


@pytest.mark.asyncio
async def test_audit_event_emitted_per_tool_invocation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "f.txt").write_text("hello")
    provider = _FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}),
            _text_turn("done"),
        ]
    )
    with caplog.at_level("INFO"):
        chunks: list[str] = [
            chunk
            async for chunk in run_agent_streaming(
                _request(), provider, Workspace.create(tmp_path), request_id="req-1"
            )
        ]
    _ = chunks
    audit_messages = [r.message for r in caplog.records if "tool_use" in r.message]
    assert any("api.agent_loop.tool_use" in m for m in audit_messages)


@pytest.mark.asyncio
async def test_rate_limit_blocks_subsequent_calls_in_one_request(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "f.txt").write_text("x")
    provider = _FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}, tool_id="t1"),
            _tool_use_turn("Read", {"file_path": "f.txt"}, tool_id="t2"),
            _text_turn("ok"),
        ]
    )
    with caplog.at_level("INFO"):
        chunks: list[str] = [
            chunk
            async for chunk in run_agent_streaming(
                _request(),
                provider,
                Workspace.create(tmp_path),
                request_id="req-r",
                tool_call_limit_per_minute=1,
            )
        ]
    body = "".join(chunks)
    assert "rate limit (1/min, scope=per_request)" in body
    assert any("api.agent_loop.rate_limited" in r.message for r in caplog.records)
