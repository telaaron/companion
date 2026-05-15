"""Audit trail for server-side tool execution.

Each :class:`ToolInvocation` produced by the agent loop is emitted as a
structured TRACE row via :func:`core.trace.trace_event`. The fields are
sanitised — large or sensitive values (full file contents, raw bash output)
are replaced with summaries so the audit log stays scannable.
"""

from __future__ import annotations

from typing import Any

from core.trace import trace_event

from .agent_loop import ToolInvocation

_INPUT_PREVIEW_CHARS = 200
_RESULT_PREVIEW_CHARS = 240


def record_tool_invocation(
    invocation: ToolInvocation,
    *,
    request_id: str | None,
    workspace_root: str,
    duration_ms: int,
) -> None:
    """Emit one ``api.agent_loop.tool_use`` audit event."""
    trace_event(
        stage="egress",
        event="api.agent_loop.tool_use",
        source="api",
        request_id=request_id,
        workspace=workspace_root,
        tool=invocation.name,
        tool_use_id=invocation.tool_use_id,
        input_summary=_summarise_input(invocation.input),
        result_preview=_summarise_result(invocation.result.content),
        is_error=invocation.result.is_error,
        bytes_written=_extract_metric(invocation, "bytes_written"),
        replacements=_extract_metric(invocation, "replacements"),
        exit_code=_extract_metric(invocation, "exit_code"),
        duration_ms=duration_ms,
    )


def record_rate_limited(
    *,
    request_id: str | None,
    workspace_root: str,
    tool_name: str,
    limit_per_minute: int,
) -> None:
    """Emit one rejection audit event when a tool call is rate-limited."""
    trace_event(
        stage="egress",
        event="api.agent_loop.rate_limited",
        source="api",
        request_id=request_id,
        workspace=workspace_root,
        tool=tool_name,
        limit_per_minute=limit_per_minute,
    )


def _summarise_input(input_data: dict[str, Any]) -> str:
    if not input_data:
        return "{}"
    parts: list[str] = []
    for key, value in input_data.items():
        text = str(value)
        if len(text) > _INPUT_PREVIEW_CHARS:
            text = text[: _INPUT_PREVIEW_CHARS - 1] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _summarise_result(content: Any) -> str:
    text = content if isinstance(content, str) else str(content)
    if len(text) > _RESULT_PREVIEW_CHARS:
        return text[: _RESULT_PREVIEW_CHARS - 1] + "…"
    return text


def _extract_metric(invocation: ToolInvocation, key: str) -> Any:
    metadata = invocation.result.metadata
    return metadata.get(key) if isinstance(metadata, dict) else None
