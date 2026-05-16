"""Plugin loader — scan ``plugins/*.yaml`` and register tools.

A plugin file looks like::

    name: my-vault
    kind: obsidian_vault
    vault_path: /Users/aaron/Documents/Obsidian Vault

Supported kinds:

* ``obsidian_vault`` — exposes ``ObsidianList`` / ``ObsidianRead`` /
  ``ObsidianAppend`` against ``vault_path``.
* ``mcp_server`` — placeholder; logs a TODO. Real MCP subprocess+JSON-RPC
  wiring tracked on the roadmap.

Plugins are loaded once on import. Add a file and restart the proxy to pick
it up.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from core.tools.registry import ToolSpec, register_extras
from core.tools.result import ToolResult
from core.tools.workspace import Workspace

PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


def discover_plugins() -> list[dict[str, Any]]:
    """Find every plugin YAML in :data:`PLUGINS_DIR` and parse it."""
    if not PLUGINS_DIR.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(PLUGINS_DIR.glob("*.yaml")):
        try:
            data = _parse_yaml(path.read_text(encoding="utf-8"))
            data["__source__"] = str(path)
            out.append(data)
        except Exception as exc:
            logger.warning("PLUGIN: failed to load {} ({})", path, exc)
    return out


def _parse_yaml(text: str) -> dict[str, Any]:
    """Tiny ``key: value`` YAML parser. No nesting, no lists.

    Keeps the proxy free of a yaml dep. Plugins are flat by convention.
    """
    result: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith(('"', "'")) and value.endswith(value[0]):
            value = value[1:-1]
        result[key] = value
    return result


# ---------------------- Obsidian vault tools ----------------------


def _obsidian_path(vault: Path, relative: str) -> Path:
    """Resolve a vault-relative path and reject escapes."""
    p = (vault / relative).resolve(strict=False)
    base = vault.resolve(strict=False)
    if base not in p.parents and p != base:
        raise ValueError(f"Path {p} is outside the Obsidian vault {base}")
    return p


def _obsidian_tools(name: str, vault_path: str) -> list[ToolSpec]:
    vault = Path(vault_path).expanduser()
    suffix = re.sub(r"[^A-Za-z0-9]+", "", name).strip() or "Vault"

    async def list_notes(input_data: dict[str, Any], _ws: Workspace) -> ToolResult:
        sub = input_data.get("folder") or ""
        try:
            base = _obsidian_path(vault, sub) if sub else vault
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        if not base.is_dir():
            return ToolResult(content=f"Error: not a directory: {base}", is_error=True)
        entries = []
        for p in sorted(
            base.rglob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True
        ):
            try:
                rel = p.relative_to(vault)
            except ValueError:
                continue
            entries.append({"path": str(rel), "size": p.stat().st_size})
            if len(entries) >= 200:
                break
        return ToolResult(
            content=json.dumps(
                {"vault": str(vault), "count": len(entries), "notes": entries},
                indent=2,
            )
        )

    async def read_note(input_data: dict[str, Any], _ws: Workspace) -> ToolResult:
        rel = (input_data.get("path") or "").strip()
        if not rel:
            return ToolResult(content="Error: 'path' is required", is_error=True)
        try:
            target = _obsidian_path(vault, rel)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        if not target.is_file():
            return ToolResult(content=f"Error: not a file: {target}", is_error=True)
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(content=f"Error reading: {exc}", is_error=True)
        return ToolResult(content=content)

    async def append_note(input_data: dict[str, Any], _ws: Workspace) -> ToolResult:
        rel = (input_data.get("path") or "").strip()
        content = input_data.get("content")
        if not rel or not isinstance(content, str):
            return ToolResult(
                content="Error: 'path' and 'content' are required",
                is_error=True,
            )
        try:
            target = _obsidian_path(vault, rel)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(content.rstrip("\n") + "\n")
        return ToolResult(
            content=f"Appended {len(content)} chars to {target}",
            metadata={"path": str(target), "bytes": len(content)},
        )

    return [
        ToolSpec(
            name=f"Obsidian{suffix}List",
            description=(
                f"List markdown notes in the Obsidian vault '{name}' "
                f"(rooted at {vault_path}). Pass ``folder`` to limit scope."
            ),
            input_schema={
                "type": "object",
                "properties": {"folder": {"type": "string"}},
                "required": [],
            },
            executor=list_notes,
        ),
        ToolSpec(
            name=f"Obsidian{suffix}Read",
            description=(
                f"Read a note from the Obsidian vault '{name}' by vault-relative path."
            ),
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            executor=read_note,
        ),
        ToolSpec(
            name=f"Obsidian{suffix}Append",
            description=(
                f"Append (or create) a note in the Obsidian vault '{name}'. "
                "Pass ``path`` (vault-relative) and ``content``."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            executor=append_note,
        ),
    ]


# ---------------------- Loader entry-point ----------------------


def load_all() -> None:
    """Discover plugins + register their tools. Idempotent on re-import."""
    plugins = discover_plugins()
    specs: list[ToolSpec] = []
    # MCP servers are async — collect specs and launch them after the sync
    # plugin types are registered so they're available even if MCP fails.
    mcp_servers: list[dict[str, Any]] = []
    for plugin in plugins:
        kind = plugin.get("kind", "")
        name = plugin.get("name") or Path(plugin.get("__source__", "plugin")).stem
        if kind == "obsidian_vault":
            vault_path = plugin.get("vault_path")
            if not vault_path:
                logger.warning(
                    "PLUGIN {}: obsidian_vault is missing vault_path — skipped",
                    plugin.get("__source__"),
                )
                continue
            specs.extend(_obsidian_tools(name, vault_path))
        elif kind == "mcp_server":
            command = plugin.get("command")
            if not command:
                logger.warning(
                    "PLUGIN {}: mcp_server is missing 'command' — skipped",
                    plugin.get("__source__"),
                )
                continue
            # MCP args come as a flat YAML field — split shell-style on spaces.
            args_raw = plugin.get("args") or ""
            args = [a for a in args_raw.split() if a]
            mcp_servers.append(
                {"name": name, "command": command, "args": args, "env": {}}
            )
        elif kind == "mcp_server":
            logger.info(
                "PLUGIN {}: kind=mcp_server is a placeholder — subprocess wiring "
                "is on the roadmap, no tool registered yet.",
                plugin.get("__source__"),
            )
        else:
            logger.warning(
                "PLUGIN {}: unknown kind '{}'", plugin.get("__source__"), kind
            )
    if specs:
        register_extras(specs)
        logger.info(
            "PLUGIN: registered {} tool(s) from {} plugin(s)", len(specs), len(plugins)
        )
    # MCP servers from YAML plugins are queued here; the real subprocess start
    # happens in AppRuntime._bootstrap_mcp_servers (async lifecycle hook).
    if mcp_servers:
        _PENDING_MCP_SERVERS.extend(mcp_servers)


_PENDING_MCP_SERVERS: list[dict[str, Any]] = []


def pending_mcp_servers() -> list[dict[str, Any]]:
    """Return YAML-defined MCP servers waiting for async startup."""
    return list(_PENDING_MCP_SERVERS)
