"""Integration tests for the streaming (SSE-interleaved) agent loop."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from api.agent.agent_loop_streaming import run_agent_streaming
from api.models.anthropic import Message, MessagesRequest
from core.tools.workspace import Workspace


def _event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _text_turn(text: str) -> list[str]:
    return [
        _event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg",
                    "model": "x",
                    "usage": {"input_tokens": 4, "output_tokens": 0},
                },
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
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 6},
            },
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]


def _tool_use_turn(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    return [
        _event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg",
                    "model": "x",
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                },
            },
        ),
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
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
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 10},
            },
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]


class FakeProvider:
    def __init__(self, turns: list[list[str]]) -> None:
        self._turns = turns
        self.calls: list[Any] = []

    def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        self.calls.append(request)
        events = self._turns.pop(0)

        async def _gen() -> AsyncIterator[str]:
            for ev in events:
                yield ev

        return _gen()


def _make_request() -> MessagesRequest:
    return MessagesRequest(
        model="x",
        max_tokens=128,
        messages=[Message(role="user", content="go")],
    )


def _parse_events(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    parsed: list[tuple[str, dict[str, Any]]] = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            block = block.strip("\n")
            if not block:
                continue
            event_type = ""
            data_payload = ""
            for line in block.split("\n"):
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_payload += line[5:].strip()
            parsed.append((event_type, json.loads(data_payload)))
    return parsed


async def _drain(stream: AsyncIterator[str]) -> list[str]:
    return [c async for c in stream]


@pytest.mark.asyncio
async def test_single_turn_text_streams_through(tmp_path: Path) -> None:
    provider = FakeProvider([_text_turn("hi there")])
    chunks = await _drain(
        run_agent_streaming(_make_request(), provider, Workspace.create(tmp_path))
    )
    events = _parse_events(chunks)
    types = [t for t, _ in events]
    assert types[0] == "message_start"
    assert types[-1] == "message_stop"
    assert types.count("message_start") == 1
    assert types.count("message_stop") == 1


@pytest.mark.asyncio
async def test_tool_use_interleaves_synthetic_block(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello")
    provider = FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}),
            _text_turn("done"),
        ]
    )
    chunks = await _drain(
        run_agent_streaming(_make_request(), provider, Workspace.create(tmp_path))
    )
    events = _parse_events(chunks)

    block_starts = [d for t, d in events if t == "content_block_start"]
    indices = [b["index"] for b in block_starts]
    assert indices == sorted(indices)
    assert len(set(indices)) == len(indices)

    types_of_blocks = [b["content_block"]["type"] for b in block_starts]
    assert types_of_blocks[0] == "tool_use"
    assert "text" in types_of_blocks[1:]

    types = [t for t, _ in events]
    assert types.count("message_start") == 1
    assert types.count("message_stop") == 1
    assert types.count("message_delta") == 1


@pytest.mark.asyncio
async def test_synthetic_block_text_summarises_tool(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello")
    provider = FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}),
            _text_turn("done"),
        ]
    )
    chunks = await _drain(
        run_agent_streaming(_make_request(), provider, Workspace.create(tmp_path))
    )
    events = _parse_events(chunks)
    text_deltas = [
        d["delta"]["text"]
        for t, d in events
        if t == "content_block_delta" and d.get("delta", {}).get("type") == "text_delta"
    ]
    joined = "".join(text_deltas)
    assert "Read(f.txt)" in joined
    assert "hello" in joined or "1\thello" in joined


@pytest.mark.asyncio
async def test_synthetic_block_marks_errors(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "missing.txt"}),
            _text_turn("sorry"),
        ]
    )
    chunks = await _drain(
        run_agent_streaming(_make_request(), provider, Workspace.create(tmp_path))
    )
    events = _parse_events(chunks)
    text_deltas = "".join(
        d["delta"]["text"]
        for t, d in events
        if t == "content_block_delta" and d.get("delta", {}).get("type") == "text_delta"
    )
    assert "✗" in text_deltas
    assert "not found" in text_deltas


@pytest.mark.asyncio
async def test_indices_are_globally_unique_across_turns(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    provider = FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}),
            _text_turn("ok"),
        ]
    )
    chunks = await _drain(
        run_agent_streaming(_make_request(), provider, Workspace.create(tmp_path))
    )
    events = _parse_events(chunks)
    starts = [d["index"] for t, d in events if t == "content_block_start"]
    assert len(starts) == len(set(starts))
    assert starts == sorted(starts)


@pytest.mark.asyncio
async def test_max_turns_emits_terminal_lifecycle(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    provider = FakeProvider(
        [_tool_use_turn("Read", {"file_path": "f.txt"}) for _ in range(5)]
    )
    chunks = await _drain(
        run_agent_streaming(
            _make_request(), provider, Workspace.create(tmp_path), max_turns=2
        )
    )
    events = _parse_events(chunks)
    final_delta = [d for t, d in events if t == "message_delta"]
    assert final_delta
    assert final_delta[-1]["delta"]["stop_reason"] == "max_turns"
    assert events[-1][0] == "message_stop"


@pytest.mark.asyncio
async def test_aggregated_usage_in_final_delta(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hi")
    provider = FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}),
            _text_turn("done"),
        ]
    )
    chunks = await _drain(
        run_agent_streaming(_make_request(), provider, Workspace.create(tmp_path))
    )
    events = _parse_events(chunks)
    final_delta = [d for t, d in events if t == "message_delta"][-1]
    usage = final_delta["usage"]
    # input across both turns: 5 + 4 = 9
    assert usage["input_tokens"] == 9
    # output across both: 10 + 6 = 16
    assert usage["output_tokens"] == 16
