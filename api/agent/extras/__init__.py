"""Product-side tool specs registered into the core tool registry.

These tools depend on api-layer state (datastore, env file) so they cannot
live in ``core/tools/executors``. Importing this package adds them to the
global :mod:`core.tools.registry` extras dict.
"""

from __future__ import annotations

from core.tools.registry import ToolSpec, register_extras

from . import env_set as env_set_executor
from . import projects as projects_executor
from . import search as search_executor

_PROJECT_LIST = ToolSpec(
    name="ProjectList",
    description=(
        "List every project the proxy knows about. Returns id, name, "
        "description, workspace_path, color. Use to discover existing "
        "projects before creating duplicates."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    executor=projects_executor.execute_list,
)


_PROJECT_CREATE = ToolSpec(
    name="ProjectCreate",
    description=(
        "Create a new project. The agent loop scopes future chat sessions "
        "in this project to ``workspace_path`` and prepends ``shared_context`` "
        "to every system prompt."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Required. Short label."},
            "description": {"type": "string"},
            "shared_context": {
                "type": "string",
                "description": "Free-text brief — becomes the system prompt for all sessions in this project.",
            },
            "workspace_path": {
                "type": "string",
                "description": "Absolute path the agent loop sandboxes file ops to.",
            },
            "color": {"type": "string", "description": "Hex color, e.g. #6366f1."},
        },
        "required": ["name"],
    },
    executor=projects_executor.execute_create,
)


_PROJECT_UPDATE = ToolSpec(
    name="ProjectUpdate",
    description=(
        "Update fields on an existing project. Pass only the fields you want "
        "to change; others are preserved. ``project_id`` is required."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "shared_context": {"type": "string"},
            "workspace_path": {"type": "string"},
            "color": {"type": "string"},
        },
        "required": ["project_id"],
    },
    executor=projects_executor.execute_update,
)


_PROJECT_DELETE = ToolSpec(
    name="ProjectDelete",
    description="Delete a project by id. Sessions are detached, not deleted.",
    input_schema={
        "type": "object",
        "properties": {"project_id": {"type": "string"}},
        "required": ["project_id"],
    },
    executor=projects_executor.execute_delete,
)


_SEARCH = ToolSpec(
    name="Search",
    description=(
        "Keyword-rank search across the proxy's memory index (chat turns, "
        "indexed files, pinned notes). Useful for recalling earlier context "
        "or finding notes/code that match a topic. "
        "Supports FTS5 syntax: quoted phrases, prefix * suffix, AND/OR/NOT."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "kind": {
                "type": "string",
                "description": "Optional filter — 'message', 'file', or 'note'.",
            },
            "path_prefix": {
                "type": "string",
                "description": (
                    "Optional absolute path prefix to restrict file hits "
                    "(e.g. '/home/user/notes' returns only chunks from that dir)."
                ),
            },
            "project_id": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["query"],
    },
    executor=search_executor.execute,
)


_ENV_SET = ToolSpec(
    name="EnvSet",
    description=(
        "Set or rotate one environment variable in the proxy's managed env "
        "file. Hot-reloads the Settings cache so the change takes effect on "
        "the next request. Use this to enable agent_mode, swap providers, "
        "tighten the bash denylist, or rotate cloud-CLI tokens."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Env var name (uppercase, e.g. AGENT_MODE_ENABLED).",
            },
            "value": {"type": "string"},
        },
        "required": ["key", "value"],
    },
    executor=env_set_executor.execute,
)


register_extras(
    [
        _PROJECT_LIST,
        _PROJECT_CREATE,
        _PROJECT_UPDATE,
        _PROJECT_DELETE,
        _ENV_SET,
        _SEARCH,
    ]
)

# Discover user-defined plugins (YAML in ``plugins/``) and register their
# tools alongside the built-ins. Failure here is logged but never fatal.
try:
    from api.agent.plugins import load_all as _load_plugins

    _load_plugins()
except Exception:
    pass
