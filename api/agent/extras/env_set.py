"""EnvSet tool — let the AI manage its own settings.

Writes a single key into ``~/.config/companion/.env`` and invalidates
the Settings cache so the change takes effect on the next request. The tool
exposes the same surface as ``PUT /v1/env`` but callable from within the
agent loop. Secret-named keys are accepted but the audit log records that
they were rotated rather than the value.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ENV_FILE = Path.home() / ".config" / "companion" / ".env"
_SECRET_FRAGMENTS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "AUTH", "BEARER")


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    key = (input_data.get("key") or "").strip()
    value = input_data.get("value", "")
    if not isinstance(value, str):
        value = str(value)
    if not _KEY_RE.match(key):
        return ToolResult(
            content="Error: 'key' must match ^[A-Z][A-Z0-9_]*$",
            is_error=True,
        )
    if len(value) > 20_000:
        return ToolResult(
            content="Error: value too long (max 20000 chars)",
            is_error=True,
        )

    _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = (
        _ENV_FILE.read_text(encoding="utf-8", errors="replace")
        if _ENV_FILE.is_file()
        else ""
    )
    lines = text.splitlines()
    written = False
    new_lines: list[str] = []
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for line in lines:
        if not written and pat.match(line):
            new_lines.append(f"{key}={value}")
            written = True
        else:
            new_lines.append(line)
    if not written:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    _ENV_FILE.write_text(new_text, encoding="utf-8")

    os.environ[key] = value
    try:
        from config.settings import get_settings

        get_settings.cache_clear()
    except Exception:
        pass

    is_secret = any(frag in key.upper() for frag in _SECRET_FRAGMENTS)
    display = f"<rotated, {len(value)} chars>" if is_secret else value[:80]
    return ToolResult(
        content=f"Set {key}={display}",
        metadata={"key": key, "secret": is_secret, "bytes": len(value)},
    )
