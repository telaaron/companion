"""Unit tests for the LS tool executor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.tools.executors.ls import execute
from core.tools.workspace import Workspace


@pytest.mark.asyncio
async def test_ls_lists_entries(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b").mkdir()
    ws = Workspace.create(tmp_path)
    result = await execute({"path": "."}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    names = {entry["name"]: entry for entry in payload["entries"]}
    assert names["a.txt"]["type"] == "file"
    assert names["a.txt"]["size"] == 1
    assert names["b"]["type"] == "dir"
    assert names["b"]["size"] is None


@pytest.mark.asyncio
async def test_ls_dirs_before_files(tmp_path: Path) -> None:
    (tmp_path / "z.txt").write_text("z")
    (tmp_path / "a_dir").mkdir()
    ws = Workspace.create(tmp_path)
    result = await execute({"path": "."}, ws)
    assert isinstance(result.content, str)
    payload = json.loads(result.content)
    assert payload["entries"][0]["name"] == "a_dir"
    assert payload["entries"][1]["name"] == "z.txt"


@pytest.mark.asyncio
async def test_ls_nonexistent_is_error(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"path": "missing"}, ws)
    assert result.is_error


@pytest.mark.asyncio
async def test_ls_file_is_error(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    ws = Workspace.create(tmp_path)
    result = await execute({"path": "f.txt"}, ws)
    assert result.is_error
    assert "not a directory" in result.content


@pytest.mark.asyncio
async def test_ls_outside_workspace_rejected(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"path": "/etc"}, ws)
    assert result.is_error
    assert "outside workspace" in result.content
