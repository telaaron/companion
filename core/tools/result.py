"""Result type returned by every local tool executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolResult:
    """Outcome of a single local tool execution.

    ``content`` is either a plain string (text result) or a list of Anthropic
    content blocks (for tools that return rich data such as images).
    """

    content: str | list[dict[str, Any]]
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_tool_result_block(self, tool_use_id: str) -> dict[str, Any]:
        """Render as an Anthropic ``tool_result`` content block."""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }
