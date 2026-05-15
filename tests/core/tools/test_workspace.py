"""Unit tests for the Workspace sandbox path resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tools.workspace import Workspace, WorkspaceViolation


def test_resolve_relative_path_joined_to_root(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    resolved = ws.resolve("nested/file.txt")
    assert resolved == (tmp_path / "nested/file.txt").resolve()


def test_resolve_absolute_path_inside_root(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    target = tmp_path / "a.txt"
    assert ws.resolve(str(target)) == target.resolve()


def test_resolve_root_itself(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    assert ws.resolve(str(tmp_path)) == tmp_path.resolve()


def test_path_outside_root_rejected(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    with pytest.raises(WorkspaceViolation):
        ws.resolve("/etc/passwd")


def test_parent_traversal_rejected(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path / "sandbox")
    (tmp_path / "sandbox").mkdir()
    with pytest.raises(WorkspaceViolation):
        ws.resolve("../outside.txt")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    outside = tmp_path / "outside"
    sandbox.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    link = sandbox / "escape"
    link.symlink_to(outside)
    ws = Workspace.create(sandbox)
    with pytest.raises(WorkspaceViolation):
        ws.resolve("escape/secret.txt")


def test_empty_path_rejected(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path)
    with pytest.raises(WorkspaceViolation):
        ws.resolve("")


def test_allow_outside_root_bypasses_check(tmp_path: Path) -> None:
    ws = Workspace.create(tmp_path, allow_outside_root=True)
    assert ws.resolve("/etc") == Path("/etc").resolve()
