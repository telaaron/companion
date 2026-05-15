"""Collect a stream of Anthropic-format SSE chunks into a final message.

The proxy's provider adapters all emit Anthropic SSE events as raw strings
(``event: ...\\ndata: {...}\\n\\n``). The agent loop needs to interpret those
events to (a) materialise the assistant message blocks for follow-up turns and
(b) detect when ``stop_reason == "tool_use"`` to dispatch local tools.

This module parses event chunks and accumulates a single
:class:`CollectedMessage` containing ordered content blocks, the final stop
reason, and usage counters.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CollectedMessage:
    """Final assistant message reconstructed from streamed SSE events."""

    content_blocks: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    stop_sequence: str | None = None
    role: str = "assistant"
    model: str | None = None
    message_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content_blocks if b.get("type") == "tool_use"]

    def to_assistant_message(self) -> dict[str, Any]:
        return {"role": "assistant", "content": list(self.content_blocks)}


def _iter_events(raw: str) -> list[tuple[str | None, dict[str, Any] | None]]:
    """Split a raw chunk into a list of (event_type, parsed_data) tuples."""
    events: list[tuple[str | None, dict[str, Any] | None]] = []
    for block in raw.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        event_type: str | None = None
        data_payload: str | None = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                segment = line[5:].strip()
                data_payload = (
                    segment if data_payload is None else data_payload + segment
                )
        parsed: dict[str, Any] | None
        if data_payload is None:
            parsed = None
        else:
            try:
                parsed = json.loads(data_payload)
            except json.JSONDecodeError:
                parsed = None
        events.append((event_type, parsed))
    return events


async def collect(stream: AsyncIterator[str]) -> CollectedMessage:
    """Drain ``stream`` and return the reconstructed message."""
    msg = CollectedMessage()
    blocks_by_index: dict[int, dict[str, Any]] = {}
    json_buffers: dict[int, list[str]] = {}

    async for chunk in stream:
        for event_type, data in _iter_events(chunk):
            if data is None:
                continue
            actual_type = data.get("type") or event_type
            if actual_type == "message_start":
                message = data.get("message", {})
                msg.message_id = message.get("id")
                msg.model = message.get("model")
                if usage := message.get("usage"):
                    msg.usage.update(usage)
            elif actual_type == "content_block_start":
                index = int(data.get("index", 0))
                block = dict(data.get("content_block", {}))
                blocks_by_index[index] = block
                if block.get("type") == "tool_use":
                    json_buffers[index] = []
            elif actual_type == "content_block_delta":
                index = int(data.get("index", 0))
                delta = data.get("delta", {})
                block = blocks_by_index.get(index)
                if block is None:
                    continue
                d_type = delta.get("type")
                if d_type == "text_delta":
                    block["text"] = (block.get("text", "") or "") + delta.get(
                        "text", ""
                    )
                elif d_type == "thinking_delta":
                    block["thinking"] = (block.get("thinking", "") or "") + delta.get(
                        "thinking", ""
                    )
                elif d_type == "input_json_delta":
                    json_buffers.setdefault(index, []).append(
                        delta.get("partial_json", "")
                    )
                elif d_type == "signature_delta":
                    block["signature"] = (block.get("signature") or "") + delta.get(
                        "signature", ""
                    )
            elif actual_type == "content_block_stop":
                index = int(data.get("index", 0))
                block = blocks_by_index.get(index)
                if block is None:
                    continue
                if block.get("type") == "tool_use" and index in json_buffers:
                    raw = "".join(json_buffers.pop(index))
                    if raw:
                        try:
                            block["input"] = json.loads(raw)
                        except json.JSONDecodeError:
                            block.setdefault("input", {})
                            block["_raw_input"] = raw
                    else:
                        block.setdefault("input", {})
            elif actual_type == "message_delta":
                delta = data.get("delta", {})
                if (sr := delta.get("stop_reason")) is not None:
                    msg.stop_reason = sr
                if (ss := delta.get("stop_sequence")) is not None:
                    msg.stop_sequence = ss
                if usage := data.get("usage"):
                    msg.usage.update(usage)
            elif actual_type == "message_stop":
                pass
            elif actual_type == "error":
                msg.error = data.get("error", data)

    msg.content_blocks = [blocks_by_index[i] for i in sorted(blocks_by_index)]
    return msg
