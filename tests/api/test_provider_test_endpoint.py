"""Tests for POST /v1/providers/{id}/test.

Covers:
- Happy path: fake provider yields SSE chunks → ok=True with duration_ms.
- Provider raises an exception → ok=False with error string.
- Unknown provider id → ok=False with "unknown provider" error.
- Timeout: provider call exceeds 5 s → ok=False with timeout error.
- Never returns HTTP 5xx — all errors wrapped as JSON.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import datastore
from api.app import create_app
from api.dependencies import get_settings
from config.settings import Settings
from providers.base import BaseProvider, ProviderConfig
from providers.model_listing import ProviderModelInfo
from providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings()
    object.__setattr__(settings, "anthropic_auth_token", "")
    return settings


def _make_registry(provider: BaseProvider, provider_id: str) -> ProviderRegistry:
    reg: dict[str, BaseProvider] = {provider_id: provider}
    return ProviderRegistry(providers=reg)


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class _OkProvider(BaseProvider):
    """Returns a minimal Anthropic SSE message_start chunk then stops."""

    def __init__(self) -> None:
        config = ProviderConfig(api_key="test", base_url="http://localhost")
        super().__init__(config)

    async def cleanup(self) -> None:
        pass

    async def list_model_ids(self) -> frozenset[str]:
        return frozenset(["test-model"])

    async def list_model_infos(self) -> frozenset[ProviderModelInfo]:
        return frozenset([ProviderModelInfo(model_id="test-model")])

    async def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        yield 'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_test","type":"message","role":"assistant","model":"test-model","content":[],"stop_reason":null,"usage":{"input_tokens":1,"output_tokens":0}}}\n\n'
        yield 'event: message_stop\ndata: {"type":"message_stop"}\n\n'


class _ErrorProvider(BaseProvider):
    """Raises an exception on stream_response."""

    def __init__(self) -> None:
        config = ProviderConfig(api_key="test", base_url="http://localhost")
        super().__init__(config)

    async def cleanup(self) -> None:
        pass

    async def list_model_ids(self) -> frozenset[str]:
        return frozenset()

    async def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        raise RuntimeError("401 invalid api key")
        if False:
            yield ""


class _SlowProvider(BaseProvider):
    """Hangs forever on stream_response to trigger timeout."""

    def __init__(self) -> None:
        config = ProviderConfig(api_key="test", base_url="http://localhost")
        super().__init__(config)

    async def cleanup(self) -> None:
        pass

    async def list_model_ids(self) -> frozenset[str]:
        return frozenset()

    async def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        await asyncio.sleep(9999)
        if False:
            yield ""


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

    with TestClient(app, client=("127.0.0.1", 50001)) as c:
        yield c, app

    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_provider_test_ok(client: tuple[TestClient, Any]) -> None:
    """Fake provider returns chunks → endpoint returns ok=True with duration_ms."""
    c, app = client
    app.state.provider_registry = _make_registry(_OkProvider(), "deepseek")

    r = c.post("/v1/providers/deepseek/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["duration_ms"], int)
    assert body["duration_ms"] >= 0
    assert body["error"] is None


def test_provider_test_error_provider(client: tuple[TestClient, Any]) -> None:
    """Provider raises → ok=False with error string; never HTTP 5xx."""
    c, app = client
    app.state.provider_registry = _make_registry(_ErrorProvider(), "deepseek")

    r = c.post("/v1/providers/deepseek/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "401" in body["error"] or "invalid api key" in body["error"]
    assert body["duration_ms"] >= 0


def test_provider_test_unknown_provider(client: tuple[TestClient, Any]) -> None:
    """Unknown provider id → ok=False, never HTTP 5xx."""
    c, app = client
    # Registry is empty — no provider registered for "totally_unknown_xyz".
    app.state.provider_registry = ProviderRegistry()

    r = c.post("/v1/providers/totally_unknown_xyz/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] is not None


def test_provider_test_timeout(client: tuple[TestClient, Any]) -> None:
    """Slow provider hits the 5 s asyncio timeout → ok=False, no HTTP 500."""
    c, app = client
    app.state.provider_registry = _make_registry(_SlowProvider(), "deepseek")

    # Patch the timeout to 0.01 s so the test runs fast.
    with patch("api.dashboard_routes._PROVIDER_TEST_TIMEOUT_S", 0.01):
        r = c.post("/v1/providers/deepseek/test")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "timed out" in body["error"]


def test_provider_test_echoes_model(client: tuple[TestClient, Any]) -> None:
    """When provider SSE includes a model field, echo_model is populated."""
    c, app = client
    app.state.provider_registry = _make_registry(_OkProvider(), "deepseek")

    r = c.post("/v1/providers/deepseek/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["echo_model"] == "test-model"
