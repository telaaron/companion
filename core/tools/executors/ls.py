"""LS tool — list directory entries with type and size."""

from __future__ import annotations

import json
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace, WorkspaceViolation


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    path = input_data.get("path")
    if not isinstance(path, str) or not path:
        return ToolResult(
            content="Error: 'path' is required and must be a string",
            is_error=True,
        )

    try:
        resolved = workspace.resolve(path)
    except WorkspaceViolation as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)

    if not resolved.exists():
        return ToolResult(content=f"Error: path not found: {resolved}", is_error=True)
    if not resolved.is_dir():
        return ToolResult(
            content=f"Error: path is not a directory: {resolved}",
            is_error=True,
        )

    entries: list[dict[str, Any]] = []
    try:
        children = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except OSError as exc:
        return ToolResult(content=f"Error listing {resolved}: {exc}", is_error=True)

    for child in children:
        try:
            stat = child.stat()
        except OSError:
            continue
        if child.is_dir():
            entry_type = "dir"
            size: int | None = None
        elif child.is_symlink():
            entry_type = "symlink"
            size = stat.st_size
        else:
            entry_type = "file"
            size = stat.st_size
        entries.append({"name": child.name, "type": entry_type, "size": size})

    payload = {"path": str(resolved), "entries": entries}
    return ToolResult(
        content=json.dumps(payload, indent=2),
        metadata={"path": str(resolved), "entry_count": len(entries)},
    )
