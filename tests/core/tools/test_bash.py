"""Unit tests for the Bash tool executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tools.executors.bash import execute
from core.tools.workspace import Workspace


@pytest.mark.asyncio
async def test_bash_captures_stdout(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"command": "echo hi"}, ws)
    assert not result.is_error
    assert "hi" in result.content
    assert result.metadata["exit_code"] == 0


@pytest.mark.asyncio
async def test_bash_nonzero_exit_is_error(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"command": "exit 3"}, ws)
    assert result.is_error
    assert result.metadata["exit_code"] == 3


@pytest.mark.asyncio
async def test_bash_captures_stderr(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"command": "echo oops 1>&2; exit 1"}, ws)
    assert result.is_error
    assert "oops" in result.content


@pytest.mark.asyncio
async def test_bash_runs_in_workspace_root(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"command": "pwd"}, ws)
    assert not result.is_error
    assert str(tmp_path.resolve()) in result.content


@pytest.mark.asyncio
async def test_bash_timeout_kills_process(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"command": "sleep 5", "timeout": 1}, ws)
    assert result.is_error
    assert result.metadata["timeout"] is True
    assert "timed out" in result.content


@pytest.mark.asyncio
async def test_bash_denylist_blocks_command(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"command": "rm -rf /"}, ws, denylist=("rm",))
    assert result.is_error
    assert "denylist" in result.content


@pytest.mark.asyncio
async def test_bash_empty_command_is_error(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await execute({"command": "   "}, ws)
    assert result.is_error


@pytest.mark.asyncio
async def test_bash_env_strips_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "topsecret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/sock")
    ws = Workspace.create(tmp_path)
    result = await execute(
        {"command": "echo aws=$AWS_SECRET_ACCESS_KEY ssh=$SSH_AUTH_SOCK"}, ws
    )
    assert not result.is_error
    assert "topsecret" not in result.content
    assert "/tmp/sock" not in result.content
