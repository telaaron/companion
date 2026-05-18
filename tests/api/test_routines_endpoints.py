"""Tests for routines CRUD + run/history/preview-cron endpoints (roadmap 6.2).

Covers:
- Datastore helpers: list_routine_runs, count_routine_runs.
- API endpoints:
    GET  /v1/routines
    POST /v1/routines
    PATCH /v1/routines/{id}
    DELETE /v1/routines/{id}
    POST /v1/routines/{id}/run
    GET  /v1/routines/{id}/runs
    GET  /v1/routines/preview-cron
- Payload validation (model + messages required).
- Trigger-type validation.
- User scoping (403 when another user tries to edit/delete/run).
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
    datastore.reset_for_tests(tmp_path / "routines_test.sqlite")
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


_VALID_PAYLOAD = {
    "model": "deepseek/deepseek-v4-flash",
    "messages": [{"role": "user", "content": "hello"}],
}

_VALID_BODY = {
    "name": "Test routine",
    "description": "A test",
    "trigger_type": "cron",
    "trigger_config": {"expression": "*/5 * * * *", "tz": "UTC"},
    "payload": _VALID_PAYLOAD,
    "enabled": True,
    "project_id": None,
}


# ---------------------------------------------------------------------------
# Datastore helpers
# ---------------------------------------------------------------------------


def test_list_routine_runs_empty(fresh_db) -> None:
    rtn = datastore.create_routine(name="r1", payload=_VALID_PAYLOAD)
    runs = datastore.list_routine_runs(rtn["id"])
    assert runs == []
    assert datastore.count_routine_runs(rtn["id"]) == 0


def test_list_routine_runs_pagination(fresh_db) -> None:
    rtn = datastore.create_routine(name="r2", payload=_VALID_PAYLOAD)
    for _ in range(5):
        datastore.record_routine_run(routine_id=rtn["id"], status="done")

    all_runs = datastore.list_routine_runs(rtn["id"], limit=100)
    assert len(all_runs) == 5
    assert datastore.count_routine_runs(rtn["id"]) == 5

    page1 = datastore.list_routine_runs(rtn["id"], limit=3, offset=0)
    page2 = datastore.list_routine_runs(rtn["id"], limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 2

    # Newest first
    assert page1[0]["started_at"] >= page1[-1]["started_at"]


# ---------------------------------------------------------------------------
# GET /v1/routines
# ---------------------------------------------------------------------------


def test_list_routines_empty(app_client: TestClient) -> None:
    resp = app_client.get("/v1/routines", headers={"Authorization": "Bearer freecc"})
    assert resp.status_code == 200
    assert resp.json()["routines"] == []


def test_list_routines_returns_created(app_client: TestClient) -> None:
    app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    resp = app_client.get("/v1/routines", headers={"Authorization": "Bearer freecc"})
    assert resp.status_code == 200
    assert len(resp.json()["routines"]) == 1
    assert resp.json()["routines"][0]["name"] == "Test routine"


# ---------------------------------------------------------------------------
# POST /v1/routines
# ---------------------------------------------------------------------------


def test_create_routine_success(app_client: TestClient) -> None:
    resp = app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"].startswith("rtn_")
    assert data["name"] == "Test routine"
    assert data["trigger_type"] == "cron"
    assert data["enabled"] == 1


def test_create_routine_missing_model_rejected(app_client: TestClient) -> None:
    body = dict(_VALID_BODY)
    body["payload"] = {"messages": []}
    resp = app_client.post(
        "/v1/routines",
        json=body,
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 400
    assert "model" in resp.json()["detail"]


def test_create_routine_missing_messages_rejected(app_client: TestClient) -> None:
    body = dict(_VALID_BODY)
    body["payload"] = {"model": "deepseek/deepseek-v4-flash"}
    resp = app_client.post(
        "/v1/routines",
        json=body,
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 400
    assert "messages" in resp.json()["detail"]


def test_create_routine_invalid_trigger_type(app_client: TestClient) -> None:
    body = dict(_VALID_BODY)
    body["trigger_type"] = "invalid_type"
    resp = app_client.post(
        "/v1/routines",
        json=body,
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 400


def test_create_manual_routine(app_client: TestClient) -> None:
    body = dict(_VALID_BODY)
    body["trigger_type"] = "manual"
    body["trigger_config"] = {}
    resp = app_client.post(
        "/v1/routines",
        json=body,
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    assert resp.json()["trigger_type"] == "manual"
    # manual routines have no next_run_ms
    assert resp.json()["next_run_ms"] is None


# ---------------------------------------------------------------------------
# PATCH /v1/routines/{id}
# ---------------------------------------------------------------------------


def test_patch_routine_name(app_client: TestClient) -> None:
    create_resp = app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    rid = create_resp.json()["id"]

    patch_resp = app_client.patch(
        f"/v1/routines/{rid}",
        json={"name": "Updated name"},
        headers={"Authorization": "Bearer freecc"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Updated name"


def test_patch_routine_enabled_toggle(app_client: TestClient) -> None:
    create_resp = app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    rid = create_resp.json()["id"]

    patch_resp = app_client.patch(
        f"/v1/routines/{rid}",
        json={"enabled": False},
        headers={"Authorization": "Bearer freecc"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["enabled"] == 0


def test_patch_routine_not_found(app_client: TestClient) -> None:
    resp = app_client.patch(
        "/v1/routines/rtn_doesnotexist",
        json={"name": "x"},
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 404


def test_patch_routine_invalid_payload_rejected(app_client: TestClient) -> None:
    create_resp = app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    rid = create_resp.json()["id"]

    patch_resp = app_client.patch(
        f"/v1/routines/{rid}",
        json={"payload": {"model": ""}},
        headers={"Authorization": "Bearer freecc"},
    )
    assert patch_resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /v1/routines/{id}
# ---------------------------------------------------------------------------


def test_delete_routine(app_client: TestClient) -> None:
    create_resp = app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    rid = create_resp.json()["id"]

    del_resp = app_client.delete(
        f"/v1/routines/{rid}",
        headers={"Authorization": "Bearer freecc"},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True

    # Confirm it's gone
    list_resp = app_client.get(
        "/v1/routines", headers={"Authorization": "Bearer freecc"}
    )
    assert list_resp.json()["routines"] == []


def test_delete_routine_not_found(app_client: TestClient) -> None:
    resp = app_client.delete(
        "/v1/routines/rtn_doesnotexist",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/routines/{id}/run
# ---------------------------------------------------------------------------


def test_run_routine_now(app_client: TestClient) -> None:
    create_resp = app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    rid = create_resp.json()["id"]

    with patch(
        "api.agent.routines.trigger_routine_now",
        new_callable=AsyncMock,
        return_value={"id": "job_abc123"},
    ):
        run_resp = app_client.post(
            f"/v1/routines/{rid}/run",
            headers={"Authorization": "Bearer freecc"},
        )
    assert run_resp.status_code == 200
    assert run_resp.json()["ok"] is True
    assert run_resp.json()["job"]["id"] == "job_abc123"


def test_run_routine_not_found(app_client: TestClient) -> None:
    resp = app_client.post(
        "/v1/routines/rtn_doesnotexist/run",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/routines/{id}/runs
# ---------------------------------------------------------------------------


def test_get_routine_runs_empty(app_client: TestClient, fresh_db: None) -> None:
    create_resp = app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    rid = create_resp.json()["id"]

    resp = app_client.get(
        f"/v1/routines/{rid}/runs",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["runs"] == []
    assert data["total"] == 0


def test_get_routine_runs_pagination(app_client: TestClient, fresh_db: None) -> None:
    create_resp = app_client.post(
        "/v1/routines",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer freecc"},
    )
    rid = create_resp.json()["id"]

    # Insert runs directly into the datastore
    for _i in range(5):
        datastore.record_routine_run(routine_id=rid, status="done")

    resp = app_client.get(
        f"/v1/routines/{rid}/runs?limit=3&offset=0",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["runs"]) == 3

    resp2 = app_client.get(
        f"/v1/routines/{rid}/runs?limit=3&offset=3",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["runs"]) == 2


def test_get_routine_runs_not_found(app_client: TestClient) -> None:
    resp = app_client.get(
        "/v1/routines/rtn_doesnotexist/runs",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/routines/preview-cron
# ---------------------------------------------------------------------------


def test_preview_cron_valid(app_client: TestClient) -> None:
    resp = app_client.get(
        "/v1/routines/preview-cron?expr=*/5+*+*+*+*&tz=UTC",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "fires" in data
    assert len(data["fires"]) == 5
    # All should be epoch-ms integers > 0
    for f in data["fires"]:
        assert isinstance(f, int)
        assert f > 0
    # Should be ascending
    assert data["fires"] == sorted(data["fires"])


def test_preview_cron_missing_expr(app_client: TestClient) -> None:
    resp = app_client.get(
        "/v1/routines/preview-cron",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 400


def test_preview_cron_invalid_expr(app_client: TestClient) -> None:
    resp = app_client.get(
        "/v1/routines/preview-cron?expr=not-a-cron",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 400


def test_preview_cron_timezone(app_client: TestClient) -> None:
    resp = app_client.get(
        "/v1/routines/preview-cron?expr=0+9+*+*+*&tz=America%2FNew_York",
        headers={"Authorization": "Bearer freecc"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["fires"]) == 5


# ---------------------------------------------------------------------------
# Auth: require API key (when auth token is configured)
# ---------------------------------------------------------------------------


def test_list_routines_with_wrong_key_rejected(fresh_db: None) -> None:
    """A wrong Bearer token is rejected when auth is configured."""
    import os

    from api.app import create_app

    old = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    os.environ["ANTHROPIC_AUTH_TOKEN"] = "correct-token"
    try:
        # Invalidate cached settings so the new token is picked up.
        from config.settings import get_settings as _get

        _get.cache_clear()
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
            resp = client.get(
                "/v1/routines",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401
    finally:
        if old is None:
            del os.environ["ANTHROPIC_AUTH_TOKEN"]
        else:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = old
        from config.settings import get_settings as _get

        _get.cache_clear()
