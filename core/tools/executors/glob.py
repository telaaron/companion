"""Glob tool — match files by pattern within the workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace, WorkspaceViolation

DEFAULT_LIMIT = 1000
IGNORED_DIR_NAMES = {".git", "node_modules", ".venv", "__pycache__"}


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    pattern = input_data.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return ToolResult(
            content="Error: 'pattern' is required and must be a string",
            is_error=True,
        )

    base_path = input_data.get("path")
    try:
        base = (
            workspace.resolve(base_path)
            if isinstance(base_path, str) and base_path
            else workspace.root
        )
    except WorkspaceViolation as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)

    if not base.exists():
        return ToolResult(
            content=f"Error: search path not found: {base}", is_error=True
        )
    if not base.is_dir():
        return ToolResult(
            content=f"Error: search path is not a directory: {base}", is_error=True
        )

    try:
        matches = sorted(
            (p for p in base.glob(pattern) if not _is_ignored(p, base)),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
    except (OSError, ValueError) as exc:
        return ToolResult(content=f"Error globbing: {exc}", is_error=True)

    truncated = len(matches) > DEFAULT_LIMIT
    matches = matches[:DEFAULT_LIMIT]

    paths = [str(p) for p in matches]
    payload = {
        "pattern": pattern,
        "base": str(base),
        "matches": paths,
        "count": len(paths),
        "truncated": truncated,
    }
    return ToolResult(
        content=json.dumps(payload, indent=2),
        metadata={"count": len(paths), "truncated": truncated},
    )


def _is_ignored(path: Path, base: Path) -> bool:
    try:
        relative = path.relative_to(base)
    except ValueError:
        return False
    return any(part in IGNORED_DIR_NAMES for part in relative.parts)
