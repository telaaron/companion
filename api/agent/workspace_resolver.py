"""Resolve the :class:`Workspace` to use for a given Messages request.

Priority chain:

1. Explicit ``request.metadata['workspace_path']`` (string).
2. ``request.metadata['project_id']`` → ``Project.workspace_path`` from datastore.
3. ``Settings.agent_default_workspace``.
4. Process CWD.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from api.models.anthropic import MessagesRequest
from config.settings import Settings
from core.tools.workspace import Workspace


def resolve_workspace(request: MessagesRequest, settings: Settings) -> Workspace:
    candidate = (
        _from_metadata(request.metadata)
        or _from_project(request.metadata)
        or settings.agent_default_workspace
    )
    root = Path(candidate).expanduser() if candidate else Path(os.getcwd())
    return Workspace.create(root)


def _from_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    raw = metadata.get("workspace_path")
    return raw if isinstance(raw, str) and raw.strip() else None


def _from_project(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    pid = metadata.get("project_id")
    if not isinstance(pid, str) or not pid.strip():
        return None
    try:
        from api import datastore

        project = datastore.get_project(pid)
    except Exception:
        return None
    if not project:
        return None
    ws = project.get("workspace_path")
    return ws if isinstance(ws, str) and ws.strip() else None


def parse_bash_denylist(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())
