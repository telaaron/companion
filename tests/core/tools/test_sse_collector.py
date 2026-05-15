"""Unit tests for the SSE collector (assistant-message reconstructor)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from core.tools.sse_collector import collect


def _event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _stream(events: list[str]) -> AsyncIterator[str]:
    for ev in events:
        yield ev


@pytest.mark.asyncio
async def test_collect_text_message() -> None:
    events = [
        _event(
            "message_start",
            {
                "type": "message_start",
                "message": {"id": "msg_1", "model": "x", "usage": {"input_tokens": 5}},
            },
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
                "delta": {"type": "text_delta", "text": "hello "},
            },
        ),
        _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "world"},
            },
        ),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 7},
            },
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]
    msg = await collect(_stream(events))
    assert msg.message_id == "msg_1"
    assert msg.stop_reason == "end_turn"
    assert msg.content_blocks == [{"type": "text", "text": "hello world"}]
    assert msg.usage["input_tokens"] == 5
    assert msg.usage["output_tokens"] == 7


@pytest.mark.asyncio
async def test_collect_tool_use_assembles_input_json() -> None:
    events = [
        _event(
            "message_start",
            {"type": "message_start", "message": {"id": "msg_2", "model": "x"}},
        ),
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
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
                "delta": {"type": "input_json_delta", "partial_json": '{"file_path"'},
            },
        ),
        _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": ': "x.txt"}'},
            },
        ),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event(
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]
    msg = await collect(_stream(events))
    assert msg.stop_reason == "tool_use"
    assert msg.tool_uses() == [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "Read",
            "input": {"file_path": "x.txt"},
        }
    ]


@pytest.mark.asyncio
async def test_collect_handles_multiple_blocks_in_order() -> None:
    events = [
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "second"},
            },
        ),
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": "first"},
            },
        ),
    ]
    msg = await collect(_stream(events))
    assert [b.get("text") for b in msg.content_blocks] == ["first", "second"]


@pytest.mark.asyncio
async def test_collect_captures_error() -> None:
    events = [_event("error", {"type": "error", "error": {"type": "overload"}})]
    msg = await collect(_stream(events))
    assert msg.error == {"type": "overload"}
