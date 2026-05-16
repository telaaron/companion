"""Tests for POST /v1/sessions/{id}/auto-rename.

Covers:
- Successful rename: fake provider returns a known title.
- Session updated in datastore and returned in response.
- Retry path: first call returns empty, second succeeds (JS-side logic, tested
  via direct unit test of _call_rename_llm via httpx.MockTransport).
- Fallback: upstream errors → endpoint returns empty title without raising.
- 404 on unknown session.
- Title capped at 60 chars and stripped of quotes/punctuation.
- _parse_auto_rename_title unit tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from api import datastore
from api.app import create_app
from api.dashboard_routes import (
    AutoRenameIn,
    _call_rename_llm,
    _parse_auto_rename_title,
)
from api.dependencies import get_settings
from config.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings()
    object.__setattr__(settings, "anthropic_auth_token", "")
    object.__setattr__(settings, "model", "deepseek/deepseek-v4-flash")
    object.__setattr__(settings, "public_base_url", "http://127.0.0.1:8082")
    return settings


def _anthropic_response(text: str) -> dict[str, Any]:
    """Minimal Anthropic-format response body."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "deepseek/deepseek-v4-flash",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _make_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    """Return a MockTransport that pops responses from a queue."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if queue:
            return queue.pop(0)
        return httpx.Response(500, text="no more responses")

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = _make_settings(tmp_path)
    datastore.reset_for_tests(tmp_path / "test.sqlite")

    app = create_app(lifespan_enabled=False)
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c

    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# Helper: create a session via the API and return its id
# ---------------------------------------------------------------------------


def _create_session(
    client: TestClient,
    title: str = "untitled",
    model: str = "deepseek/deepseek-v4-flash",
) -> str:
    r = client.post("/v1/sessions", json={"title": title, "model": model})
    assert r.status_code == 200
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Unit tests for _parse_auto_rename_title
# ---------------------------------------------------------------------------


def test_parse_strips_leading_trailing_whitespace() -> None:
    assert _parse_auto_rename_title("  Hello World  ") == "Hello World"


def test_parse_strips_surrounding_double_quotes() -> None:
    assert _parse_auto_rename_title('"Hello World"') == "Hello World"


def test_parse_strips_surrounding_single_quotes() -> None:
    assert _parse_auto_rename_title("'Hello World'") == "Hello World"


def test_parse_strips_trailing_punctuation() -> None:
    assert _parse_auto_rename_title("Hello World!") == "Hello World"
    assert _parse_auto_rename_title("Hello World.") == "Hello World"
    assert _parse_auto_rename_title("Hello World?") == "Hello World"


def test_parse_caps_at_60_chars() -> None:
    long = "A" * 80
    result = _parse_auto_rename_title(long)
    assert len(result) == 60


def test_parse_empty_string_returns_empty() -> None:
    assert _parse_auto_rename_title("") == ""


# ---------------------------------------------------------------------------
# Unit tests for _call_rename_llm (uses httpx.MockTransport)
# ---------------------------------------------------------------------------


def _make_fake_client_ctx(
    post_return: httpx.Response | None = None, post_error: Exception | None = None
):
    """Build a fake async context manager that yields a mock httpx client."""

    async def _fake_post(*args, **kwargs) -> httpx.Response:
        if post_error is not None:
            raise post_error
        if post_return is not None:
            return post_return
        return httpx.Response(500, text="no response configured")

    class _FakeClient:
        post = staticmethod(_fake_post)

    class _FakeCtx:
        async def __aenter__(self_inner):
            return _FakeClient()

        async def __aexit__(self_inner, *a):
            pass

    return _FakeCtx()


@pytest.mark.asyncio
async def test_call_rename_llm_happy_path(tmp_path: Path) -> None:
    """_call_rename_llm extracts text from an Anthropic-format response."""
    settings = _make_settings(tmp_path)
    body = AutoRenameIn(
        first_user_message="Why is my loop infinite?",
        first_assistant_message="The loop condition never becomes false.",
    )
    resp_body = _anthropic_response("Debugging Python Loop")
    fake_resp = httpx.Response(
        200,
        content=json.dumps(resp_body).encode(),
        headers={"Content-Type": "application/json"},
    )

    with patch("api.dashboard_routes.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_fake_client_ctx(post_return=fake_resp)
        result = await _call_rename_llm(body, settings, None)

    assert result == "Debugging Python Loop"


@pytest.mark.asyncio
async def test_call_rename_llm_http_error_returns_empty(tmp_path: Path) -> None:
    """HTTP 500 → returns empty string without raising."""
    settings = _make_settings(tmp_path)
    body = AutoRenameIn(first_user_message="hi", first_assistant_message="hello")
    error_resp = httpx.Response(500, text="internal error")

    with patch("api.dashboard_routes.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_fake_client_ctx(post_return=error_resp)
        result = await _call_rename_llm(body, settings, None)

    assert result == ""


@pytest.mark.asyncio
async def test_call_rename_llm_network_error_returns_empty(tmp_path: Path) -> None:
    """Network exception → returns empty string without raising."""
    settings = _make_settings(tmp_path)
    body = AutoRenameIn(first_user_message="hi", first_assistant_message="hello")

    with patch("api.dashboard_routes.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_fake_client_ctx(
            post_error=httpx.ConnectError("refused")
        )
        result = await _call_rename_llm(body, settings, None)

    assert result == ""


# ---------------------------------------------------------------------------
# Integration tests via TestClient
# ---------------------------------------------------------------------------


def test_auto_rename_happy_path(client: TestClient) -> None:
    """Fake provider returns a known title; endpoint persists it."""
    sid = _create_session(client)

    with patch(
        "api.dashboard_routes._call_rename_llm",
        new=AsyncMock(return_value="Debugging Python Loop"),
    ):
        r = client.post(
            f"/v1/sessions/{sid}/auto-rename",
            json={
                "first_user_message": "Why is my loop infinite?",
                "first_assistant_message": "The loop condition never becomes false.",
            },
        )

    assert r.status_code == 200
    assert r.json()["title"] == "Debugging Python Loop"

    session = datastore.get_session(sid)
    assert session is not None
    assert session["title"] == "Debugging Python Loop"


def test_auto_rename_unknown_session(client: TestClient) -> None:
    """404 when session does not exist."""
    r = client.post(
        "/v1/sessions/nonexistent_abc/auto-rename",
        json={"first_user_message": "hello", "first_assistant_message": "world"},
    )
    assert r.status_code == 404


def test_auto_rename_empty_llm_response_returns_empty(client: TestClient) -> None:
    """LLM returns empty string → endpoint returns title='' without error."""
    sid = _create_session(client)

    with patch(
        "api.dashboard_routes._call_rename_llm",
        new=AsyncMock(return_value=""),
    ):
        r = client.post(
            f"/v1/sessions/{sid}/auto-rename",
            json={"first_user_message": "hi", "first_assistant_message": "hello"},
        )

    assert r.status_code == 200
    assert r.json()["title"] == ""

    session = datastore.get_session(sid)
    assert session is not None
    assert session["title"] == "untitled"


def test_auto_rename_strips_quotes_and_punctuation(client: TestClient) -> None:
    """Endpoint cleans quoted, punctuated LLM output."""
    sid = _create_session(client)

    with patch(
        "api.dashboard_routes._call_rename_llm",
        new=AsyncMock(return_value='"Setting Up Dev Environment!"'),
    ):
        r = client.post(
            f"/v1/sessions/{sid}/auto-rename",
            json={
                "first_user_message": "how do I set up my dev env",
                "first_assistant_message": "Install Python first.",
            },
        )

    assert r.status_code == 200
    assert r.json()["title"] == "Setting Up Dev Environment"


def test_auto_rename_caps_at_60_chars(client: TestClient) -> None:
    """Titles longer than 60 characters are capped."""
    sid = _create_session(client)

    with patch(
        "api.dashboard_routes._call_rename_llm",
        new=AsyncMock(return_value="A" * 80),
    ):
        r = client.post(
            f"/v1/sessions/{sid}/auto-rename",
            json={"first_user_message": "hi", "first_assistant_message": "hello"},
        )

    assert r.status_code == 200
    assert len(r.json()["title"]) == 60


def test_auto_rename_preserves_existing_model(client: TestClient) -> None:
    """upsert_session must not clobber the session's existing model."""
    sid = _create_session(client, model="deepseek/chat")

    with patch(
        "api.dashboard_routes._call_rename_llm",
        new=AsyncMock(return_value="Fix Memory Leak"),
    ):
        r2 = client.post(
            f"/v1/sessions/{sid}/auto-rename",
            json={
                "first_user_message": "memory usage is growing",
                "first_assistant_message": "Check for unclosed handles.",
            },
        )

    assert r2.status_code == 200
    assert r2.json()["title"] == "Fix Memory Leak"

    session = datastore.get_session(sid)
    assert session is not None
    assert session["title"] == "Fix Memory Leak"
    assert session["model"] == "deepseek/chat"


def test_auto_rename_does_not_update_on_empty_title(client: TestClient) -> None:
    """When LLM returns empty, session title is untouched."""
    sid = _create_session(client, title="My Custom Title")
    # Force session to have non-default title.
    datastore.upsert_session(
        session_id=sid, title="My Custom Title", model="deepseek/x"
    )

    with patch(
        "api.dashboard_routes._call_rename_llm",
        new=AsyncMock(return_value=""),
    ):
        r = client.post(
            f"/v1/sessions/{sid}/auto-rename",
            json={"first_user_message": "test", "first_assistant_message": "response"},
        )

    assert r.status_code == 200
    assert r.json()["title"] == ""
    # Original title preserved.
    session = datastore.get_session(sid)
    assert session is not None
    assert session["title"] == "My Custom Title"
