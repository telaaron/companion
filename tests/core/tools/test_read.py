"""Unit tests for the Read tool executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tools.executors.read import execute
from core.tools.workspace import Workspace


@pytest.mark.asyncio
async def test_read_returns_numbered_lines(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "f.txt"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    assert "1\talpha" in result.content
    assert "3\tgamma" in result.content
    assert result.metadata["total_lines"] == 3
    assert result.metadata["truncated"] is False


@pytest.mark.asyncio
async def test_read_with_offset_and_limit(tmp_path: Path) -> None:
    target = tmp_path / "many.txt"
    target.write_text("\n".join(str(i) for i in range(10)))
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "many.txt", "offset": 5, "limit": 2}, ws)
    assert not result.is_error
    assert "6\t5" in result.content
    assert "7\t6" in result.content
    assert "8\t7" not in result.content
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_read_missing_file_is_error(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "nope.txt"}, ws)
    assert result.is_error
    assert "not found" in result.content


@pytest.mark.asyncio
async def test_read_directory_is_error(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "sub"}, ws)
    assert result.is_error
    assert "directory" in result.content


@pytest.mark.asyncio
async def test_read_outside_workspace_is_error(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"file_path": "/etc/passwd"}, ws)
    assert result.is_error
    assert "outside workspace" in result.content


@pytest.mark.asyncio
async def test_read_missing_file_path_is_error(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({}, ws)
    assert result.is_error
    assert "file_path" in result.content
