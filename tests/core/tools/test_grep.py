"""Unit tests for the Grep tool executor (works with rg or Python fallback)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tools.executors.grep import execute
from core.tools.workspace import Workspace


@pytest.mark.asyncio
async def test_grep_files_with_matches(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\nfoo\n")
    (tmp_path / "b.txt").write_text("world\n")
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "foo"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    assert "a.txt" in result.content
    assert "b.txt" not in result.content


@pytest.mark.asyncio
async def test_grep_content_mode(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("alpha\nbravo\nfoo bar\n")
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "bar", "output_mode": "content"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    assert "foo bar" in result.content
    assert ":3:" in result.content


@pytest.mark.asyncio
async def test_grep_count_mode(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x\nx\nx\n")
    (tmp_path / "b.txt").write_text("x\n")
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "x", "output_mode": "count"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    assert ":3" in result.content
    assert ":1" in result.content


@pytest.mark.asyncio
async def test_grep_no_matches(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n")
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "nope"}, ws)
    assert not result.is_error
    assert "No matches" in result.content


@pytest.mark.asyncio
async def test_grep_invalid_mode(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "x", "output_mode": "bogus"}, ws)
    assert result.is_error


@pytest.mark.asyncio
async def test_grep_outside_workspace_rejected(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"pattern": "x", "path": "/etc"}, ws)
    assert result.is_error
