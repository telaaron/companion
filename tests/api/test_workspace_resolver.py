"""Tests for workspace resolution priority chain (BUG-002 regression guard).

Priority order (highest first):
1. ``metadata['workspace_path']``  (explicit per-request override)
2. ``metadata['project_id']``       (look up project → workspace_path)
3. ``AGENT_DEFAULT_WORKSPACE_<USER>`` env (per-user fallback)
4. ``settings.agent_default_workspace`` (global default)
5. ``os.getcwd()`` (process working directory)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api import datastore
from api.agent.workspace_resolver import resolve_workspace
from api.models.anthropic import Message, MessagesRequest
from config.settings import Settings


@pytest.fixture
def fresh_db(tmp_path: Path):
    """Point the datastore at a throwaway SQLite file for this test."""
    datastore.reset_for_tests(tmp_path / "ws.sqlite")
    yield
    datastore.reset_for_tests()


def _make_request(metadata: dict[str, Any] | None = None) -> MessagesRequest:
    return MessagesRequest(
        model="x",
        max_tokens=128,
        messages=[Message(role="user", content="hi")],
        metadata=metadata,
    )


def _settings_with_default_ws(workspace: Path | str) -> Settings:
    """Build a Settings instance with ``agent_default_workspace`` set.

    Pydantic-settings fields use ``validation_alias``; constructor kwargs go
    through ``__init__`` which ``ty`` cannot introspect. Use
    ``object.__setattr__`` to bypass and write the resolved field directly
    (same pattern as ``tests/api/test_voice.py::_make_settings``).
    """
    s = Settings()
    object.__setattr__(s, "agent_default_workspace", str(workspace))
    return s


def test_explicit_workspace_path_metadata_wins(tmp_path: Path) -> None:
    settings = _settings_with_default_ws(tmp_path / "fallback")
    request = _make_request({"workspace_path": str(tmp_path / "explicit")})
    (tmp_path / "explicit").mkdir()
    ws = resolve_workspace(request, settings)
    assert ws.root == tmp_path / "explicit"


def test_project_id_resolves_to_project_workspace_path(
    fresh_db, tmp_path: Path
) -> None:
    """BUG-002: when session is in a project, workspace must come from the
    project's ``workspace_path`` column.
    """
    project_root = tmp_path / "proj-root"
    project_root.mkdir()
    project = datastore.upsert_project(
        name="Test Project",
        workspace_path=str(project_root),
    )
    settings = _settings_with_default_ws(tmp_path / "fallback")
    request = _make_request({"project_id": project["id"]})
    ws = resolve_workspace(request, settings)
    assert ws.root == project_root


def test_explicit_metadata_path_beats_project(fresh_db, tmp_path: Path) -> None:
    project_root = tmp_path / "proj-root"
    explicit = tmp_path / "explicit"
    project_root.mkdir()
    explicit.mkdir()
    project = datastore.upsert_project(
        name="P",
        workspace_path=str(project_root),
    )
    settings = _settings_with_default_ws("")
    request = _make_request(
        {"project_id": project["id"], "workspace_path": str(explicit)}
    )
    ws = resolve_workspace(request, settings)
    assert ws.root == explicit


def test_no_project_falls_back_to_default(tmp_path: Path) -> None:
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    settings = _settings_with_default_ws(fallback)
    request = _make_request()
    ws = resolve_workspace(request, settings)
    assert ws.root == fallback


def test_missing_project_id_falls_back(fresh_db, tmp_path: Path) -> None:
    """If project_id points at a vanished project, fall back gracefully."""
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    settings = _settings_with_default_ws(fallback)
    request = _make_request({"project_id": "prj_nonexistent"})
    ws = resolve_workspace(request, settings)
    assert ws.root == fallback


def test_project_with_empty_workspace_path_falls_back(fresh_db, tmp_path: Path) -> None:
    """Project exists but its workspace_path is empty — fall through to default."""
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    project = datastore.upsert_project(name="P", workspace_path="")
    settings = _settings_with_default_ws(fallback)
    request = _make_request({"project_id": project["id"]})
    ws = resolve_workspace(request, settings)
    assert ws.root == fallback
