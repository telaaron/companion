"""Project-management tools — the AI manages its own organizational state.

Unlike Read/Write/Bash these aren't filesystem-sandboxed; they operate on
the proxy's SQLite metadata store. The workspace argument is ignored.
"""

from __future__ import annotations

import json
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace

MAX_NAME = 120
MAX_DESCRIPTION = 4000
MAX_SHARED_CONTEXT = 20000
MAX_WORKSPACE_PATH = 4096


async def execute_list(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    from api import datastore

    rows = datastore.list_projects()
    summary = [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r.get("description", ""),
            "workspace_path": r.get("workspace_path", ""),
            "color": r.get("color", ""),
        }
        for r in rows
    ]
    return ToolResult(content=json.dumps({"projects": summary}, indent=2))


async def execute_create(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    from api import datastore

    name = (input_data.get("name") or "").strip()
    if not name:
        return ToolResult(content="Error: 'name' is required", is_error=True)
    if len(name) > MAX_NAME:
        return ToolResult(
            content=f"Error: name too long (max {MAX_NAME})", is_error=True
        )
    description = (input_data.get("description") or "")[:MAX_DESCRIPTION]
    shared_context = (input_data.get("shared_context") or "")[:MAX_SHARED_CONTEXT]
    workspace_path = (input_data.get("workspace_path") or "")[:MAX_WORKSPACE_PATH]
    color = (input_data.get("color") or "#6366f1")[:16]

    project = datastore.upsert_project(
        name=name,
        description=description,
        shared_context=shared_context,
        color=color,
        workspace_path=workspace_path,
    )
    return ToolResult(
        content=json.dumps(
            {
                "ok": True,
                "id": project["id"],
                "name": project["name"],
                "workspace_path": project.get("workspace_path", ""),
            }
        ),
        metadata={"project_id": project["id"]},
    )


async def execute_update(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    from api import datastore

    project_id = (input_data.get("project_id") or "").strip()
    if not project_id:
        return ToolResult(content="Error: 'project_id' is required", is_error=True)
    existing = datastore.get_project(project_id)
    if not existing:
        return ToolResult(
            content=f"Error: project {project_id} not found", is_error=True
        )

    merged = dict(existing)
    for key in ("name", "description", "shared_context", "color", "workspace_path"):
        if key in input_data and input_data[key] is not None:
            merged[key] = str(input_data[key])

    updated = datastore.upsert_project(
        project_id=project_id,
        name=merged["name"],
        description=merged.get("description", ""),
        shared_context=merged.get("shared_context", ""),
        color=merged.get("color", "#6366f1"),
        workspace_path=merged.get("workspace_path", ""),
    )
    return ToolResult(
        content=json.dumps({"ok": True, "id": updated["id"], "name": updated["name"]})
    )


async def execute_delete(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    from api import datastore

    project_id = (input_data.get("project_id") or "").strip()
    if not project_id:
        return ToolResult(content="Error: 'project_id' is required", is_error=True)
    existing = datastore.get_project(project_id)
    if not existing:
        return ToolResult(
            content=f"Error: project {project_id} not found", is_error=True
        )
    datastore.delete_project(project_id)
    return ToolResult(content=f"Deleted project {project_id} ({existing['name']})")
