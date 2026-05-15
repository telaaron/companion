"""Tests for the XML-style tool-call text parser + agent-loop promotion."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from api.agent.agent_loop_streaming import run_agent_streaming
from api.agent.text_tool_parser import extract_text_form_tool_uses
from api.models.anthropic import Message, MessagesRequest
from core.tools.workspace import Workspace

# ---------------------- Parser unit tests ----------------------


def test_parser_returns_empty_when_no_invoke_tag() -> None:
    text, blocks = extract_text_form_tool_uses("hello world")
    assert text == "hello world"
    assert blocks == []


def test_parser_extracts_single_invocation() -> None:
    raw = (
        "I'll do it.\n"
        "<tool_calls>\n"
        '<invoke name="Bash">\n'
        '<parameter name="command">mkdir -p ~/foo</parameter>\n'
        "</invoke>\n"
        "</tool_calls>\n"
    )
    text, blocks = extract_text_form_tool_uses(raw)
    assert "I'll do it." in text
    assert "<invoke" not in text
    assert len(blocks) == 1
    assert blocks[0]["name"] == "Bash"
    assert blocks[0]["input"] == {"command": "mkdir -p ~/foo"}
    assert blocks[0]["type"] == "tool_use"
    assert blocks[0]["id"].startswith("toolu_text_")


def test_parser_handles_multiple_parameters() -> None:
    raw = (
        '<invoke name="Edit">\n'
        '<parameter name="file_path">a.txt</parameter>\n'
        '<parameter name="old_string">x</parameter>\n'
        '<parameter name="new_string">y</parameter>\n'
        "</invoke>"
    )
    _, blocks = extract_text_form_tool_uses(raw)
    assert blocks[0]["input"] == {
        "file_path": "a.txt",
        "old_string": "x",
        "new_string": "y",
    }


def test_parser_ignores_attribute_clutter_on_parameter_tag() -> None:
    raw = (
        '<invoke name="Bash">'
        '<parameter name="command" string="true">echo hi</parameter>'
        "</invoke>"
    )
    _, blocks = extract_text_form_tool_uses(raw)
    assert blocks[0]["input"] == {"command": "echo hi"}


def test_parser_extracts_multiple_invocations() -> None:
    raw = (
        "<tool_calls>"
        '<invoke name="Bash"><parameter name="command">ls</parameter></invoke>'
        '<invoke name="Bash"><parameter name="command">pwd</parameter></invoke>'
        "</tool_calls>"
    )
    _, blocks = extract_text_form_tool_uses(raw)
    assert len(blocks) == 2
    assert {b["input"]["command"] for b in blocks} == {"ls", "pwd"}


# ---------------------- Streaming agent-loop integration ----------------------


def _event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _text_only_turn(text: str, *, stop_reason: str = "end_turn") -> list[str]:
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
            {"type": "message_delta", "delta": {"stop_reason": stop_reason}},
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]


class _FakeProvider:
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


def _request() -> MessagesRequest:
    return MessagesRequest(
        model="x",
        max_tokens=64,
        messages=[Message(role="user", content="erstelle ordner")],
    )


@pytest.mark.asyncio
async def test_streaming_loop_promotes_text_invoke_to_dispatch(
    tmp_path: Path,
) -> None:
    xml_text = (
        "I'll create the folder.\n"
        '<tool_calls><invoke name="Bash">'
        f'<parameter name="command">mkdir -p {tmp_path}/frühstück</parameter>'
        "</invoke></tool_calls>"
    )
    provider = _FakeProvider(
        [
            _text_only_turn(xml_text, stop_reason="end_turn"),
            _text_only_turn("Done.", stop_reason="end_turn"),
        ]
    )
    chunks: list[str] = [
        c
        async for c in run_agent_streaming(
            _request(), provider, Workspace.create(tmp_path)
        )
    ]
    body = "".join(chunks)

    # Folder must exist on disk.
    assert (tmp_path / "frühstück").is_dir()
    # Synthetic tool-result block emitted with Bash label.
    assert "Bash(mkdir -p" in body
    # Both upstream calls should have happened (initial + continuation).
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_streaming_loop_no_text_tool_call_passes_through(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider([_text_only_turn("plain answer")])
    chunks: list[str] = [
        c
        async for c in run_agent_streaming(
            _request(), provider, Workspace.create(tmp_path)
        )
    ]
    body = "".join(chunks)
    assert "plain answer" in body
    # No synthetic tool block should appear.
    assert "⏺ " not in body and "✗ " not in body
