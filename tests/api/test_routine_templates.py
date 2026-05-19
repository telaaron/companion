"""Tests for routine template library (roadmap 6.3).

Covers:
- GET  /v1/routines/templates  — returns all 4 starter templates.
- POST /v1/routines/from-template — substitution, unknown slug → 404,
  missing required input → 422.
- Template loader caches and returns valid YAML dicts.
- {{var}} substitution replaces placeholders correctly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api import datastore
from api.app import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_db(tmp_path: Path):
    datastore.reset_for_tests(tmp_path / "templates_test.sqlite")
    yield
    datastore.reset_for_tests()


@pytest.fixture
def app_client(fresh_db):
    # Reset template cache so each test loads fresh
    import api.dashboard_routes as dr

    dr._templates_cache = None
    app = create_app()
    mock_provider = MagicMock()
    mock_provider.stream_response = AsyncMock(return_value=iter([]))
    with (
        patch("api.dependencies.resolve_provider", return_value=mock_provider),
        patch(
            "providers.registry.ProviderRegistry.validate_configured_models",
            new_callable=AsyncMock,
        ),
        patch("providers.registry.ProviderRegistry.start_model_list_refresh"),
        TestClient(app) as client,
    ):
        yield client


_AUTH = {"Authorization": "Bearer freecc"}

# ---------------------------------------------------------------------------
# GET /v1/routines/templates
# ---------------------------------------------------------------------------


def test_list_templates_returns_all_four(app_client: TestClient) -> None:
    resp = app_client.get("/v1/routines/templates", headers=_AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data
    slugs = {t["slug"] for t in data["templates"]}
    assert "daily-news-digest" in slugs
    assert "refactor-todo-sweeper" in slugs
    assert "inbox-triage" in slugs
    assert "standup-notes-summarizer" in slugs


def test_list_templates_each_has_required_fields(app_client: TestClient) -> None:
    resp = app_client.get("/v1/routines/templates", headers=_AUTH)
    assert resp.status_code == 200
    for tpl in resp.json()["templates"]:
        assert "slug" in tpl
        assert "name" in tpl
        assert "description" in tpl
        assert "trigger" in tpl
        assert "payload" in tpl
        assert "inputs" in tpl


def test_list_templates_auth_required(fresh_db: None) -> None:
    import api.dashboard_routes as dr

    dr._templates_cache = None
    app = create_app()
    mock_provider = MagicMock()
    mock_provider.stream_response = AsyncMock(return_value=iter([]))
    with (
        patch("api.dependencies.resolve_provider", return_value=mock_provider),
        patch(
            "providers.registry.ProviderRegistry.validate_configured_models",
            new_callable=AsyncMock,
        ),
        patch("providers.registry.ProviderRegistry.start_model_list_refresh"),
        TestClient(app) as client,
    ):
        import os

        old = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "secret"
        from config.settings import get_settings as _get

        _get.cache_clear()
        try:
            resp = client.get(
                "/v1/routines/templates", headers={"Authorization": "Bearer wrong"}
            )
            assert resp.status_code == 401
        finally:
            if old is None:
                del os.environ["ANTHROPIC_AUTH_TOKEN"]
            else:
                os.environ["ANTHROPIC_AUTH_TOKEN"] = old
            _get.cache_clear()


# ---------------------------------------------------------------------------
# POST /v1/routines/from-template — happy path
# ---------------------------------------------------------------------------


def test_from_template_daily_news_digest(app_client: TestClient) -> None:
    resp = app_client.post(
        "/v1/routines/from-template",
        json={
            "slug": "daily-news-digest",
            "inputs": {"feeds": "https://example.com/rss"},
        },
        headers=_AUTH,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"].startswith("rtn_")
    assert data["name"] == "Daily news digest"
    assert data["trigger_type"] == "cron"
    # Payload should have substituted the feed URL
    import json

    payload = json.loads(data["payload"])
    assert "https://example.com/rss" in json.dumps(payload)


def test_from_template_uses_default_input(app_client: TestClient) -> None:
    """If an input is not provided, its default should be used."""
    resp = app_client.post(
        "/v1/routines/from-template",
        json={"slug": "daily-news-digest", "inputs": {}},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    import json

    payload = json.loads(resp.json()["payload"])
    # Default feed URL should appear in the payload
    assert "news.ycombinator.com" in json.dumps(payload)


def test_from_template_all_four_slugs(app_client: TestClient) -> None:
    """All 4 templates can be materialized with their defaults."""
    slugs = [
        "daily-news-digest",
        "refactor-todo-sweeper",
        "inbox-triage",
        "standup-notes-summarizer",
    ]
    for slug in slugs:
        resp = app_client.post(
            "/v1/routines/from-template",
            json={"slug": slug, "inputs": {}},
            headers=_AUTH,
        )
        assert resp.status_code == 200, f"slug {slug!r} failed: {resp.text}"
        assert resp.json()["id"].startswith("rtn_")


# ---------------------------------------------------------------------------
# POST /v1/routines/from-template — error cases
# ---------------------------------------------------------------------------


def test_from_template_unknown_slug_404(app_client: TestClient) -> None:
    resp = app_client.post(
        "/v1/routines/from-template",
        json={"slug": "nonexistent-slug", "inputs": {}},
        headers=_AUTH,
    )
    assert resp.status_code == 404
    assert "nonexistent-slug" in resp.json()["detail"]


def test_from_template_missing_required_input_422(
    app_client: TestClient, tmp_path: Path
) -> None:
    """A template with a required input (no default) must reject missing values with 422."""
    import api.dashboard_routes as dr

    # Inject a synthetic template with a required input (no default)
    synthetic: dict = {
        "slug": "test-required",
        "name": "Test required",
        "description": "test",
        "trigger": {"type": "cron", "expression": "0 9 * * *"},
        "payload": {
            "model": "deepseek/deepseek-v4-flash",
            "messages": [{"role": "user", "content": "Hello {{required_var}}"}],
        },
        "inputs": [
            {
                "name": "required_var",
                "label": "Required var",
                "type": "string",
                "default": None,
            }
        ],
    }
    original_cache = dr._templates_cache
    dr._templates_cache = {"test-required": synthetic}
    try:
        resp = app_client.post(
            "/v1/routines/from-template",
            json={"slug": "test-required", "inputs": {}},
            headers=_AUTH,
        )
        assert resp.status_code == 422
        assert "required_var" in resp.json()["detail"]
    finally:
        dr._templates_cache = original_cache


# ---------------------------------------------------------------------------
# Unit: _substitute_vars
# ---------------------------------------------------------------------------


def test_substitute_vars_basic() -> None:
    from api.dashboard_routes import _substitute_vars

    result = _substitute_vars(
        "Hello {{name}}, you have {{count}} messages.", {"name": "Alice", "count": "3"}
    )
    assert result == "Hello Alice, you have 3 messages."


def test_substitute_vars_no_placeholders() -> None:
    from api.dashboard_routes import _substitute_vars

    text = "No placeholders here."
    assert _substitute_vars(text, {"x": "y"}) == text


def test_substitute_vars_multiple_occurrences() -> None:
    from api.dashboard_routes import _substitute_vars

    result = _substitute_vars("{{x}} and {{x}}", {"x": "hello"})
    assert result == "hello and hello"


# ---------------------------------------------------------------------------
# Unit: template cache
# ---------------------------------------------------------------------------


def test_template_cache_populated() -> None:
    """After calling _load_templates the cache should be non-empty."""
    import api.dashboard_routes as dr

    dr._templates_cache = None
    templates = dr._load_templates()
    assert len(templates) >= 4
    assert "daily-news-digest" in templates


def test_template_cache_reused() -> None:
    """Second call returns the same dict object (cache hit)."""
    import api.dashboard_routes as dr

    dr._templates_cache = None
    t1 = dr._load_templates()
    t2 = dr._load_templates()
    assert t1 is t2
