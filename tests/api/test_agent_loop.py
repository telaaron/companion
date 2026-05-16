"""Integration tests for the non-streaming agent loop."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from api.agent.agent_loop import run_agent_to_completion
from api.models.anthropic import Message, MessagesRequest
from core.tools.workspace import Workspace


def _event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _text_turn(text: str, *, stop_reason: str = "end_turn") -> list[str]:
    return [
        _event(
            "message_start",
            {"type": "message_start", "message": {"id": "msg", "model": "x"}},
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
            {"type": "message_delta", "delta": {"stop_reason": stop_reason}},
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]


def _tool_use_turn(
    tool_name: str, tool_input: dict[str, Any], *, tool_id: str = "toolu_1"
) -> list[str]:
    return [
        _event(
            "message_start",
            {"type": "message_start", "message": {"id": "msg", "model": "x"}},
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


class FakeProvider:
    """Provider stub that returns scripted SSE responses per turn."""

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
        if not self._turns:
            raise AssertionError("FakeProvider exhausted")
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


@pytest.mark.asyncio
async def test_single_turn_no_tool_returns_text(tmp_path: Path) -> None:
    provider = FakeProvider([_text_turn("hi there")])
    run = await run_agent_to_completion(
        _make_request(),
        provider,
        Workspace.create(tmp_path),
    )
    assert run.turns == 1
    assert run.halted_reason == "end_turn"
    assert run.invocations == []
    assert run.final_message.content_blocks[0]["text"] == "hi there"


@pytest.mark.asyncio
async def test_tool_use_turn_executes_then_continues(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello")
    provider = FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}),
            _text_turn("the file says hello"),
        ]
    )
    run = await run_agent_to_completion(
        _make_request(),
        provider,
        Workspace.create(tmp_path),
    )
    assert run.turns == 2
    assert run.halted_reason == "end_turn"
    assert len(run.invocations) == 1
    assert run.invocations[0].name == "Read"
    assert not run.invocations[0].result.is_error


@pytest.mark.asyncio
async def test_tool_error_continues_loop(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "missing.txt"}),
            _text_turn("could not read it"),
        ]
    )
    run = await run_agent_to_completion(
        _make_request(),
        provider,
        Workspace.create(tmp_path),
    )
    assert run.turns == 2
    assert run.invocations[0].result.is_error
    assert run.halted_reason == "end_turn"


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_block(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            _tool_use_turn("Bogus", {"x": 1}),
            _text_turn("noted"),
        ]
    )
    run = await run_agent_to_completion(
        _make_request(),
        provider,
        Workspace.create(tmp_path),
    )
    assert run.invocations[0].result.is_error
    assert "unknown tool" in run.invocations[0].result.content


@pytest.mark.asyncio
async def test_max_turns_cap_enforced(tmp_path: Path) -> None:
    looping_turns = [_tool_use_turn("Read", {"file_path": "f.txt"}) for _ in range(5)]
    (tmp_path / "f.txt").write_text("x")
    provider = FakeProvider(looping_turns)
    run = await run_agent_to_completion(
        _make_request(),
        provider,
        Workspace.create(tmp_path),
        max_turns=3,
    )
    assert run.turns == 3
    assert run.halted_reason == "max_turns"
    assert len(run.invocations) == 3


@pytest.mark.asyncio
async def test_request_tools_are_augmented_with_local_tools(tmp_path: Path) -> None:
    provider = FakeProvider([_text_turn("done")])
    request = _make_request()
    await run_agent_to_completion(
        request,
        provider,
        Workspace.create(tmp_path),
    )
    sent = provider.calls[0]
    tool_names = {t.name for t in (sent.tools or [])}
    assert {"Read", "Write", "LS", "Edit", "Glob", "Grep", "Bash"}.issubset(tool_names)


@pytest.mark.asyncio
async def test_tool_use_appends_assistant_and_user_messages(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hi")
    provider = FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}),
            _text_turn("done"),
        ]
    )
    await run_agent_to_completion(
        _make_request(),
        provider,
        Workspace.create(tmp_path),
    )
    second_request = provider.calls[1]
    msgs = second_request.messages
    assert len(msgs) == 3
    # Original user, assistant tool_use, user tool_result
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == "toolu_1"
