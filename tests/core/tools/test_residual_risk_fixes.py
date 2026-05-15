"""Tests covering the residual-risk fixes (R1, R2, R3, R4, R5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tools.executors.bash import _sanitize_env
from core.tools.executors.grep import (
    PYTHON_FALLBACK_MAX_FILE_BYTES,
    _looks_binary,
)
from core.tools.executors.grep import execute as grep_execute
from core.tools.registry import AgentContext, get
from core.tools.workspace import Workspace, WorkspaceViolation

# ---------------------- R1: Workspace component validation ----------------------


def test_workspace_rejects_nul_byte_in_path(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    with pytest.raises(WorkspaceViolation, match="NUL"):
        ws.resolve("foo\x00bar")


def test_workspace_rejects_overlong_path_component(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    too_long = "a" * 300
    with pytest.raises(WorkspaceViolation, match="component too long"):
        ws.resolve(too_long)


def test_workspace_rejects_overlong_total_path(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    parts = "/".join(["a" * 100 for _ in range(100)])
    with pytest.raises(WorkspaceViolation, match="too long"):
        ws.resolve(parts)


# ---------------------- R2: Grep binary skip + size cap ----------------------


def test_looks_binary_detects_nul(tmp_path: Path) -> None:
    f = tmp_path / "blob"
    f.write_bytes(b"hello\x00world")
    assert _looks_binary(f) is True


def test_looks_binary_text_is_false(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("plain text")
    assert _looks_binary(f) is False


@pytest.mark.asyncio
async def test_grep_python_fallback_skips_binary(tmp_path: Path, monkeypatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    (tmp_path / "data.bin").write_bytes(b"NEEDLE\x00binary")
    (tmp_path / "doc.txt").write_text("NEEDLE in plain file\n")
    ws = Workspace.create(tmp_path)
    result = await grep_execute({"pattern": "NEEDLE"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    assert "doc.txt" in result.content
    assert "data.bin" not in result.content


@pytest.mark.asyncio
async def test_grep_python_fallback_skips_oversize(tmp_path: Path, monkeypatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    big = tmp_path / "big.txt"
    big.write_text("x" * (PYTHON_FALLBACK_MAX_FILE_BYTES + 10) + "\nNEEDLE\n")
    (tmp_path / "small.txt").write_text("NEEDLE\n")
    ws = Workspace.create(tmp_path)
    result = await grep_execute({"pattern": "NEEDLE"}, ws)
    assert not result.is_error
    assert isinstance(result.content, str)
    assert "small.txt" in result.content
    assert "big.txt" not in result.content


# ---------------------- R3: Bash extra-env allowlist ----------------------


def test_extra_env_allowlist_passes_through_safe_var() -> None:
    env = {"PATH": "/usr/bin", "NODE_OPTIONS": "--max-old-space-size=4096"}
    cleaned = _sanitize_env(env, extra_allowlist=("NODE_OPTIONS",))
    assert cleaned["NODE_OPTIONS"] == "--max-old-space-size=4096"


def test_extra_env_allowlist_unblocks_credentials_when_user_consents() -> None:
    """Explicit allowlist is the user's escape hatch for cloud CLI tokens.

    gh / vercel / supabase need credential-named env vars. Naming them in the
    allowlist counts as consent; the sanitizer passes them through.
    """
    env = {
        "PATH": "/usr/bin",
        "MY_SECRET_TOKEN": "leak-me",
        "GITHUB_TOKEN": "ghp_xxx",
    }
    cleaned = _sanitize_env(env, extra_allowlist=("MY_SECRET_TOKEN", "GITHUB_TOKEN"))
    assert cleaned["MY_SECRET_TOKEN"] == "leak-me"
    assert cleaned["GITHUB_TOKEN"] == "ghp_xxx"


def test_default_sanitize_still_blocks_credential_named_vars() -> None:
    """Without explicit allowlist, credential-named vars stay blocked."""
    env = {
        "PATH": "/usr/bin",
        "MY_SECRET_TOKEN": "leak-me",
        "GITHUB_TOKEN": "ghp_xxx",
    }
    cleaned = _sanitize_env(env)
    assert "MY_SECRET_TOKEN" not in cleaned
    assert "GITHUB_TOKEN" not in cleaned


# ---------------------- R4: extra_kwargs_factory ----------------------


def test_bash_spec_factory_supplies_denylist_and_extra_env() -> None:
    spec = get("Bash")
    assert spec is not None
    ctx = AgentContext(
        bash_denylist=("rm",), bash_extra_env_allowlist=("NODE_OPTIONS",)
    )
    kwargs = spec.build_extra_kwargs(ctx)
    assert kwargs == {
        "denylist": ("rm",),
        "extra_env_allowlist": ("NODE_OPTIONS",),
    }


def test_non_bash_spec_factory_returns_empty_kwargs() -> None:
    for name in ("Read", "Write", "LS", "Edit", "Glob", "Grep"):
        spec = get(name)
        assert spec is not None, name
        assert spec.build_extra_kwargs(AgentContext()) == {}
