"""Per-turn SSE rewriter for the multi-turn agent loop.

The streaming agent loop runs N upstream turns to satisfy a single client
request. Each upstream turn emits its own ``message_start``/``message_stop``
lifecycle and starts content-block indices at zero. Anthropic clients only
expect one such lifecycle per response and unique ascending block indices.

This module rewrites each upstream stream so that:

* ``message_start`` is emitted only on the very first turn (caller decides).
* ``content_block_*`` events are reindexed against a shared global counter.
* ``input_json_delta`` payloads are buffered per block so the loop can
  reconstruct ``tool_use.input`` and dispatch the local executor.
* ``message_delta`` / ``message_stop`` are intercepted so the loop can decide
  whether to forward (final turn) or suppress (mid-loop turn).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from core.anthropic.sse import format_sse_event
from core.tools.sse_collector import _iter_events


@dataclass(slots=True)
class TurnState:
    """Mutable state accumulated while a single upstream turn streams."""

    blocks: dict[int, dict[str, Any]] = field(default_factory=dict)
    json_buffers: dict[int, list[str]] = field(default_factory=dict)
    stop_reason: str | None = None
    stop_sequence: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    message_id: str | None = None
    model: str | None = None

    def tool_uses_in_order(self) -> list[dict[str, Any]]:
        ordered: list[dict[str, Any]] = []
        for idx in sorted(self.blocks):
            block = self.blocks[idx]
            if block.get("type") == "tool_use":
                ordered.append(block)
        return ordered


@dataclass(slots=True)
class IndexAllocator:
    """Shared, monotonically increasing global content-block index."""

    next_index: int = 0

    def allocate(self) -> int:
        idx = self.next_index
        self.next_index += 1
        return idx


async def rewrite_turn(
    upstream: AsyncIterator[str],
    indexer: IndexAllocator,
    *,
    is_first_turn: bool,
    is_final_turn: bool,
) -> AsyncIterator[str]:
    """Stream-process one upstream turn.

    Yields rewritten SSE chunks for the client and updates ``indexer``. The
    final accumulated :class:`TurnState` can be obtained by passing in
    ``state`` via :func:`drive_turn` instead — this generator focuses on the
    output side. Callers that need both should use :func:`drive_turn`.
    """
    state = TurnState()
    async for chunk in _drive(
        upstream,
        indexer,
        state,
        is_first_turn=is_first_turn,
        is_final_turn=is_final_turn,
    ):
        yield chunk


async def drive_turn(
    upstream: AsyncIterator[str],
    indexer: IndexAllocator,
    state: TurnState,
    *,
    is_first_turn: bool,
    is_final_turn: bool,
) -> AsyncIterator[str]:
    """Stream-rewrite one turn and populate ``state`` in place."""
    async for chunk in _drive(
        upstream,
        indexer,
        state,
        is_first_turn=is_first_turn,
        is_final_turn=is_final_turn,
    ):
        yield chunk


async def _drive(
    upstream: AsyncIterator[str],
    indexer: IndexAllocator,
    state: TurnState,
    *,
    is_first_turn: bool,
    is_final_turn: bool,
) -> AsyncIterator[str]:
    local_to_global: dict[int, int] = {}

    async for chunk in upstream:
        for event_type, data in _iter_events(chunk):
            if data is None:
                continue
            actual = data.get("type") or event_type
            if actual == "message_start":
                message = data.get("message", {})
                state.message_id = message.get("id")
                state.model = message.get("model")
                if usage := message.get("usage"):
                    state.usage.update(usage)
                if is_first_turn:
                    yield format_sse_event("message_start", data)
                continue

            if actual == "ping":
                yield format_sse_event("ping", data)
                continue

            if actual == "content_block_start":
                local_idx = int(data.get("index", 0))
                global_idx = indexer.allocate()
                local_to_global[local_idx] = global_idx
                block = dict(data.get("content_block", {}))
                state.blocks[global_idx] = block
                if block.get("type") == "tool_use":
                    state.json_buffers[global_idx] = []
                rewritten = dict(data)
                rewritten["index"] = global_idx
                yield format_sse_event("content_block_start", rewritten)
                continue

            if actual == "content_block_delta":
                local_idx = int(data.get("index", 0))
                global_idx = local_to_global.get(local_idx)
                if global_idx is None:
                    continue
                delta = data.get("delta", {})
                d_type = delta.get("type")
                block = state.blocks.get(global_idx)
                if block is not None:
                    if d_type == "text_delta":
                        block["text"] = (block.get("text") or "") + delta.get(
                            "text", ""
                        )
                    elif d_type == "thinking_delta":
                        block["thinking"] = (block.get("thinking") or "") + delta.get(
                            "thinking", ""
                        )
                    elif d_type == "input_json_delta":
                        state.json_buffers.setdefault(global_idx, []).append(
                            delta.get("partial_json", "")
                        )
                rewritten = dict(data)
                rewritten["index"] = global_idx
                yield format_sse_event("content_block_delta", rewritten)
                continue

            if actual == "content_block_stop":
                local_idx = int(data.get("index", 0))
                global_idx = local_to_global.get(local_idx)
                if global_idx is None:
                    continue
                block = state.blocks.get(global_idx)
                if (
                    block is not None
                    and block.get("type") == "tool_use"
                    and global_idx in state.json_buffers
                ):
                    raw = "".join(state.json_buffers.pop(global_idx))
                    if raw:
                        try:
                            block["input"] = json.loads(raw)
                        except json.JSONDecodeError:
                            block.setdefault("input", {})
                    else:
                        block.setdefault("input", {})
                rewritten = dict(data)
                rewritten["index"] = global_idx
                yield format_sse_event("content_block_stop", rewritten)
                continue

            if actual == "message_delta":
                delta = data.get("delta", {})
                if (sr := delta.get("stop_reason")) is not None:
                    state.stop_reason = sr
                if (ss := delta.get("stop_sequence")) is not None:
                    state.stop_sequence = ss
                if usage := data.get("usage"):
                    state.usage.update(usage)
                if is_final_turn:
                    yield format_sse_event("message_delta", data)
                continue

            if actual == "message_stop":
                if is_final_turn:
                    yield format_sse_event("message_stop", data)
                continue

            if actual == "error":
                state.error = data.get("error", data)
                yield format_sse_event("error", data)
                continue

            # Unknown / passthrough event
            yield format_sse_event(actual or "unknown", data)
