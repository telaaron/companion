"""Tests for the AI-managed Project + EnvSet tools."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from api import datastore
from api.agent.extras import env_set as env_set_executor
from api.agent.extras import projects as projects_executor
from core.tools.workspace import Workspace


@pytest.fixture
def fresh_db(tmp_path: Path):
    datastore.reset_for_tests(tmp_path / "projects.sqlite")
    yield
    datastore.reset_for_tests()


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace.create(tmp_path)


# ---------------------- ProjectList / Create / Update / Delete ----------------------


@pytest.mark.asyncio
async def test_project_create_then_list(fresh_db, ws: Workspace) -> None:
    result = await projects_executor.execute_create(
        {
            "name": "smoke",
            "shared_context": "test brief",
            "workspace_path": "/tmp",
        },
        ws,
    )
    assert not result.is_error
    assert isinstance(result.content, str)
    body = json.loads(result.content)
    assert body["ok"] is True
    pid = body["id"]

    list_result = await projects_executor.execute_list({}, ws)
    assert isinstance(list_result.content, str)
    listed = json.loads(list_result.content)
    names = [p["name"] for p in listed["projects"]]
    assert "smoke" in names
    matched = next(p for p in listed["projects"] if p["id"] == pid)
    assert matched["workspace_path"] == "/tmp"


@pytest.mark.asyncio
async def test_project_update_partial(fresh_db, ws: Workspace) -> None:
    created = await projects_executor.execute_create({"name": "init"}, ws)
    assert isinstance(created.content, str)
    pid = json.loads(created.content)["id"]
    updated = await projects_executor.execute_update(
        {"project_id": pid, "shared_context": "new brief"}, ws
    )
    assert not updated.is_error
    project = datastore.get_project(pid)
    assert project is not None
    assert project["shared_context"] == "new brief"
    assert project["name"] == "init"  # unchanged


@pytest.mark.asyncio
async def test_project_delete(fresh_db, ws: Workspace) -> None:
    created = await projects_executor.execute_create({"name": "doomed"}, ws)
    assert isinstance(created.content, str)
    pid = json.loads(created.content)["id"]
    result = await projects_executor.execute_delete({"project_id": pid}, ws)
    assert not result.is_error
    assert datastore.get_project(pid) is None


@pytest.mark.asyncio
async def test_project_update_missing_returns_error(fresh_db, ws: Workspace) -> None:
    result = await projects_executor.execute_update(
        {"project_id": "prj_nonexistent", "name": "x"}, ws
    )
    assert result.is_error


@pytest.mark.asyncio
async def test_project_create_requires_name(fresh_db, ws: Workspace) -> None:
    result = await projects_executor.execute_create({}, ws)
    assert result.is_error
    assert "name" in result.content


# ---------------------- EnvSet ----------------------


@pytest.mark.asyncio
async def test_env_set_writes_and_invalidates_cache(
    ws: Workspace, monkeypatch, tmp_path: Path
) -> None:
    fake_env = tmp_path / "fake.env"
    monkeypatch.setattr(env_set_executor, "_ENV_FILE", fake_env)
    result = await env_set_executor.execute(
        {"key": "AGENT_MAX_TURNS", "value": "42"}, ws
    )
    assert not result.is_error
    assert "AGENT_MAX_TURNS" in result.content
    body = fake_env.read_text(encoding="utf-8")
    assert "AGENT_MAX_TURNS=42" in body
    assert os.environ.get("AGENT_MAX_TURNS") == "42"


@pytest.mark.asyncio
async def test_env_set_rejects_invalid_key(ws: Workspace) -> None:
    result = await env_set_executor.execute({"key": "lowercase", "value": "x"}, ws)
    assert result.is_error


@pytest.mark.asyncio
async def test_env_set_masks_secret_in_response(
    ws: Workspace, monkeypatch, tmp_path: Path
) -> None:
    fake_env = tmp_path / "fake.env"
    monkeypatch.setattr(env_set_executor, "_ENV_FILE", fake_env)
    result = await env_set_executor.execute(
        {"key": "GITHUB_TOKEN", "value": "ghp_super_secret"}, ws
    )
    assert not result.is_error
    assert "ghp_super_secret" not in result.content
    assert "rotated" in result.content
    assert result.metadata["secret"] is True
