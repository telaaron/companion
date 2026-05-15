"""End-to-end TestClient verification of the full agent-mode request path.

Exercises ``POST /v1/messages`` against a real FastAPI app + ``ClaudeProxyService``
with a fake provider injected through dependency overrides. Confirms that:

* The agent loop fires when ``AGENT_MODE_ENABLED=true`` and the request
  carries no tools.
* Native ``tool_use`` blocks are dispatched to local executors and the side
  effect (file creation) actually lands on disk.
* The XML-style text tool-call form (DeepSeek-shaped) is promoted, dispatched,
  and produces the same on-disk effect.
* The response stream contains exactly one ``message_start`` /
  ``message_stop`` lifecycle plus the synthetic tool-result block.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_settings
from api.model_router import ModelRouter, ResolvedModel, RoutedMessagesRequest
from api.models.anthropic import MessagesRequest
from api.routes import get_proxy_service
from api.services import ClaudeProxyService
from config.settings import Settings
from providers.base import BaseProvider


def _event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _native_tool_use_turn(
    tool_name: str, tool_input: dict[str, Any], *, tool_id: str = "toolu_e2e"
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


def _xml_text_turn(xml_body: str) -> list[str]:
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
                "delta": {"type": "text_delta", "text": xml_body},
            },
        ),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event(
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        ),
        _event("message_stop", {"type": "message_stop"}),
    ]


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


class _FakeProvider:
    def __init__(self, turns: list[list[str]]) -> None:
        self._turns = turns

    def preflight_stream(self, *_a: Any, **_k: Any) -> None:
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


class _PinnedRouter(ModelRouter):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    def resolve_messages_request(
        self, request: MessagesRequest
    ) -> RoutedMessagesRequest:
        resolved = ResolvedModel(
            original_model=request.model,
            provider_id="deepseek",
            provider_model=request.model,
            provider_model_ref=f"deepseek/{request.model}",
            thinking_enabled=False,
        )
        routed = request.model_copy(deep=True)
        routed.model = resolved.provider_model
        return RoutedMessagesRequest(request=routed, resolved=resolved)


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    return tmp_path


def _settings(workspace: Path, *, agent_on: bool) -> Settings:
    overrides: dict[str, Any] = {"AGENT_DEFAULT_WORKSPACE": str(workspace)}
    if agent_on:
        overrides["AGENT_MODE_ENABLED"] = True
    return Settings(**overrides)


def _build_app(settings: Settings, provider: _FakeProvider) -> tuple[Any, TestClient]:
    app = create_app(lifespan_enabled=False)

    def _override_settings() -> Settings:
        return settings

    def _override_service() -> ClaudeProxyService:
        return ClaudeProxyService(
            settings,
            provider_getter=lambda _pid: cast(BaseProvider, provider),
            model_router=_PinnedRouter(settings),
        )

    app.dependency_overrides[get_settings] = _override_settings
    app.dependency_overrides[get_proxy_service] = _override_service
    return app, TestClient(app)


def _post_messages(client: TestClient, prompt: str) -> str:
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": prompt}],
    }
    with client.stream("POST", "/v1/messages", json=payload) as response:
        assert response.status_code == 200, response.text
        return "".join(chunk for chunk in response.iter_text())


# ---------------------- Native tool_use path ----------------------


def test_native_tool_use_creates_folder_on_disk(workspace_root: Path) -> None:
    settings = _settings(workspace_root, agent_on=True)
    target = workspace_root / "frühstück"
    provider = _FakeProvider(
        [
            _native_tool_use_turn("Bash", {"command": f"mkdir -p {target}"}),
            _text_turn("Folder created."),
        ]
    )
    app, client = _build_app(settings, provider)
    try:
        body = _post_messages(client, "erstelle ordner")
    finally:
        app.dependency_overrides.clear()

    assert target.is_dir(), "Bash tool did not create the folder on disk"
    assert "Bash(mkdir -p" in body
    assert body.count("event: message_start") == 1
    assert body.count("event: message_stop") == 1


# ---------------------- XML text-form path ----------------------


def test_xml_text_form_creates_folder_on_disk(workspace_root: Path) -> None:
    settings = _settings(workspace_root, agent_on=True)
    target = workspace_root / "frühstück"
    xml = (
        "Sure, I'll do that.\n"
        '<tool_calls><invoke name="Bash">'
        f'<parameter name="command">mkdir -p {target}</parameter>'
        "</invoke></tool_calls>"
    )
    provider = _FakeProvider([_xml_text_turn(xml), _text_turn("Done.")])
    app, client = _build_app(settings, provider)
    try:
        body = _post_messages(client, "erstelle ordner")
    finally:
        app.dependency_overrides.clear()

    assert target.is_dir(), (
        "XML-form tool call was not promoted + dispatched (folder missing)"
    )
    assert "Bash(mkdir -p" in body
    assert body.count("event: message_start") == 1
    assert body.count("event: message_stop") == 1


# ---------------------- Flag off → passthrough ----------------------


def test_agent_mode_off_does_not_engage(workspace_root: Path) -> None:
    settings = _settings(workspace_root, agent_on=False)
    target = workspace_root / "should-not-exist"
    xml = (
        f'<tool_calls><invoke name="Bash">'
        f'<parameter name="command">mkdir -p {target}</parameter>'
        "</invoke></tool_calls>"
    )
    provider = _FakeProvider([_xml_text_turn(xml)])
    app, client = _build_app(settings, provider)
    try:
        body = _post_messages(client, "do nothing")
    finally:
        app.dependency_overrides.clear()

    assert not target.exists()
    # Passthrough preserves model output verbatim, no synthetic Bash block.
    assert "Bash(mkdir" not in body
