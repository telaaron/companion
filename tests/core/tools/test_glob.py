"""Unit tests for the Glob tool executor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.tools.executors.glob import execute
from core.tools.workspace import Workspace


@pytest.mark.asyncio
async def test_glob_finds_files_by_extension(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("y")
    (tmp_path / "c.txt").write_text("z")
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "*.py"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    names = {Path(p).name for p in payload["matches"]}
    assert names == {"a.py", "b.py"}


@pytest.mark.asyncio
async def test_glob_recursive(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("x")
    (tmp_path / "src" / "y.py").write_text("y")
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "**/*.py"}, ws)
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    assert payload["count"] == 2


@pytest.mark.asyncio
async def test_glob_skips_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.py").write_text("x")
    (tmp_path / "real.py").write_text("y")
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "**/*.py"}, ws)
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    names = {Path(p).name for p in payload["matches"]}
    assert names == {"real.py"}


@pytest.mark.asyncio
async def test_glob_no_matches(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "*.nope"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    assert payload["count"] == 0


@pytest.mark.asyncio
async def test_glob_outside_workspace_rejected(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "*", "path": "/etc"}, ws)
    assert result.is_error
    assert "outside workspace" in result.content


@pytest.mark.asyncio
async def test_glob_requires_pattern(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({}, ws)
    assert result.is_error
