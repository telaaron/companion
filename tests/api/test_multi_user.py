"""Multi-user isolation tests.

Covers:
- Alice and Bob see only their own sessions/projects via x-companion-user header.
- Bearer-token-to-user mapping via Settings.companion_users.
- Audit log is scoped per user.
- Migration is a no-op when re-run on an already-migrated DB.
- Default bucket preserves single-user data.
- /v1/me returns the resolved user_id.
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
    """Redirect the datastore to a throw-away SQLite file."""
    datastore.reset_for_tests(tmp_path / "multi_user.sqlite")
    yield
    datastore.reset_for_tests()


@pytest.fixture
def app_client(fresh_db):
    """FastAPI test client with no auth (ANTHROPIC_AUTH_TOKEN unset)."""
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


# ---------------------------------------------------------------------------
# /v1/me
# ---------------------------------------------------------------------------


def test_me_returns_default_when_no_header(app_client: TestClient) -> None:
    resp = app_client.get("/v1/me")
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "default"


def test_me_returns_user_from_x_companion_user_header(app_client: TestClient) -> None:
    resp = app_client.get("/v1/me", headers={"x-companion-user": "alice"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "alice"


def test_me_bearer_token_mapping(app_client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("COMPANION_USERS", "tok_alice=alice,tok_bob=bob")
    # Force settings reload for this test
    from config.settings import get_settings

    get_settings.cache_clear()
    try:
        resp = app_client.get("/v1/me", headers={"Authorization": "Bearer tok_alice"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "alice"
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("COMPANION_USERS", raising=False)


# ---------------------------------------------------------------------------
# Session isolation (x-companion-user header)
# ---------------------------------------------------------------------------


def test_alice_and_bob_cannot_see_each_others_sessions(
    app_client: TestClient,
) -> None:
    # Alice creates a session
    resp = app_client.post(
        "/v1/sessions",
        json={"title": "Alice session"},
        headers={"x-companion-user": "alice"},
    )
    assert resp.status_code == 200
    alice_sid = resp.json()["id"]

    # Bob lists sessions — should be empty
    resp = app_client.get("/v1/sessions", headers={"x-companion-user": "bob"})
    assert resp.status_code == 200
    bob_sessions = resp.json()["sessions"]
    assert all(s["id"] != alice_sid for s in bob_sessions)
    assert len(bob_sessions) == 0

    # Alice lists sessions — sees her own
    resp = app_client.get("/v1/sessions", headers={"x-companion-user": "alice"})
    assert resp.status_code == 200
    alice_sessions = resp.json()["sessions"]
    assert any(s["id"] == alice_sid for s in alice_sessions)


# ---------------------------------------------------------------------------
# Project isolation
# ---------------------------------------------------------------------------


def test_alice_and_bob_cannot_see_each_others_projects(
    app_client: TestClient,
) -> None:
    resp = app_client.post(
        "/v1/projects",
        json={"name": "Alice project"},
        headers={"x-companion-user": "alice"},
    )
    assert resp.status_code == 200
    alice_pid = resp.json()["id"]

    resp = app_client.get("/v1/projects", headers={"x-companion-user": "bob"})
    assert resp.status_code == 200
    assert all(p["id"] != alice_pid for p in resp.json()["projects"])

    resp = app_client.get("/v1/projects", headers={"x-companion-user": "alice"})
    assert resp.status_code == 200
    assert any(p["id"] == alice_pid for p in resp.json()["projects"])


# ---------------------------------------------------------------------------
# Audit log isolation (datastore level)
# ---------------------------------------------------------------------------


def test_audit_log_scoped_per_user(fresh_db) -> None:
    datastore.record_audit(
        category="env",
        event="upsert",
        detail="SOME_KEY",
        user_id="alice",
    )
    datastore.record_audit(
        category="env",
        event="upsert",
        detail="OTHER_KEY",
        user_id="bob",
    )

    alice_events = datastore.list_audit(user_id="alice")
    bob_events = datastore.list_audit(user_id="bob")

    assert all(e["user_id"] == "alice" for e in alice_events)
    assert all(e["user_id"] == "bob" for e in bob_events)
    assert len(alice_events) == 1
    assert len(bob_events) == 1


# ---------------------------------------------------------------------------
# Default bucket — single-user data preserved
# ---------------------------------------------------------------------------


def test_default_bucket_is_backward_compatible(fresh_db) -> None:
    """Data written without a user_id ends up in the 'default' bucket."""
    datastore.upsert_session(title="legacy session")
    sessions = datastore.list_sessions(user_id="default")
    assert any(s["title"] == "legacy session" for s in sessions)


def test_default_bucket_not_visible_to_named_user(fresh_db) -> None:
    datastore.upsert_session(title="shared session", user_id="default")
    alice_sessions = datastore.list_sessions(user_id="alice")
    assert len(alice_sessions) == 0


# ---------------------------------------------------------------------------
# Migration idempotency (datastore level)
# ---------------------------------------------------------------------------


def test_schema_migration_is_idempotent(tmp_path: Path) -> None:
    """Running init_schema twice on the same DB must not raise."""
    db = tmp_path / "idem.sqlite"
    datastore.reset_for_tests(db)
    datastore.init_schema()  # first run (already called by reset_for_tests)

    # Force a second run by clearing the flag
    datastore._INIT_DONE = False
    datastore.init_schema()  # second run — must be a no-op
    assert datastore._INIT_DONE is True


def test_user_version_is_set_after_migration(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "version.sqlite"
    datastore.reset_for_tests(db)

    conn = sqlite3.connect(str(db))
    row = conn.execute("PRAGMA user_version").fetchone()
    conn.close()
    assert row[0] == datastore._SCHEMA_VERSION
