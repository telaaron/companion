"""Grep tool — search file contents using ripgrep when available, else Python re.

Output modes:
    files_with_matches (default): newline-separated unique paths.
    content: ``path:line:match`` triples (ripgrep --line-number).
    count: ``path:N`` lines with per-file counts.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace, WorkspaceViolation

VALID_MODES = ("files_with_matches", "content", "count")
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
PYTHON_FALLBACK_MAX_FILE_BYTES = 5 * 1024 * 1024
PYTHON_FALLBACK_BINARY_SNIFF_BYTES = 4096
IGNORED_DIRS = {".git", "node_modules", ".venv", "__pycache__"}


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    pattern = input_data.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return ToolResult(
            content="Error: 'pattern' is required and must be a string",
            is_error=True,
        )

    mode = input_data.get("output_mode", "files_with_matches")
    if mode not in VALID_MODES:
        return ToolResult(
            content=f"Error: 'output_mode' must be one of {VALID_MODES}",
            is_error=True,
        )

    path_in = input_data.get("path")
    try:
        base = (
            workspace.resolve(path_in)
            if isinstance(path_in, str) and path_in
            else workspace.root
        )
    except WorkspaceViolation as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)

    if not base.exists():
        return ToolResult(content=f"Error: path not found: {base}", is_error=True)

    glob_filter = input_data.get("glob")
    case_insensitive = bool(input_data.get("case_insensitive", False))

    rg_path = shutil.which("rg")
    if rg_path is not None:
        return await _run_ripgrep(
            rg_path, pattern, base, mode, glob_filter, case_insensitive
        )
    return _run_python_grep(pattern, base, mode, glob_filter, case_insensitive)


async def _run_ripgrep(
    rg_path: str,
    pattern: str,
    base: Path,
    mode: str,
    glob_filter: Any,
    case_insensitive: bool,
) -> ToolResult:
    args: list[str] = [rg_path, "--no-heading", "--color", "never"]
    if mode == "files_with_matches":
        args.append("--files-with-matches")
    elif mode == "content":
        args.append("--line-number")
    else:
        args.append("--count")

    if case_insensitive:
        args.append("-i")
    if isinstance(glob_filter, str) and glob_filter:
        args.extend(["--glob", glob_filter])

    args.extend(["--", pattern, str(base)])

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if proc.returncode == 0:
        return _format_grep_result(stdout, mode)
    if proc.returncode == 1:
        return ToolResult(content="No matches found", metadata={"matches": 0})
    return ToolResult(
        content=f"Error: ripgrep exited {proc.returncode}: {stderr.strip()}",
        is_error=True,
    )


def _format_grep_result(stdout: str, mode: str) -> ToolResult:
    if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        truncated = stdout.encode("utf-8")[:MAX_OUTPUT_BYTES].decode(
            "utf-8", errors="replace"
        )
        return ToolResult(
            content=truncated + "\n[output truncated]",
            metadata={"truncated": True, "mode": mode},
        )
    return ToolResult(content=stdout.rstrip("\n"), metadata={"mode": mode})


def _run_python_grep(
    pattern: str,
    base: Path,
    mode: str,
    glob_filter: Any,
    case_insensitive: bool,
) -> ToolResult:
    try:
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return ToolResult(content=f"Error: invalid regex: {exc}", is_error=True)

    files: list[Path]
    if base.is_file():
        files = [base]
    else:
        glob_pat = (
            glob_filter if isinstance(glob_filter, str) and glob_filter else "**/*"
        )
        files = [
            p for p in base.glob(glob_pat) if p.is_file() and not _is_ignored(p, base)
        ]

    file_results: dict[str, list[tuple[int, str]]] = {}
    for file in files:
        try:
            stat = file.stat()
        except OSError:
            continue
        if stat.st_size > PYTHON_FALLBACK_MAX_FILE_BYTES:
            continue
        if _looks_binary(file):
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits: list[tuple[int, str]] = []
        for idx, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append((idx, line))
        if hits:
            file_results[str(file)] = hits

    if not file_results:
        return ToolResult(content="No matches found", metadata={"matches": 0})

    if mode == "files_with_matches":
        body = "\n".join(sorted(file_results))
    elif mode == "count":
        body = "\n".join(
            f"{path}:{len(hits)}" for path, hits in sorted(file_results.items())
        )
    else:
        lines: list[str] = []
        for path in sorted(file_results):
            for line_no, line in file_results[path]:
                lines.append(f"{path}:{line_no}:{line}")
        body = "\n".join(lines)

    if len(body.encode("utf-8")) > MAX_OUTPUT_BYTES:
        body = (
            body.encode("utf-8")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            + "\n[output truncated]"
        )
        return ToolResult(content=body, metadata={"truncated": True, "mode": mode})
    return ToolResult(content=body, metadata={"mode": mode})


def _looks_binary(path: Path) -> bool:
    """Cheap binary sniff: NUL byte in the first ``PYTHON_FALLBACK_BINARY_SNIFF_BYTES``."""
    try:
        with path.open("rb") as fh:
            head = fh.read(PYTHON_FALLBACK_BINARY_SNIFF_BYTES)
    except OSError:
        return True
    return b"\x00" in head


def _is_ignored(path: Path, base: Path) -> bool:
    try:
        relative = path.relative_to(base)
    except ValueError:
        return False
    return any(part in IGNORED_DIRS for part in relative.parts)


# Re-export for tests that need to inspect serialised metadata.
def serialize_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, indent=2)
