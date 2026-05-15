"""Resolve the :class:`Workspace` to use for a given Messages request.

Priority chain (PR5 baseline; PR6/PR7 will extend with per-session overrides):

1. Explicit ``request.metadata['workspace_path']`` (string).
2. ``Settings.agent_default_workspace``.
3. Process CWD.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from api.models.anthropic import MessagesRequest
from config.settings import Settings
from core.tools.workspace import Workspace


def resolve_workspace(request: MessagesRequest, settings: Settings) -> Workspace:
    candidate = _from_metadata(request.metadata) or settings.agent_default_workspace
    root = Path(candidate).expanduser() if candidate else Path(os.getcwd())
    return Workspace.create(root)


def _from_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    raw = metadata.get("workspace_path")
    return raw if isinstance(raw, str) and raw.strip() else None


def parse_bash_denylist(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())
