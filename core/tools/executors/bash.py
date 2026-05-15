"""Bash tool — run a shell command inside the workspace sandbox."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace

DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600
MAX_OUTPUT_BYTES = 1 * 1024 * 1024
MAX_COMMAND_BYTES = 16 * 1024  # reject absurdly long commands
SAFE_ENV_PREFIXES = ("PATH", "LANG", "LC_", "HOME", "SHELL", "TERM", "TMPDIR")
SENSITIVE_ENV_KEYS = {
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
}
# Substrings that mark an env var as carrying credentials. Match is
# case-insensitive against the variable name; matched vars are stripped from
# the subprocess environment regardless of provider.
SENSITIVE_ENV_NAME_FRAGMENTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "APIKEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "BEARER",
    "CREDENTIAL",
    "AUTH",
    "COOKIE",
    "SESSION",
)


async def execute(
    input_data: dict[str, Any],
    workspace: Workspace,
    *,
    denylist: tuple[str, ...] = (),
    extra_env_allowlist: tuple[str, ...] = (),
) -> ToolResult:
    command = input_data.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolResult(
            content="Error: 'command' is required and must be a non-empty string",
            is_error=True,
        )
    if "\x00" in command:
        return ToolResult(
            content="Error: 'command' must not contain NUL bytes",
            is_error=True,
        )
    if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
        return ToolResult(
            content=(
                f"Error: command too long ({len(command.encode('utf-8'))} bytes, "
                f"max {MAX_COMMAND_BYTES})"
            ),
            is_error=True,
        )

    timeout_raw = input_data.get("timeout", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S
    try:
        timeout = min(max(int(timeout_raw), 1), MAX_TIMEOUT_S)
    except TypeError, ValueError:
        return ToolResult(content="Error: 'timeout' must be an integer", is_error=True)

    if violation := _denylist_violation(command, denylist):
        return ToolResult(
            content=f"Error: command rejected by denylist (matched '{violation}')",
            is_error=True,
        )

    env = _sanitize_env(os.environ, extra_allowlist=extra_env_allowlist)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace.root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return ToolResult(content=f"Error launching shell: {exc}", is_error=True)

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        return ToolResult(
            content=f"Error: command timed out after {timeout}s and was killed",
            is_error=True,
            metadata={"timeout": True, "duration_s": timeout},
        )
    except asyncio.CancelledError:
        # Caller (e.g. FastAPI on client disconnect) cancelled the agent loop.
        # Kill the subprocess so it does not outlive the request, then re-raise.
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise

    stdout = _decode_clip(stdout_b)
    stderr = _decode_clip(stderr_b)
    exit_code = proc.returncode if proc.returncode is not None else -1

    body = stdout
    if stderr:
        body = f"{body}\n[stderr]\n{stderr}" if body else f"[stderr]\n{stderr}"
    if not body:
        body = f"[no output, exit code {exit_code}]"

    return ToolResult(
        content=body,
        is_error=exit_code != 0,
        metadata={"exit_code": exit_code, "timeout": False},
    )


def _denylist_violation(command: str, denylist: tuple[str, ...]) -> str | None:
    if not denylist:
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    lowered_tokens = [t.lower() for t in tokens]
    for entry in denylist:
        needle = entry.strip().lower()
        if not needle:
            continue
        if needle in lowered_tokens:
            return entry
        if needle in command.lower():
            return entry
    return None


def _sanitize_env(
    env: os._Environ[str] | dict[str, str],
    *,
    extra_allowlist: tuple[str, ...] = (),
) -> dict[str, str]:
    """Build a sanitised subprocess env.

    Three-tier policy:

    1. Hard block: ``SENSITIVE_ENV_KEYS`` (SSH sockets etc.) are always
       stripped regardless of allowlist.
    2. Explicit allowlist: names in ``extra_allowlist`` are passed through —
       this is the user's escape hatch for cloud CLIs (``GITHUB_TOKEN``,
       ``VERCEL_TOKEN``, ``SUPABASE_*``) that *would* otherwise match the
       credential-name denylist.
    3. Default rules: drop ``AWS_``/``GCP_``/``AZURE_``/``GH_``/``GITHUB_``
       prefixes and ``TOKEN``/``SECRET``/``API_KEY``/``AUTH``/... fragments,
       then keep anything matching ``SAFE_ENV_PREFIXES``.
    """
    extras = {entry for entry in extra_allowlist if entry}
    cleaned: dict[str, str] = {}
    for key, value in env.items():
        if key in SENSITIVE_ENV_KEYS:
            continue
        if key in extras:
            cleaned[key] = value
            continue
        upper = key.upper()
        if upper.startswith(("AWS_", "GCP_", "AZURE_", "GH_", "GITHUB_")):
            continue
        if any(fragment in upper for fragment in SENSITIVE_ENV_NAME_FRAGMENTS):
            continue
        if any(key == prefix or key.startswith(prefix) for prefix in SAFE_ENV_PREFIXES):
            cleaned[key] = value
    cleaned.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return cleaned


def _decode_clip(data: bytes) -> str:
    if len(data) > MAX_OUTPUT_BYTES:
        clipped = data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        return clipped + "\n[output truncated]"
    return data.decode("utf-8", errors="replace")
