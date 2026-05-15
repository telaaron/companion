"""Write tool — create or overwrite a file inside the workspace."""

from __future__ import annotations

from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace, WorkspaceViolation

MAX_BYTES = 50 * 1024 * 1024  # 50 MB hard cap


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    file_path = input_data.get("file_path")
    content = input_data.get("content")
    if not isinstance(file_path, str) or not file_path:
        return ToolResult(
            content="Error: 'file_path' is required and must be a string",
            is_error=True,
        )
    if not isinstance(content, str):
        return ToolResult(
            content="Error: 'content' is required and must be a string",
            is_error=True,
        )

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        return ToolResult(
            content=f"Error: content too large ({len(encoded)} bytes, max {MAX_BYTES})",
            is_error=True,
        )

    try:
        resolved = workspace.resolve(file_path)
    except WorkspaceViolation as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)

    if resolved.is_dir():
        return ToolResult(
            content=f"Error: path is a directory: {resolved}",
            is_error=True,
        )

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        existed = resolved.exists()
        resolved.write_bytes(encoded)
    except OSError as exc:
        return ToolResult(content=f"Error writing {resolved}: {exc}", is_error=True)

    verb = "Updated" if existed else "Created"
    return ToolResult(
        content=f"{verb} {resolved} ({len(encoded)} bytes)",
        metadata={
            "path": str(resolved),
            "bytes_written": len(encoded),
            "created": not existed,
        },
    )
