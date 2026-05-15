"""Search tool — keyword + ranked search over the proxy's memory index.

Indexes user/assistant chat turns automatically (see datastore.append_message).
Future kinds: ``file`` (workspace snippets), ``note`` (user-pinned memories).
The agent uses this to recall context the model itself would otherwise lose
between sessions.
"""

from __future__ import annotations

import json
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace


async def execute(input_data: dict[str, Any], workspace: Workspace) -> ToolResult:
    from api import datastore

    query = (input_data.get("query") or "").strip()
    if not query:
        return ToolResult(content="Error: 'query' is required", is_error=True)
    kind = input_data.get("kind") or None
    project_id = input_data.get("project_id") or None
    try:
        limit = int(input_data.get("limit") or 10)
    except TypeError, ValueError:
        limit = 10
    limit = max(1, min(limit, 50))

    rows = datastore.memory_search(query, kind=kind, project_id=project_id, limit=limit)
    if not rows:
        return ToolResult(content=f'No memory hits for query "{query}".')

    summary = [
        {
            "kind": r["kind"],
            "title": r["title"],
            "ref": r["ref"],
            "project_id": r.get("project_id") or None,
            "snippet": (r.get("body") or "")[:240],
        }
        for r in rows
    ]
    return ToolResult(
        content=json.dumps({"query": query, "hits": summary}, indent=2),
        metadata={"hits": len(rows)},
    )
