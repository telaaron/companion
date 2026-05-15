"""Tests for the capability scanner + admin endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from api.capabilities import scan, to_payload
from config.settings import Settings


def _local_client(app: Any) -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


def _settings(**overrides: Any) -> Settings:
    """Build a Settings instance and post-zero conftest-injected creds.

    The repo conftest sets ``NVIDIA_NIM_API_KEY=test_key`` and the env wins
    over kwargs, so we instantiate then mutate the secret fields back to "".
    Tests can then opt back in via the `_with` helper or pass the alias name.
    """
    settings = Settings(**overrides)
    for field_name in (
        "nvidia_nim_api_key",
        "open_router_api_key",
        "deepseek_api_key",
        "kimi_api_key",
        "zai_api_key",
        "opencode_api_key",
        "wafer_api_key",
    ):
        alias_upper = field_name.upper()
        if alias_upper in overrides:
            object.__setattr__(settings, field_name, overrides[alias_upper])
        else:
            object.__setattr__(settings, field_name, "")
    return settings


# ---------------------- Scanner unit tests ----------------------


def test_scan_suggests_provider_when_none_configured() -> None:
    items = scan(_settings())
    titles = {c.title for c in items}
    assert "Connect a chat provider" in titles


def test_scan_marks_provider_active_when_key_set() -> None:
    items = scan(_settings(DEEPSEEK_API_KEY="sk-deepseekkeyXYZ"))
    actives = [c for c in items if c.status == "active" and "DeepSeek" in c.title]
    assert actives, "expected DeepSeek active card"


def test_scan_suggests_agent_mode_when_disabled() -> None:
    items = scan(_settings())
    suggested = {c.id for c in items if c.status == "suggested"}
    assert "agent_mode_off" in suggested


def test_scan_marks_agent_mode_active_with_workspace(tmp_path: Path) -> None:
    items = scan(
        _settings(
            AGENT_MODE_ENABLED=True,
            AGENT_DEFAULT_WORKSPACE=str(tmp_path),
        )
    )
    actives = [c for c in items if c.id == "agent_mode"]
    assert actives and actives[0].status == "active"


def test_scan_flags_missing_workspace_when_agent_on() -> None:
    items = scan(
        _settings(
            AGENT_MODE_ENABLED=True,
            AGENT_DEFAULT_WORKSPACE="/definitely/does/not/exist/anywhere",
        )
    )
    inactives = {c.id for c in items if c.status == "inactive"}
    assert "agent_workspace_missing" in inactives


def test_scan_suggests_denylist_when_empty() -> None:
    items = scan(
        _settings(
            AGENT_MODE_ENABLED=True,
            AGENT_DEFAULT_WORKSPACE="/tmp",
        )
    )
    suggested = {c.id for c in items if c.status == "suggested"}
    assert "agent_bash_denylist_empty" in suggested
    assert "agent_global_rate" in suggested


def test_scan_flags_open_token_warning() -> None:
    items = scan(_settings())
    inactives = {c.id for c in items if c.status == "inactive"}
    assert "auth_token_missing" in inactives


def test_to_payload_groups_by_status(tmp_path: Path) -> None:
    items = scan(
        _settings(
            DEEPSEEK_API_KEY="sk-x",
            ANTHROPIC_AUTH_TOKEN="freecc",
            AGENT_MODE_ENABLED=True,
            AGENT_DEFAULT_WORKSPACE=str(tmp_path),
        )
    )
    payload = to_payload(items)
    assert {"active", "inactive", "suggested"} == set(payload.keys())
    assert any(c["title"] == "Agent mode active" for c in payload["active"])


# ---------------------- Endpoint tests ----------------------


def test_capabilities_endpoint_returns_grouped_payload(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    app = create_app(lifespan_enabled=False)
    response = _local_client(app).get("/admin/api/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert {"active", "inactive", "suggested"} == set(body.keys())


def test_capabilities_endpoint_loopback_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    app = create_app(lifespan_enabled=False)
    remote = TestClient(app, client=("203.0.113.10", 50000))
    response = remote.get("/admin/api/capabilities")
    assert response.status_code == 403


def test_onboarding_route_serves_wizard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    app = create_app(lifespan_enabled=False)
    response = _local_client(app).get("/admin/onboarding")
    assert response.status_code == 200
    assert b"wizard.js" in response.content


def test_wizard_assets_served(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    app = create_app(lifespan_enabled=False)
    client = _local_client(app)
    assert client.get("/admin/assets/wizard.html").status_code == 200
    assert client.get("/admin/assets/wizard.js").status_code == 200
