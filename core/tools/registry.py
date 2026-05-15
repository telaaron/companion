"""Tool registry — single source of truth for local tool schemas + executors.

Each entry pairs an Anthropic-format tool definition (name + description +
input_schema) with the async executor that runs it. The agent loop iterates
this registry to advertise tools upstream and dispatch ``tool_use`` blocks
back to local handlers.

Phase B PR1 ships Read / Write / LS. Subsequent PRs append Bash / Glob /
Grep / Edit / WebFetch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from core.tools.executors import bash as bash_executor
from core.tools.executors import edit as edit_executor
from core.tools.executors import glob as glob_executor
from core.tools.executors import grep as grep_executor
from core.tools.executors import ls as ls_executor
from core.tools.executors import read as read_executor
from core.tools.executors import write as write_executor
from core.tools.result import ToolResult

ToolExecutor = Callable[..., Awaitable[ToolResult]]
ExtraKwargsFactory = Callable[["AgentContext"], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Per-request context exposed to per-tool ``extra_kwargs_factory`` hooks."""

    bash_denylist: tuple[str, ...] = ()
    bash_extra_env_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Definition + executor for a single local tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    executor: ToolExecutor
    extra_kwargs_factory: ExtraKwargsFactory | None = None

    def to_anthropic_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def build_extra_kwargs(self, ctx: AgentContext) -> dict[str, Any]:
        if self.extra_kwargs_factory is None:
            return {}
        return self.extra_kwargs_factory(ctx)


_READ = ToolSpec(
    name="Read",
    description=(
        "Read the contents of a file from the workspace. Returns the file "
        "content with line numbers (cat -n format). Use 'offset' and 'limit' "
        "to page through large files."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or workspace-relative path to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Zero-based line offset to start reading from.",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return.",
                "default": 2000,
            },
        },
        "required": ["file_path"],
    },
    executor=read_executor.execute,
)


_WRITE = ToolSpec(
    name="Write",
    description=(
        "Create a new file or overwrite an existing one with the provided "
        "content. Parent directories are created as needed."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or workspace-relative path to write.",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write.",
            },
        },
        "required": ["file_path", "content"],
    },
    executor=write_executor.execute,
)


_LS = ToolSpec(
    name="LS",
    description=(
        "List the entries of a directory in the workspace. Returns JSON with "
        "name, type (file/dir/symlink) and size."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or workspace-relative directory path.",
            },
        },
        "required": ["path"],
    },
    executor=ls_executor.execute,
)


_EDIT = ToolSpec(
    name="Edit",
    description=(
        "Replace exact text in a workspace file. Use 'replace_all' to substitute "
        "every occurrence; otherwise 'old_string' must match exactly once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    executor=edit_executor.execute,
)


_GLOB = ToolSpec(
    name="Glob",
    description=(
        "Find files matching a glob pattern within the workspace. Returns JSON "
        "with matched paths sorted by mtime descending."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {
                "type": "string",
                "description": "Optional subdirectory to search; defaults to workspace root.",
            },
        },
        "required": ["pattern"],
    },
    executor=glob_executor.execute,
)


_GREP = ToolSpec(
    name="Grep",
    description=(
        "Search file contents using ripgrep (or a Python fallback). Modes: "
        "files_with_matches (default), content, count."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "output_mode": {
                "type": "string",
                "enum": ["files_with_matches", "content", "count"],
                "default": "files_with_matches",
            },
            "case_insensitive": {"type": "boolean", "default": False},
        },
        "required": ["pattern"],
    },
    executor=grep_executor.execute,
)


def _bash_extra_kwargs(ctx: AgentContext) -> dict[str, Any]:
    return {
        "denylist": ctx.bash_denylist,
        "extra_env_allowlist": ctx.bash_extra_env_allowlist,
    }


_BASH = ToolSpec(
    name="Bash",
    description=(
        "Run a shell command inside the workspace root. Returns combined "
        "stdout/stderr; non-zero exit code surfaces as is_error=True."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120, max 600).",
                "default": 120,
            },
        },
        "required": ["command"],
    },
    executor=bash_executor.execute,
    extra_kwargs_factory=_bash_extra_kwargs,
)


_REGISTRY: dict[str, ToolSpec] = {
    tool.name: tool for tool in (_READ, _WRITE, _LS, _EDIT, _GLOB, _GREP, _BASH)
}

# Product-specific tools (depend on `api/` packages) are registered at import
# time via :func:`register_extras`. Keeps ``core/`` provider-neutral while
# letting the agent loop see one unified surface.
_EXTRAS: dict[str, ToolSpec] = {}


def register_extras(specs: list[ToolSpec]) -> None:
    """Add product-side tools to the registry. Idempotent on name collisions."""
    for spec in specs:
        _EXTRAS[spec.name] = spec


def all_tools() -> list[ToolSpec]:
    """Return every registered tool spec — core + product extras."""
    return list(_REGISTRY.values()) + list(_EXTRAS.values())


def anthropic_tool_definitions() -> list[dict[str, Any]]:
    """Return tool definitions in the Anthropic ``tools`` array format."""
    return [tool.to_anthropic_definition() for tool in all_tools()]


def get(name: str) -> ToolSpec | None:
    """Look up a tool spec by name, or None if not registered."""
    return _REGISTRY.get(name) or _EXTRAS.get(name)
