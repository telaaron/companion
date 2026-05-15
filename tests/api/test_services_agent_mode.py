"""Tests for ClaudeProxyService routing into the agent loop."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.responses import StreamingResponse

from api.model_router import ModelRouter, ResolvedModel, RoutedMessagesRequest
from api.models.anthropic import Message, MessagesRequest, Tool
from api.services import ClaudeProxyService
from config.settings import Settings


class _PinnedRouter(ModelRouter):
    def __init__(self, settings: Settings, provider_id: str) -> None:
        super().__init__(settings)
        self._provider_id = provider_id

    def resolve_messages_request(
        self, request: MessagesRequest
    ) -> RoutedMessagesRequest:
        resolved = ResolvedModel(
            original_model=request.model,
            provider_id=self._provider_id,
            provider_model=request.model,
            provider_model_ref=f"{self._provider_id}/{request.model}",
            thinking_enabled=False,
        )
        routed = request.model_copy(deep=True)
        routed.model = resolved.provider_model
        return RoutedMessagesRequest(request=routed, resolved=resolved)


def _event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _text_turn(text: str) -> list[str]:
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
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]


def _tool_use_turn(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
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
                    "id": "toolu_x",
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

    def preflight_stream(self, request: Any, *, thinking_enabled: bool | None = None):
        return None

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


def _make_request(*, with_tool: bool = False) -> MessagesRequest:
    kwargs: dict[str, Any] = {
        "model": "claude-x",
        "max_tokens": 64,
        "messages": [Message(role="user", content="hi")],
    }
    if with_tool:
        kwargs["tools"] = [
            Tool(
                name="custom",
                description="d",
                input_schema={"type": "object", "properties": {}},
            )
        ]
    return MessagesRequest(**kwargs)


def _service(settings: Settings, provider: Any) -> ClaudeProxyService:
    return ClaudeProxyService(
        settings,
        provider_getter=lambda _: provider,
        model_router=_PinnedRouter(settings, "deepseek"),
    )


def _settings(*, agent_on: bool, workspace: str = "") -> Settings:
    overrides: dict[str, Any] = {}
    if agent_on:
        overrides["AGENT_MODE_ENABLED"] = True
    if workspace:
        overrides["AGENT_DEFAULT_WORKSPACE"] = workspace
    return Settings(**overrides)


def test_should_use_agent_loop_disabled_when_flag_off() -> None:
    service = _service(_settings(agent_on=False), MagicMock())
    assert service._should_use_agent_loop(_make_request()) is False


def test_should_use_agent_loop_disabled_when_request_carries_tools() -> None:
    service = _service(_settings(agent_on=True), MagicMock())
    assert service._should_use_agent_loop(_make_request(with_tool=True)) is False


def test_should_use_agent_loop_enabled_when_flag_on_and_no_tools() -> None:
    service = _service(_settings(agent_on=True), MagicMock())
    assert service._should_use_agent_loop(_make_request()) is True


@pytest.mark.asyncio
async def test_agent_loop_streams_through_service(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello")
    settings = _settings(agent_on=True, workspace=str(tmp_path))
    provider = _FakeProvider(
        [
            _tool_use_turn("Read", {"file_path": "f.txt"}),
            _text_turn("done"),
        ]
    )
    service = _service(settings, provider)
    response = service.create_message(_make_request())
    assert isinstance(response, StreamingResponse)

    body_chunks: list[str] = [
        bytes(chunk).decode("utf-8")
        if isinstance(chunk, (bytes, bytearray, memoryview))
        else chunk
        async for chunk in response.body_iterator
    ]
    body = "".join(body_chunks)
    assert "Read(f.txt)" in body
    assert "hello" in body
    assert body.count("event: message_start") == 1
    assert body.count("event: message_stop") == 1


@pytest.mark.asyncio
async def test_agent_loop_skipped_when_flag_off_uses_passthrough(
    tmp_path: Path,
) -> None:
    settings = _settings(agent_on=False)
    provider = _FakeProvider([_text_turn("plain")])
    service = _service(settings, provider)
    response = service.create_message(_make_request())
    assert isinstance(response, StreamingResponse)

    body_chunks: list[str] = [
        bytes(chunk).decode("utf-8")
        if isinstance(chunk, (bytes, bytearray, memoryview))
        else chunk
        async for chunk in response.body_iterator
    ]
    body = "".join(body_chunks)
    # Passthrough preserves upstream's lifecycle but does NOT inject tool blocks
    assert "Read(" not in body
    assert "plain" in body
