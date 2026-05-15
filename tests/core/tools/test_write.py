"""Unit tests for the Write tool executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tools.executors.write import execute
from core.tools.workspace import Workspace


@pytest.mark.asyncio
async def test_write_creates_new_file(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "new.txt", "content": "hello"}, ws)
    assert not result.is_error
    assert (tmp_path / "new.txt").read_text() == "hello"
    assert result.metadata["created"] is True
    assert result.metadata["bytes_written"] == 5


@pytest.mark.asyncio
async def test_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("old")
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "x.txt", "content": "new"}, ws)
    assert not result.is_error
    assert target.read_text() == "new"
    assert result.metadata["created"] is False


@pytest.mark.asyncio
async def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "deep/nested/dir/f.txt", "content": "z"}, ws)
    assert not result.is_error
    assert (tmp_path / "deep/nested/dir/f.txt").read_text() == "z"


@pytest.mark.asyncio
async def test_write_outside_workspace_rejected(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "/tmp/escape.txt", "content": "x"}, ws)
    assert result.is_error
    assert "outside workspace" in result.content


@pytest.mark.asyncio
async def test_write_to_directory_rejected(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "sub", "content": "x"}, ws)
    assert result.is_error


@pytest.mark.asyncio
async def test_write_requires_content_string(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "f.txt"}, ws)
    assert result.is_error
    assert "content" in result.content
