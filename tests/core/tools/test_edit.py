"""Unit tests for the Edit tool executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tools.executors.edit import execute
from core.tools.workspace import Workspace


@pytest.mark.asyncio
async def test_edit_replaces_single_match(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello world")
    ws = Workspace.create(tmp_path)
    result = await execute(
        {"file_path": "f.txt", "old_string": "world", "new_string": "there"}, ws
    )
    assert not result.is_error
    assert target.read_text() == "hello there"
    assert result.metadata["replacements"] == 1


@pytest.mark.asyncio
async def test_edit_multiple_matches_without_replace_all_errors(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("a a a")
    ws = Workspace.create(tmp_path)
    result = await execute(
        {"file_path": "f.txt", "old_string": "a", "new_string": "b"}, ws
    )
    assert result.is_error
    assert "matched 3 times" in result.content
    assert target.read_text() == "a a a"


@pytest.mark.asyncio
async def test_edit_replace_all(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("a a a")
    ws = Workspace.create(tmp_path)
    result = await execute(
        {
            "file_path": "f.txt",
            "old_string": "a",
            "new_string": "b",
            "replace_all": True,
        },
        ws,
    )
    assert not result.is_error
    assert target.read_text() == "b b b"
    assert result.metadata["replacements"] == 3


@pytest.mark.asyncio
async def test_edit_no_match_errors(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello")
    ws = Workspace.create(tmp_path)
    result = await execute(
        {"file_path": "f.txt", "old_string": "missing", "new_string": "x"}, ws
    )
    assert result.is_error
    assert "not found" in result.content


@pytest.mark.asyncio
async def test_edit_identical_strings_errors(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello")
    ws = Workspace.create(tmp_path)
    result = await execute(
        {"file_path": "f.txt", "old_string": "hello", "new_string": "hello"}, ws
    )
    assert result.is_error
    assert "differ" in result.content


@pytest.mark.asyncio
async def test_edit_outside_workspace_rejected(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute(
        {"file_path": "/etc/hosts", "old_string": "x", "new_string": "y"}, ws
    )
    assert result.is_error
    assert "outside workspace" in result.content


@pytest.mark.asyncio
async def test_edit_missing_file_errors(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute(
        {"file_path": "nope.txt", "old_string": "x", "new_string": "y"}, ws
    )
    assert result.is_error
    assert "not found" in result.content
