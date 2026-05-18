"""Preference management tools — PreferenceSet / PreferenceDelete / PreferenceList.

Preferences are stored as a Markdown bullet list in the path configured via
``PREFERENCES_PATH`` (default ``~/.config/free-claude-code/preferences.md``).
Each bullet has the form::

    - **key**: value

Atomic writes use a tmpfile + rename so the file is never left in a partial
state.  Keys and values are validated as plain strings, max 200 chars each.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace

# Format: ``- **key**: value``
_LINE_RE = re.compile(r"^\s*-\s+\*\*([^*]+)\*\*:\s*(.*)")
_MAX_CHARS = 200


def _prefs_path() -> Path:
    """Return the configured preferences file path."""
    from config.settings import get_settings

    return Path(get_settings().preferences_path)


def _load_prefs(path: Path) -> dict[str, str]:
    """Parse preferences file into an ordered dict {key: value}."""
    if not path.is_file():
        return {}
    prefs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE_RE.match(line)
        if m:
            prefs[m.group(1).strip()] = m.group(2).strip()
    return prefs


def _save_prefs(path: Path, prefs: dict[str, str]) -> None:
    """Atomically write ``prefs`` to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"- **{k}**: {v}" for k, v in prefs.items()]
    text = "\n".join(lines)
    if text:
        text += "\n"
    # Atomic write: write to tmp in same directory, then rename.
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, text.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def load_prefs_text() -> str:
    """Return the formatted preferences block for system-prompt injection.

    Returns an empty string when no preferences are set.
    """
    path = _prefs_path()
    prefs = _load_prefs(path)
    if not prefs:
        return ""
    lines = ["Long-term user preferences (always respect):"]
    lines.extend(f"- **{k}**: {v}" for k, v in prefs.items())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


async def execute_set(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    """PreferenceSet — write or update a preference."""
    key = (input_data.get("key") or "").strip()
    value = (input_data.get("value") or "").strip()

    if not key:
        return ToolResult(content="Error: 'key' must not be empty", is_error=True)
    if len(key) > _MAX_CHARS:
        return ToolResult(
            content=f"Error: 'key' exceeds {_MAX_CHARS} characters", is_error=True
        )
    if not isinstance(value, str):
        return ToolResult(content="Error: 'value' must be a string", is_error=True)
    if len(value) > _MAX_CHARS:
        return ToolResult(
            content=f"Error: 'value' exceeds {_MAX_CHARS} characters", is_error=True
        )

    path = _prefs_path()
    prefs = _load_prefs(path)
    action = "Updated" if key in prefs else "Recorded"
    prefs[key] = value
    _save_prefs(path, prefs)
    return ToolResult(
        content=f"{action} preference — **{key}**: {value}",
        metadata={"key": key, "value": value, "action": action.lower()},
    )


async def execute_delete(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    """PreferenceDelete — remove a preference by key."""
    key = (input_data.get("key") or "").strip()
    if not key:
        return ToolResult(content="Error: 'key' must not be empty", is_error=True)

    path = _prefs_path()
    prefs = _load_prefs(path)
    if key not in prefs:
        return ToolResult(
            content=f"Preference '{key}' not found — nothing to delete",
            metadata={"key": key, "found": False},
        )
    del prefs[key]
    _save_prefs(path, prefs)
    return ToolResult(
        content=f"Deleted preference '{key}'",
        metadata={"key": key, "found": True},
    )


async def execute_list(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    """PreferenceList — return all current preferences."""
    path = _prefs_path()
    prefs = _load_prefs(path)
    if not prefs:
        return ToolResult(
            content="No preferences set.",
            metadata={"preferences": {}},
        )
    lines = [f"- **{k}**: {v}" for k, v in prefs.items()]
    return ToolResult(
        content="\n".join(lines),
        metadata={"preferences": dict(prefs)},
    )
