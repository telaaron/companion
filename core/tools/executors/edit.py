"""Edit tool — exact-string replacement inside a workspace file."""

from __future__ import annotations

from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace, WorkspaceViolation

MAX_BYTES = 50 * 1024 * 1024


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    file_path = input_data.get("file_path")
    old_string = input_data.get("old_string")
    new_string = input_data.get("new_string")
    replace_all = bool(input_data.get("replace_all", False))

    if not isinstance(file_path, str) or not file_path:
        return ToolResult(
            content="Error: 'file_path' is required and must be a string",
            is_error=True,
        )
    if not isinstance(old_string, str):
        return ToolResult(
            content="Error: 'old_string' is required and must be a string",
            is_error=True,
        )
    if not isinstance(new_string, str):
        return ToolResult(
            content="Error: 'new_string' is required and must be a string",
            is_error=True,
        )
    if old_string == new_string:
        return ToolResult(
            content="Error: 'old_string' and 'new_string' must differ",
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
            content=f"Error: path is a directory: {resolved}", is_error=True
        )

    try:
        original = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return ToolResult(content=f"Error reading {resolved}: {exc}", is_error=True)
    except UnicodeDecodeError:
        return ToolResult(
            content=f"Error: file is not valid UTF-8: {resolved}", is_error=True
        )

    occurrences = original.count(old_string)
    if occurrences == 0:
        return ToolResult(
            content=f"Error: 'old_string' not found in {resolved}",
            is_error=True,
        )
    if occurrences > 1 and not replace_all:
        return ToolResult(
            content=(
                f"Error: 'old_string' matched {occurrences} times in {resolved}; "
                "pass replace_all=true or include more context to disambiguate"
            ),
            is_error=True,
        )

    if replace_all:
        updated = original.replace(old_string, new_string)
        replaced = occurrences
    else:
        updated = original.replace(old_string, new_string, 1)
        replaced = 1

    encoded = updated.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        return ToolResult(
            content=f"Error: result too large ({len(encoded)} bytes, max {MAX_BYTES})",
            is_error=True,
        )

    try:
        resolved.write_bytes(encoded)
    except OSError as exc:
        return ToolResult(content=f"Error writing {resolved}: {exc}", is_error=True)

    return ToolResult(
        content=f"Edited {resolved} ({replaced} replacement(s))",
        metadata={
            "path": str(resolved),
            "replacements": replaced,
            "bytes_written": len(encoded),
        },
    )
