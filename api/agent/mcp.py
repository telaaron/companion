"""MCP (Model Context Protocol) client.

Spawns an MCP server as a subprocess, completes the initialize handshake,
lists its tools, and exposes each as a :class:`~core.tools.registry.ToolSpec`
for the Companion agent loop.

Wire protocol: JSON-RPC 2.0 over stdio, newline-delimited JSON. Each request
gets an integer ``id`` and the response is matched back via a futures dict.

References:
* https://modelcontextprotocol.io/specification
* https://github.com/modelcontextprotocol/servers — official + community
  servers like firecrawl-mcp, obsidian-mcp-server.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from core.tools.registry import ToolSpec, register_extras
from core.tools.result import ToolResult
from core.tools.workspace import Workspace

# Active client registry. Keyed by server name → MCPClient. Lets the
# dashboard inspect / restart servers via API later.
_CLIENTS: dict[str, MCPClient] = {}


@dataclass(slots=True)
class MCPClient:
    """One MCP server. Owns its subprocess + the request/response pipeline."""

    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)

    _proc: asyncio.subprocess.Process | None = None
    _read_task: asyncio.Task | None = None
    _next_id: int = 1
    _inflight: dict[int, asyncio.Future] = field(default_factory=dict)
    _tools: list[dict[str, Any]] = field(default_factory=list)
    _initialized: bool = False
    _last_error: str = ""

    async def start(self) -> list[dict[str, Any]]:
        """Spawn the server, complete handshake, return its tool list."""
        full_env = {**os.environ, **self.env}
        binary = shutil.which(self.command) or self.command
        try:
            self._proc = await asyncio.create_subprocess_exec(
                binary,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
            )
        except FileNotFoundError as exc:
            self._last_error = f"binary not found: {self.command}"
            raise RuntimeError(self._last_error) from exc

        self._read_task = asyncio.create_task(self._read_loop())

        # Drain stderr in the background so the server isn't blocked.
        asyncio.create_task(self._drain_stderr())

        try:
            await asyncio.wait_for(self._initialize(), timeout=20.0)
            tools = await asyncio.wait_for(self._list_tools(), timeout=20.0)
        except TimeoutError as exc:
            self._last_error = "handshake timeout"
            await self.stop()
            raise RuntimeError(self._last_error) from exc
        self._initialized = True
        self._tools = tools
        return tools

    async def _initialize(self) -> None:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "companion", "version": "1.0.0"},
            },
        )
        # Some servers want a notification back.
        await self._notify("notifications/initialized", {})
        return result

    async def _list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return list(tools)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "tools/call", {"name": tool_name, "arguments": arguments}
        )

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"MCP {self.name}: not started")
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[rid] = fut
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        line = (json.dumps(payload) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()
        result = await fut
        return result

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        line = (json.dumps(payload) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                rid = msg.get("id")
                if rid in self._inflight:
                    fut = self._inflight.pop(rid)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("MCP {} read loop crashed: {}", self.name, exc)
        finally:
            # Fail any pending futures.
            for fut in list(self._inflight.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError("MCP server closed"))
            self._inflight.clear()

    async def _drain_stderr(self) -> None:
        """Drain the MCP server's stderr and forward to the debug log.

        Capped at ~4 MB per minute per server so a busted MCP that prints
        unbounded errors can't pin memory through loguru's queue or
        explode the log file. Lines beyond the cap are counted and
        summarised once the window resets.
        """
        assert self._proc is not None
        if self._proc.stderr is None:
            return
        max_bytes_per_window = 4 * 1024 * 1024  # 4 MB / 60 s
        window_seconds = 60.0
        bytes_in_window = 0
        dropped_in_window = 0
        window_start = time.monotonic()
        try:
            async for line in self._proc.stderr:
                now = time.monotonic()
                if now - window_start > window_seconds:
                    if dropped_in_window:
                        logger.warning(
                            "MCP {}: dropped {} stderr lines in last {:.0f}s (cap reached)",
                            self.name,
                            dropped_in_window,
                            window_seconds,
                        )
                    window_start = now
                    bytes_in_window = 0
                    dropped_in_window = 0
                bytes_in_window += len(line)
                if bytes_in_window > max_bytes_per_window:
                    dropped_in_window += 1
                    continue
                # Truncate any single absurdly long line.
                snippet = line[:4096]
                logger.debug(
                    "MCP {}: {}",
                    self.name,
                    snippet.decode(errors="replace").rstrip(),
                )
        except Exception:
            pass

    async def stop(self) -> None:
        import contextlib

        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if self._proc is not None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except Exception:
                with contextlib.suppress(Exception):
                    self._proc.kill()
        self._proc = None
        self._initialized = False

    @property
    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "args": self.args,
            "status": "running" if self._initialized else "stopped",
            "tools": [t.get("name") for t in self._tools],
            "tool_count": len(self._tools),
            "last_error": self._last_error,
        }


# ---------------------- Conversion of MCP tools to ToolSpec ----------------------


def _sanitize_tool_name(server_name: str, raw: str) -> str:
    """Turn ``mcp__server__tool`` into a clean Companion-side identifier."""
    base = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    prefix = re.sub(r"[^A-Za-z0-9]+", "", server_name).title()
    name = f"{prefix}_{base}" if prefix else base
    return name[:120]


def _sanitize_schema(schema: Any) -> dict[str, Any]:
    """Coerce an MCP tool schema into the subset Anthropic accepts."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": []}
    out: dict[str, Any] = {
        "type": schema.get("type", "object"),
        "properties": schema.get("properties", {}) or {},
    }
    if "required" in schema:
        out["required"] = list(schema["required"])
    else:
        out["required"] = []
    return out


def _build_spec(client: MCPClient, tool: dict[str, Any]) -> ToolSpec:
    raw_name = tool.get("name") or "unnamed"
    description = tool.get("description") or f"MCP tool from {client.name}"
    schema = _sanitize_schema(tool.get("inputSchema"))

    async def executor(input_data: dict[str, Any], _ws: Workspace) -> ToolResult:
        try:
            result = await client.call_tool(raw_name, input_data or {})
        except Exception as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        content_parts: list[str] = []
        if isinstance(result, dict):
            for block in result.get("content", []) or []:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        content_parts.append(str(block.get("text", "")))
                    else:
                        content_parts.append(json.dumps(block))
        body = "\n".join(content_parts) if content_parts else json.dumps(result)
        is_error = bool(result.get("isError")) if isinstance(result, dict) else False
        return ToolResult(content=body, is_error=is_error)

    return ToolSpec(
        name=_sanitize_tool_name(client.name, raw_name),
        description=f"[MCP · {client.name}] {description}",
        input_schema=schema,
        executor=executor,
    )


# ---------------------- Plugin loader entry-point ----------------------


async def register_mcp_server(
    *,
    name: str,
    command: str,
    args: list[str],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Start an MCP server + register all its tools into the agent registry."""
    if name in _CLIENTS:
        await _CLIENTS[name].stop()
    client = MCPClient(name=name, command=command, args=list(args), env=dict(env or {}))
    _CLIENTS[name] = client
    try:
        tools = await client.start()
    except Exception as exc:
        logger.warning("MCP {} start failed: {}", name, exc)
        return client.info
    specs = [_build_spec(client, t) for t in tools]
    if specs:
        register_extras(specs)
    logger.info("MCP {}: registered {} tool(s)", name, len(specs))
    return client.info


def list_clients() -> list[dict[str, Any]]:
    return [c.info for c in _CLIENTS.values()]


async def stop_all() -> None:
    for client in list(_CLIENTS.values()):
        await client.stop()
    _CLIENTS.clear()


# ---------------------- Auto-discover Claude Desktop config ----------------------


_CLAUDE_DESKTOP_CONFIG = "Library/Application Support/Claude/claude_desktop_config.json"

_CREDENTIAL_ARG_KEYS = {
    "--api-key",
    "--token",
    "--auth",
    "--secret",
    "--password",
    "--api-token",
    "--access-token",
    "--bearer",
    "--key",
    "-H",
    "--header",
    "--authorization",
}
_CREDENTIAL_ARG_PREFIXES = ("Bearer ", "bearer ", "Basic ", "basic ")


def _mask_credentials_in_args(args: list[str]) -> list[str]:
    """Replace credential values in CLI args with masked placeholders."""
    if not args:
        return args
    masked = []
    i = 0
    while i < len(args):
        arg = args[i]
        next_idx = i + 1
        # Check if this arg is a known credential flag
        if arg in _CREDENTIAL_ARG_KEYS and next_idx < len(args):
            masked.append(arg)
            masked.append(_mask_token_string(args[next_idx]))
            i += 2
            continue
        # Check if this arg is a --key=value pattern
        for flag in _CREDENTIAL_ARG_KEYS:
            if arg.startswith(flag + "="):
                prefix = flag + "="
                masked.append(prefix + _mask_token_string(arg[len(prefix) :]))
                i += 1
                break
        else:
            # Check if the arg value itself looks like a credential
            if _looks_like_credential(arg):
                masked.append(_mask_token_string(arg))
            else:
                masked.append(arg)
            i += 1
    return masked


def _looks_like_credential(value: str) -> bool:
    """Heuristic: does this value look like a secret token?"""
    if not value or len(value) < 16:
        return False
    # Hex strings >= 32 chars (common for API keys/tokens)
    if len(value) >= 32 and all(c in "0123456789abcdefABCDEF" for c in value):
        return True
    # Bearer/Basic tokens
    return any(value.startswith(p) for p in _CREDENTIAL_ARG_PREFIXES)


def _mask_token_string(value: str) -> str:
    """Mask a credential string: show first 4 + '****' + last 4."""
    if not value or len(value) <= 8:
        return "****"
    # Strip Bearer/Basic prefix then mask the token
    for prefix in _CREDENTIAL_ARG_PREFIXES:
        if value.startswith(prefix):
            token = value[len(prefix) :]
            return prefix + _mask_token_string(token)
    vis = min(4, len(value) // 4)
    return value[:vis] + "****" + value[-vis:]


def discover_claude_mcp_servers() -> list[dict[str, Any]]:
    """Read Claude Desktop's MCP config (if present) and return server specs."""
    from pathlib import Path

    path = Path.home() / _CLAUDE_DESKTOP_CONFIG
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("MCP: couldn't read Claude Desktop config: {}", exc)
        return []
    servers = data.get("mcpServers") or {}
    out: list[dict[str, Any]] = []
    for name, conf in servers.items():
        if not isinstance(conf, dict):
            continue
        if not conf.get("command"):
            continue  # http-only servers handled separately
        out.append(
            {
                "name": name,
                "command": conf.get("command"),
                "args": _mask_credentials_in_args(list(conf.get("args", []) or [])),
                "env": dict(conf.get("env", {}) or {}),
            }
        )
    return out


async def bootstrap_from_claude_desktop() -> list[dict[str, Any]]:
    """Start every MCP server defined in Claude Desktop's config file."""
    specs = discover_claude_mcp_servers()
    results: list[dict[str, Any]] = []
    for spec in specs:
        info = await register_mcp_server(**spec)
        results.append(info)
    return results
