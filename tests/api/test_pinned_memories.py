"""Tests for cross-session pinned memories (feature 2.6).

Covers:
- Datastore helpers: pin, list, delete.
- API endpoints: POST/GET/DELETE /v1/projects/{id}/memories.
- System-prompt injection in _run_job when project_id is set.
- Cap enforcement (10 memories, 4 000 combined chars).
- User scoping: memory is stored with the caller's user_id.
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
    datastore.reset_for_tests(tmp_path / "memories.sqlite")
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


def _make_project(client: TestClient, name: str = "Test Project") -> str:
    resp = client.post("/v1/projects", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Datastore helpers
# ---------------------------------------------------------------------------


def test_pin_and_list_memories(fresh_db) -> None:
    proj = datastore.upsert_project(name="P1")
    mem = datastore.pin_memory(
        project_id=proj["id"], content="staging key is in 1Password"
    )
    assert mem["id"].startswith("mem_")
    assert mem["project_id"] == proj["id"]
    assert mem["content"] == "staging key is in 1Password"
    assert mem["user_id"] == "default"

    mems = datastore.list_memories(proj["id"])
    assert len(mems) == 1
    assert mems[0]["id"] == mem["id"]


def test_delete_memory(fresh_db) -> None:
    proj = datastore.upsert_project(name="P2")
    mem = datastore.pin_memory(project_id=proj["id"], content="remember this")
    datastore.delete_memory(mem["id"])
    assert datastore.list_memories(proj["id"]) == []


def test_memory_hard_cap(fresh_db) -> None:
    proj = datastore.upsert_project(name="P3")
    for i in range(10):
        datastore.pin_memory(project_id=proj["id"], content=f"memory {i}")
    with pytest.raises(ValueError, match="maximum of 10"):
        datastore.pin_memory(project_id=proj["id"], content="one too many")


def test_memory_combined_char_cap(fresh_db) -> None:
    proj = datastore.upsert_project(name="P4")
    # Add 3 memories totalling 3900 chars.
    datastore.pin_memory(project_id=proj["id"], content="a" * 1300)
    datastore.pin_memory(project_id=proj["id"], content="b" * 1300)
    datastore.pin_memory(project_id=proj["id"], content="c" * 1300)
    # The next one would push total beyond 4000.
    with pytest.raises(ValueError, match="4000 characters"):
        datastore.pin_memory(project_id=proj["id"], content="d" * 200)


def test_memory_content_too_long(fresh_db) -> None:
    proj = datastore.upsert_project(name="P5")
    with pytest.raises(ValueError, match="maximum length"):
        datastore.pin_memory(project_id=proj["id"], content="x" * 4001)


def test_memory_source_session(fresh_db) -> None:
    proj = datastore.upsert_project(name="P6")
    sess = datastore.upsert_session(title="s1", project_id=proj["id"])
    mem = datastore.pin_memory(
        project_id=proj["id"],
        content="linked",
        source_session_id=sess["id"],
    )
    assert mem["source_session_id"] == sess["id"]


def test_schema_version_is_3(fresh_db) -> None:
    import sqlite3

    db_path = datastore.db_path()
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("PRAGMA user_version").fetchone()
    conn.close()
    assert row[0] == 3


def test_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "idem.sqlite"
    datastore.reset_for_tests(db)
    datastore._INIT_DONE = False
    datastore.init_schema()
    assert datastore._INIT_DONE is True


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


def test_api_pin_list_delete(app_client: TestClient) -> None:
    pid = _make_project(app_client)

    # Pin a memory
    resp = app_client.post(
        f"/v1/projects/{pid}/memories",
        json={"content": "staging key is in 1Password"},
    )
    assert resp.status_code == 200, resp.text
    mem = resp.json()
    assert mem["content"] == "staging key is in 1Password"
    mid = mem["id"]

    # List
    resp = app_client.get(f"/v1/projects/{pid}/memories")
    assert resp.status_code == 200
    mems = resp.json()["memories"]
    assert any(m["id"] == mid for m in mems)

    # Delete
    resp = app_client.delete(f"/v1/projects/{pid}/memories/{mid}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Gone
    resp = app_client.get(f"/v1/projects/{pid}/memories")
    assert resp.status_code == 200
    assert resp.json()["memories"] == []


def test_api_pin_unknown_project(app_client: TestClient) -> None:
    resp = app_client.post(
        "/v1/projects/nonexistent/memories",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_api_delete_wrong_project(app_client: TestClient) -> None:
    pid1 = _make_project(app_client, "Proj 1")
    pid2 = _make_project(app_client, "Proj 2")
    resp = app_client.post(f"/v1/projects/{pid1}/memories", json={"content": "secret"})
    mid = resp.json()["id"]
    # Attempt to delete via pid2's URL.
    resp = app_client.delete(f"/v1/projects/{pid2}/memories/{mid}")
    assert resp.status_code == 404


def test_api_cap_returns_400(app_client: TestClient) -> None:
    pid = _make_project(app_client)
    for i in range(10):
        r = app_client.post(
            f"/v1/projects/{pid}/memories", json={"content": f"memory {i}"}
        )
        assert r.status_code == 200
    resp = app_client.post(
        f"/v1/projects/{pid}/memories", json={"content": "one too many"}
    )
    assert resp.status_code == 400


def test_api_user_scoped(app_client: TestClient) -> None:
    pid = _make_project(app_client)
    resp = app_client.post(
        f"/v1/projects/{pid}/memories",
        json={"content": "alice's memory"},
        headers={"x-companion-user": "alice"},
    )
    assert resp.status_code == 200
    mem = resp.json()
    assert mem["user_id"] == "alice"


# ---------------------------------------------------------------------------
# System-prompt injection in _run_job
# ---------------------------------------------------------------------------


def test_system_prompt_contains_pinned_memory(fresh_db) -> None:
    """When _run_job runs with a project_id, memories appear in the system prompt.

    We intercept the request passed to ``run_agent_streaming`` BEFORE the
    model router replaces it, by having the mock router echo back the
    (already memory-injected) request it receives.
    """
    import asyncio

    proj = datastore.upsert_project(name="Injection test")
    pid = proj["id"]
    datastore.pin_memory(project_id=pid, content="staging key is in 1Password")

    # Create a job row pointing at this project.
    job = datastore.create_agent_job(
        session_id=None,
        project_id=pid,
        model="test/model",
        request_payload={
            "model": "test/model",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
            "system": None,
        },
    )
    job_id = job["id"]

    captured_system: list[str | None] = []

    async def _fake_run(request, provider, workspace, **kwargs):
        captured_system.append(request.system)
        return
        yield  # make it a generator

    mock_provider = MagicMock()
    mock_provider.preflight_stream = MagicMock()

    with (
        patch("api.agent.jobs.run_agent_streaming", side_effect=_fake_run),
        patch("api.agent.jobs.resolve_workspace", return_value=MagicMock()),
        patch("api.agent.jobs.parse_bash_denylist", return_value=[]),
        patch("api.agent.jobs.configure_global_tool_limiter", return_value=MagicMock()),
        patch("api.model_router.ModelRouter") as mock_router_cls,
        patch("api.agent.jobs._maybe_notify", new_callable=AsyncMock),
    ):
        # Have the router echo back the request it receives (already
        # memory-injected by _run_job before the routing step).
        def _echo_route(request):
            result = MagicMock()
            result.resolved.provider_id = "test"
            result.resolved.provider_model = "model"
            result.request = request  # preserve the injected system
            return result

        mock_router = MagicMock()
        mock_router_cls.return_value = mock_router
        mock_router.resolve_messages_request.side_effect = _echo_route

        from config.settings import get_settings

        settings = get_settings()

        asyncio.run(
            __import__("api.agent.jobs", fromlist=["_run_job"])._run_job(
                job_id,
                settings=settings,
                provider_getter=lambda: mock_provider,
            )
        )

    assert captured_system, "run_agent_streaming was never called"
    sys_text = captured_system[0] or ""
    assert "Memory you must respect for this project:" in sys_text
    assert "staging key is in 1Password" in sys_text


def test_system_prompt_no_memory_no_header(fresh_db) -> None:
    """When there are no pinned memories, the header is NOT injected."""
    import asyncio

    proj = datastore.upsert_project(name="Empty memories")
    pid = proj["id"]

    job = datastore.create_agent_job(
        session_id=None,
        project_id=pid,
        model="test/model",
        request_payload={
            "model": "test/model",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
            "system": None,
        },
    )
    job_id = job["id"]

    captured_system: list[str | None] = []

    async def _fake_run(request, provider, workspace, **kwargs):
        captured_system.append(request.system)
        return
        yield

    mock_provider = MagicMock()
    mock_provider.preflight_stream = MagicMock()

    with (
        patch("api.agent.jobs.run_agent_streaming", side_effect=_fake_run),
        patch("api.agent.jobs.resolve_workspace", return_value=MagicMock()),
        patch("api.agent.jobs.parse_bash_denylist", return_value=[]),
        patch("api.agent.jobs.configure_global_tool_limiter", return_value=MagicMock()),
        patch("api.model_router.ModelRouter") as mock_router_cls,
        patch("api.agent.jobs._maybe_notify", new_callable=AsyncMock),
    ):
        mock_router = MagicMock()
        mock_router_cls.return_value = mock_router
        routed = MagicMock()
        routed.resolved.provider_id = "test"
        routed.resolved.provider_model = "model"
        from api.models.anthropic import MessagesRequest

        routed.request = MessagesRequest(
            model="test/model",
            max_tokens=16,
            messages=[],
            system=None,
        )
        mock_router.resolve_messages_request.return_value = routed

        from config.settings import get_settings

        settings = get_settings()

        asyncio.run(
            __import__("api.agent.jobs", fromlist=["_run_job"])._run_job(
                job_id,
                settings=settings,
                provider_getter=lambda: mock_provider,
            )
        )

    assert captured_system, "run_agent_streaming was never called"
    sys_text = captured_system[0] or ""
    assert "Memory you must respect" not in sys_text
