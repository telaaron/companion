"""Read tool — return file contents with cat -n style line numbering."""

from __future__ import annotations

from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace, WorkspaceViolation

DEFAULT_LIMIT = 2000
MAX_BYTES = 100 * 1024 * 1024  # 100 MB hard cap


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    file_path = input_data.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return ToolResult(
            content="Error: 'file_path' is required and must be a string",
            is_error=True,
        )

    offset_raw = input_data.get("offset", 0) or 0
    limit_raw = input_data.get("limit", DEFAULT_LIMIT) or DEFAULT_LIMIT
    try:
        offset = max(int(offset_raw), 0)
        limit = max(int(limit_raw), 1)
    except TypeError, ValueError:
        return ToolResult(
            content="Error: 'offset' and 'limit' must be integers",
            is_error=True,
        )

    try:
        resolved = workspace.resolve(file_path)
    except WorkspaceViolation as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)

    if not resolved.exists():
        return ToolResult(content=f"Error: file not found: {resolved}", is_error=True)
    if resolved.is_dir():
        return ToolResult(
            content=f"Error: path is a directory, not a file: {resolved}",
            is_error=True,
        )

    size = resolved.stat().st_size
    if size > MAX_BYTES:
        return ToolResult(
            content=f"Error: file too large ({size} bytes, max {MAX_BYTES})",
            is_error=True,
        )

    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(content=f"Error reading {resolved}: {exc}", is_error=True)

    lines = text.splitlines()
    total = len(lines)
    window = lines[offset : offset + limit]
    numbered = "\n".join(
        f"{idx + 1 + offset}\t{line}" for idx, line in enumerate(window)
    )
    truncated = offset + limit < total
    metadata = {
        "path": str(resolved),
        "total_lines": total,
        "offset": offset,
        "limit": limit,
        "truncated": truncated,
    }
    return ToolResult(content=numbered, metadata=metadata)
