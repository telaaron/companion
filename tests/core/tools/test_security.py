"""Adversarial / penetration-style tests for the local tool surface."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from core.tools.executors.bash import (
    MAX_COMMAND_BYTES,
    SENSITIVE_ENV_NAME_FRAGMENTS,
    _sanitize_env,
)
from core.tools.executors.bash import execute as bash_execute
from core.tools.executors.read import execute as read_execute
from core.tools.executors.write import execute as write_execute
from core.tools.workspace import Workspace, WorkspaceViolation

# ---------------------- Workspace path traversal ----------------------


def test_workspace_blocks_dot_dot_traversal(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ws = Workspace.create(sandbox)
    with pytest.raises(WorkspaceViolation):
        ws.resolve("../../../../etc/passwd")


def test_workspace_blocks_absolute_outside_path(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    with pytest.raises(WorkspaceViolation):
        ws.resolve("/etc/passwd")


def test_workspace_blocks_symlink_chain(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    secrets = tmp_path / "secrets"
    sandbox.mkdir()
    secrets.mkdir()
    (secrets / "creds").write_text("super-secret")
    (sandbox / "innocent").symlink_to(secrets / "creds")
    ws = Workspace.create(sandbox)
    with pytest.raises(WorkspaceViolation):
        ws.resolve("innocent")


# ---------------------- Read / Write hardening ----------------------


@pytest.mark.asyncio
async def test_read_outside_workspace_returns_error(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await read_execute({"file_path": "../../../etc/passwd"}, ws)
    assert result.is_error
    assert "outside workspace" in result.content


@pytest.mark.asyncio
async def test_write_outside_workspace_returns_error(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await write_execute(
        {"file_path": "/tmp/escape-attempt", "content": "x"}, ws
    )
    assert result.is_error
    assert "outside workspace" in result.content


# ---------------------- Bash hardening ----------------------


@pytest.mark.asyncio
async def test_bash_rejects_nul_byte(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await bash_execute({"command": "echo a\x00b"}, ws)
    assert result.is_error
    assert "NUL" in result.content


@pytest.mark.asyncio
async def test_bash_rejects_overlong_command(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    long_cmd = "echo " + ("x" * (MAX_COMMAND_BYTES + 100))
    result = await bash_execute({"command": long_cmd}, ws)
    assert result.is_error
    assert "too long" in result.content


@pytest.mark.asyncio
async def test_bash_denylist_token_match(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await bash_execute(
        {"command": "curl https://example.invalid"}, ws, denylist=("curl",)
    )
    assert result.is_error
    assert "denylist" in result.content


@pytest.mark.asyncio
async def test_bash_denylist_substring_match(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    # Substring rule catches the literal pattern as it appears in the command
    result = await bash_execute(
        {"command": "echo 'fetch | bash' && true"}, ws, denylist=("fetch | bash",)
    )
    assert result.is_error
    assert "denylist" in result.content


def test_sanitize_env_strips_secret_named_vars() -> None:
    raw = {
        "PATH": "/usr/bin",
        "MY_API_KEY": "secret",
        "GITHUB_TOKEN": "ghp_xx",
        "SOME_PASSWORD": "p",
        "AZURE_CLIENT_SECRET": "z",
        "GH_TOKEN": "y",
        "GENERIC_AUTH_HEADER": "h",
        "HOME": "/root",
    }
    cleaned = _sanitize_env(raw)
    for sensitive in (
        "MY_API_KEY",
        "GITHUB_TOKEN",
        "SOME_PASSWORD",
        "AZURE_CLIENT_SECRET",
        "GH_TOKEN",
        "GENERIC_AUTH_HEADER",
    ):
        assert sensitive not in cleaned, sensitive
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["HOME"] == "/root"


def test_sensitive_fragments_cover_common_keys() -> None:
    expected = {"TOKEN", "SECRET", "PASSWORD", "API_KEY", "AUTH"}
    assert expected.issubset(set(SENSITIVE_ENV_NAME_FRAGMENTS))


@pytest.mark.asyncio
async def test_bash_runs_inside_workspace(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    result = await bash_execute({"command": "pwd"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    assert os.path.realpath(result.content.strip()) == os.path.realpath(str(tmp_path))


@pytest.mark.asyncio
async def test_bash_cancelled_kills_subprocess(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)

    async def _run() -> None:
        await bash_execute({"command": "sleep 30", "timeout": 60}, ws)

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
