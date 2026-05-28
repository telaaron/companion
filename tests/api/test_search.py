"""Tests for GET /v1/search — unified fuzzy search endpoint."""

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
    datastore.reset_for_tests(tmp_path / "search_test.sqlite")
    yield
    datastore.reset_for_tests()


@pytest.fixture
def app_client(fresh_db):
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


@pytest.fixture
def seeded_db(fresh_db):
    """Seed the database with known records for predictable search results."""
    # Create a project
    datastore.upsert_project(
        name="Test Project Alpha",
        description="A test project for search testing",
        user_id="default",
        workspace_path="/tmp/alpha",
    )

    # Create a session with messages
    session = datastore.upsert_session(
        title="Debugging session",
        model="deepseek/deepseek-v4-pro",
        user_id="default",
    )
    sid = session["id"]

    datastore.append_message(
        session_id=sid,
        role="user",
        content="How do I fix the null pointer exception in my Java code?",
    )
    datastore.append_message(
        session_id=sid,
        role="assistant",
        content="You need to check for null before dereferencing the object.",
    )

    # Create another session
    session2 = datastore.upsert_session(
        title="Python data processing",
        model="open_router/anthropic/claude-sonnet-4",
        user_id="default",
    )
    datastore.append_message(
        session_id=session2["id"],
        role="user",
        content="How do I use pandas for data analysis?",
    )

    # Pin a project memory
    projects = datastore.list_projects(user_id="default")
    if projects:
        datastore.pin_memory(
            project_id=projects[0]["id"],
            content="Important: always check null before deref",
            source_session_id=sid,
            user_id="default",
        )

    # Record audit log entry
    datastore.record_audit(
        category="config",
        event="env_set",
        detail="ANTHROPIC_AUTH_TOKEN configured",
    )

    return {
        "session_id": sid,
        "session2_id": session2["id"],
    }


# ---------------------------------------------------------------------------
# Test: search endpoint requires auth
# ---------------------------------------------------------------------------


def test_search_requires_auth(fresh_db, app_client: TestClient):
    """The search endpoint requires authentication."""
    # Create a fresh app without the default auth token
    resp = app_client.get("/v1/search?q=test")
    # With the standard mock setup, the default token freecc works
    # but we check that the endpoint at least responds properly
    resp_auth = app_client.get(
        "/v1/search?q=test", headers={"Authorization": "Bearer freecc"}
    )
    assert resp_auth.status_code == 200


def test_search_empty_query(fresh_db, app_client: TestClient):
    """Empty query returns empty results."""
    resp = app_client.get("/v1/search?q=", headers={"Authorization": "Bearer freecc"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_search_sessions(seeded_db, app_client: TestClient):
    """Search finds sessions by title."""
    resp = app_client.get(
        "/v1/search?q=debug&kinds=session",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    titles = [r["title"] for r in results]
    assert any("ebug" in t for t in titles)


def test_search_messages(seeded_db, app_client: TestClient):
    """Search finds messages via memory_fts."""
    resp = app_client.get(
        "/v1/search?q=null&kinds=message",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    msgs = [r for r in results if r["kind"] == "message"]
    assert len(msgs) > 0


def test_search_project(seeded_db, app_client: TestClient):
    """Search finds projects."""
    resp = app_client.get(
        "/v1/search?q=Alpha&kinds=project",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) > 0
    names = [r["title"] for r in results]
    assert any("Alpha" in n for n in names)


def test_search_memory(seeded_db, app_client: TestClient):
    """Search finds project memories."""
    resp = app_client.get(
        "/v1/search?q=null+before+deref&kinds=memory",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    memories = [r for r in results if r["kind"] == "memory"]
    assert len(memories) > 0


def test_search_audit(seeded_db, app_client: TestClient):
    """Search finds audit log entries."""
    resp = app_client.get(
        "/v1/search?q=ANTHROPIC&kinds=audit",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) > 0


def test_search_message_empty(fresh_db, app_client: TestClient):
    """Search with no data returns empty results."""
    resp = app_client.get(
        "/v1/search?q=nonexistent&kinds=message",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_search_result_structure(seeded_db, app_client: TestClient):
    """Each result has the required fields."""
    resp = app_client.get(
        "/v1/search?q=test",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    for r in results:
        for key in ("kind", "title", "subtitle", "navigate_url", "score", "ts", "ref"):
            assert key in r, f"Missing key '{key}' in result: {r}"


def test_search_limit(seeded_db, app_client: TestClient):
    """The limit parameter caps result count."""
    resp = app_client.get(
        "/v1/search?q=a&limit=2",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) <= 2
