"""Streaming variant of the multi-turn agent loop (PR4).

Where :func:`api.agent.agent_loop.run_agent_to_completion` drains each
upstream turn into a single :class:`CollectedMessage`, this module yields
SSE chunks to the client as they arrive. The client sees one global
``message_start``/``message_stop`` lifecycle per request even though the
proxy may issue many upstream turns in between.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from api.agent.agent_loop import (
    DEFAULT_MAX_TURNS,
    StreamingProvider,
    ToolInvocation,
    _build_request_for_turn,
    _clone_messages,
    build_identity_prompt,
    dispatch_tool_audited,
)
from api.agent.rate_limit import ToolCallRateLimiter
from api.agent.sse_rewriter import IndexAllocator, TurnState, drive_turn
from api.agent.text_tool_parser import extract_text_form_tool_uses
from api.models.anthropic import MessagesRequest
from core.anthropic.sse import format_sse_event
from core.tools.registry import AgentContext
from core.tools.result import ToolResult
from core.tools.workspace import Workspace

_TOOL_RESULT_PREVIEW_CHARS = 400


async def run_agent_streaming(
    request: MessagesRequest,
    provider: StreamingProvider,
    workspace: Workspace,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    request_id: str | None = None,
    bash_denylist: tuple[str, ...] = (),
    bash_extra_env_allowlist: tuple[str, ...] = (),
    tool_call_limit_per_minute: int = 0,
    global_limiter: Any = None,
) -> AsyncIterator[str]:
    """Multi-turn agent loop that streams Anthropic SSE chunks to the client.

    Yields raw SSE event strings. Caller wraps them in a FastAPI
    ``StreamingResponse`` (see :func:`anthropic_sse_streaming_response`).
    """
    working_messages = _clone_messages(request.messages)
    indexer = IndexAllocator()
    aggregated_usage: dict[str, Any] = {}
    invocations: list[ToolInvocation] = []
    last_state: TurnState | None = None
    limiter = ToolCallRateLimiter(limit_per_minute=tool_call_limit_per_minute)
    agent_ctx = AgentContext(
        bash_denylist=bash_denylist,
        bash_extra_env_allowlist=bash_extra_env_allowlist,
    )
    identity_prompt = build_identity_prompt(request.model, str(workspace.root))

    halt_reason: str | None = None
    halt_sequence: str | None = None

    for turn_no in range(1, max_turns + 1):
        is_first_turn = turn_no == 1

        upstream_request = _build_request_for_turn(
            request, working_messages, identity_prompt=identity_prompt
        )
        upstream = provider.stream_response(upstream_request, request_id=request_id)

        state = TurnState()
        async for chunk in drive_turn(
            upstream,
            indexer,
            state,
            is_first_turn=is_first_turn,
            is_final_turn=False,
        ):
            yield chunk

        last_state = state
        _accumulate_usage(aggregated_usage, state.usage)

        if state.error is not None:
            # Error event already streamed via passthrough. Skip final lifecycle.
            return

        # Some upstreams emit Claude-CLI XML tool calls as plain text instead
        # of native ``tool_use`` blocks. Promote them so the loop can dispatch.
        _promote_text_form_tool_calls(state)

        if state.stop_reason != "tool_use":
            halt_reason = state.stop_reason or "end_turn"
            halt_sequence = state.stop_sequence
            break

        tool_uses = state.tool_uses_in_order()
        if not tool_uses:
            logger.warning("Streaming agent loop: tool_use stop but no tool blocks")
            halt_reason = "end_turn"
            break

        working_messages.append(_assistant_message_from_state(state))

        tool_result_blocks: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            invocation = await dispatch_tool_audited(
                tool_use,
                workspace,
                agent_ctx,
                request_id=request_id,
                limiter=limiter,
                global_limiter=global_limiter,
            )
            invocations.append(invocation)
            tool_result_blocks.append(
                invocation.result.as_tool_result_block(invocation.tool_use_id)
            )
            async for ev in _emit_synthetic_tool_result_block(invocation, indexer):
                yield ev

        working_messages.append({"role": "user", "content": tool_result_blocks})
    else:
        logger.warning("Streaming agent loop: hit max_turns={} cap", max_turns)
        halt_reason = "max_turns"

    async for ev in _emit_final_lifecycle(
        aggregated_usage,
        halt_reason or "end_turn",
        halt_sequence,
    ):
        yield ev
    _ = last_state  # retained for potential future telemetry hooks


def _assistant_message_from_state(state: TurnState) -> dict[str, Any]:
    blocks = [state.blocks[i] for i in sorted(state.blocks)]
    return {"role": "assistant", "content": blocks}


def _promote_text_form_tool_calls(state: TurnState) -> None:
    """Detect XML-style tool calls inside text blocks and add native tool_use.

    The text block keeps its original (visible) content; new ``tool_use``
    blocks are appended with fresh global indices so subsequent dispatch and
    the synthesised tool-result blocks slot in naturally. ``stop_reason`` is
    forced to ``tool_use`` so the loop continues.
    """
    if state.stop_reason == "tool_use" and state.tool_uses_in_order():
        return

    promoted: list[dict[str, Any]] = []
    for idx in sorted(state.blocks):
        block = state.blocks[idx]
        if block.get("type") != "text":
            continue
        text = block.get("text") or ""
        _, parsed = extract_text_form_tool_uses(text)
        promoted.extend(parsed)

    if not promoted:
        return

    next_index = max(state.blocks) + 1 if state.blocks else 0
    for tool_use in promoted:
        state.blocks[next_index] = tool_use
        next_index += 1
    state.stop_reason = "tool_use"


_SUMMABLE_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    }
)


def _accumulate_usage(aggregate: dict[str, Any], turn_usage: dict[str, Any]) -> None:
    """Sum well-known integer counters, last-write-wins for everything else.

    Naive ``int`` summing previously double-counted opaque per-turn fields the
    upstream might add (e.g. service-tier flags, model identifiers). Restricting
    summation to a fixed allow-list keeps cache and token accounting accurate
    while leaving forward-compatible fields untouched.
    """
    for key, value in turn_usage.items():
        if key in _SUMMABLE_USAGE_KEYS and isinstance(value, int):
            aggregate[key] = aggregate.get(key, 0) + value
        else:
            aggregate[key] = value


async def _emit_synthetic_tool_result_block(
    invocation: ToolInvocation, indexer: IndexAllocator
) -> AsyncIterator[str]:
    """Emit a visible text block summarising one local tool execution."""
    global_idx = indexer.allocate()
    text = _format_tool_block_text(invocation)
    yield format_sse_event(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": global_idx,
            "content_block": {"type": "text", "text": ""},
        },
    )
    yield format_sse_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": global_idx,
            "delta": {"type": "text_delta", "text": text},
        },
    )
    yield format_sse_event(
        "content_block_stop",
        {"type": "content_block_stop", "index": global_idx},
    )


def _format_tool_block_text(invocation: ToolInvocation) -> str:
    label = _summarise_input(invocation.name, invocation.input)
    icon = "✗" if invocation.result.is_error else "⏺"
    snippet = _truncate(_result_text(invocation.result), _TOOL_RESULT_PREVIEW_CHARS)
    return f"\n{icon} {label}\n  ⎿ {snippet}\n"


def _summarise_input(tool_name: str, input_data: dict[str, Any]) -> str:
    primary_keys = {
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "LS": "path",
        "Glob": "pattern",
        "Grep": "pattern",
        "Bash": "command",
    }
    key = primary_keys.get(tool_name)
    if key and key in input_data:
        value = str(input_data[key])
        return f"{tool_name}({_truncate(value, 80)})"
    return f"{tool_name}(...)"


def _result_text(result: ToolResult) -> str:
    if isinstance(result.content, str):
        return result.content
    try:
        return json.dumps(result.content)
    except TypeError, ValueError:
        return str(result.content)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


async def _emit_final_lifecycle(
    aggregated_usage: dict[str, Any],
    stop_reason: str,
    stop_sequence: str | None,
) -> AsyncIterator[str]:
    yield format_sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": stop_sequence},
            "usage": aggregated_usage,
        },
    )
    yield format_sse_event("message_stop", {"type": "message_stop"})
