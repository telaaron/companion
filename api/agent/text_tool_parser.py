"""Parse Claude-CLI XML-style tool calls embedded in plain text.

Some upstream models (notably DeepSeek when shown Anthropic-shaped tool
definitions) emit tool calls as plain text inside the assistant content
instead of as native ``tool_use`` content blocks. The text form looks like::

    <tool_calls>
    <invoke name="Bash">
    <parameter name="command">mkdir -p ~/Desktop/foo</parameter>
    </invoke>
    </tool_calls>

This module extracts those calls into synthetic ``tool_use`` block dicts
ready for the agent loop's normal dispatch path. The companion text content
is also returned with the matched XML stripped so the assistant message stays
coherent for the next upstream turn.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

_INVOKE_RE = re.compile(
    r"<invoke\s+name=\"([^\"]+)\"\s*>(.*?)</invoke>",
    re.DOTALL,
)
_PARAMETER_RE = re.compile(
    r"<parameter\s+name=\"([^\"]+)\"(?:\s+[^>]*)?>(.*?)</parameter>",
    re.DOTALL,
)
_WRAPPER_RE = re.compile(r"<tool_calls>\s*", re.IGNORECASE)
_WRAPPER_END_RE = re.compile(r"\s*</tool_calls>", re.IGNORECASE)


def extract_text_form_tool_uses(
    text: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Pull XML-style invocations out of ``text``.

    Returns ``(remaining_text, tool_use_blocks)``. ``remaining_text`` has the
    matched XML removed; ``tool_use_blocks`` follow the Anthropic
    ``content_block`` shape (``type``, ``id``, ``name``, ``input``).
    """
    if "<invoke" not in text:
        return text, []

    blocks: list[dict[str, Any]] = []
    for match in _INVOKE_RE.finditer(text):
        name = match.group(1)
        body = match.group(2)
        params: dict[str, Any] = {}
        for pmatch in _PARAMETER_RE.finditer(body):
            params[pmatch.group(1)] = _coerce_value(pmatch.group(2))
        blocks.append(
            {
                "type": "tool_use",
                "id": f"toolu_text_{uuid.uuid4().hex[:10]}",
                "name": name,
                "input": params,
            }
        )

    if not blocks:
        return text, []

    cleaned = _INVOKE_RE.sub("", text)
    cleaned = _WRAPPER_RE.sub("", cleaned)
    cleaned = _WRAPPER_END_RE.sub("", cleaned)
    return cleaned.strip(), blocks


def _coerce_value(raw: str) -> str:
    """Strip surrounding whitespace; leave the value as a string.

    Models occasionally wrap with leading/trailing newlines from indented XML.
    Tool input schemas accept strings for every PR1/PR2 tool, so no further
    coercion is needed.
    """
    return raw.strip()
