"""Insights computation — daily reflection over audit_log + usage_events.

Queries are user-scoped via ``user_id`` (multi-user isolation).
Results are cached per ``(since_ts, user_id)`` with a 5-minute TTL to avoid
hammering SQLite on every dashboard refresh.
"""

from __future__ import annotations

from typing import Any

from cachetools import TTLCache

from api import datastore

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ToolStat = dict[str, Any]  # {name, calls, avg_ms, errors}
ProviderStat = dict[str, Any]  # {provider, cost_usd, tokens}
Suggestion = dict[str, Any]  # {id, title, body, action}
Insights = dict[str, Any]  # {tools, providers, suggestions}

# ---------------------------------------------------------------------------
# Cache — keyed by (since_ts_bucket, user_id); 5-min TTL, max 32 entries
# ---------------------------------------------------------------------------

_TTL_S = 300  # 5 minutes
_cache: TTLCache[tuple[int, str], Insights] = TTLCache(maxsize=32, ttl=_TTL_S)

# Bucket resolution: round since_ts to the nearest minute so nearby requests
# share a cache entry without requiring exact millisecond match.
_BUCKET_MS = 60 * 1000


def _bucket(ts: int) -> int:
    """Round a millisecond timestamp down to the nearest minute."""
    return (ts // _BUCKET_MS) * _BUCKET_MS


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _tool_stats(since_ts: int, user_id: str) -> list[ToolStat]:
    """Aggregate audit_log rows into per-tool call/error/latency stats."""
    import sqlite3

    path = datastore.db_path()
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        # audit_log stores tool calls in category='tool' or category='agent'
        # with event='tool_call'/'tool_result'. We aggregate by detail (tool name)
        # for all tool_call events.
        rows = conn.execute(
            """
            SELECT
              detail                          AS name,
              COUNT(*)                        AS calls,
              AVG(CAST(
                COALESCE(
                  json_extract(metadata, '$.duration_ms'), 0
                ) AS REAL
              ))                              AS avg_ms,
              SUM(CASE WHEN
                json_extract(metadata, '$.error') IS NOT NULL
                AND json_extract(metadata, '$.error') != ''
                THEN 1 ELSE 0 END)            AS errors
            FROM audit_log
            WHERE ts >= ?
              AND user_id = ?
              AND category = 'tool'
              AND event = 'call'
            GROUP BY detail
            ORDER BY calls DESC
            LIMIT 20
            """,
            (since_ts, user_id),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "name": r["name"],
            "calls": r["calls"],
            "avg_ms": int(r["avg_ms"] or 0),
            "errors": r["errors"] or 0,
        }
        for r in rows
    ]


def _provider_stats(since_ts: int, user_id: str) -> list[ProviderStat]:
    """Aggregate usage_events into per-provider cost + token totals."""
    import sqlite3

    path = datastore.db_path()
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              provider,
              COALESCE(SUM(cost_usd), 0.0)                        AS cost_usd,
              COALESCE(SUM(input_tokens + output_tokens), 0)       AS tokens
            FROM usage_events
            WHERE ts >= ?
              AND user_id = ?
            GROUP BY provider
            ORDER BY cost_usd DESC
            LIMIT 20
            """,
            (since_ts, user_id),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "provider": r["provider"],
            "cost_usd": round(r["cost_usd"], 6),
            "tokens": r["tokens"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Bash-repeat heuristic
# ---------------------------------------------------------------------------


def _bash_repeat_suggestions(since_ts: int, user_id: str) -> list[Suggestion]:
    """Suggest a pin when a Bash command repeats > 20 times in the window."""
    import sqlite3

    path = datastore.db_path()
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT detail, COUNT(*) AS cnt
            FROM audit_log
            WHERE ts >= ?
              AND user_id = ?
              AND category = 'tool'
              AND event = 'call'
              AND detail LIKE '%Bash%'
            GROUP BY detail
            HAVING cnt > 20
            ORDER BY cnt DESC
            LIMIT 5
            """,
            (since_ts, user_id),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": f"bash-repeat-{r['detail'][:40]}",
            "title": "Frequent Bash command detected",
            "body": (
                f"The command '{r['detail']}' was called {r['cnt']} times "
                f"in the selected window. Consider pinning it as a project "
                f"script or alias to reduce repetition."
            ),
            "action": None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Provider cost heuristic
# ---------------------------------------------------------------------------


def _provider_cost_suggestions(provider_stats: list[ProviderStat]) -> list[Suggestion]:
    """Suggest routing to a cheaper provider when the top one exceeds $5."""
    if len(provider_stats) < 2:
        return []
    top = provider_stats[0]
    cheaper = provider_stats[-1]
    if top["cost_usd"] <= 5.0:
        return []
    if cheaper["provider"] == top["provider"]:
        return []
    return [
        {
            "id": f"provider-cost-{top['provider']}",
            "title": "High provider cost — cheaper alternative available",
            "body": (
                f"'{top['provider']}' cost ${top['cost_usd']:.2f} in the selected "
                f"window. '{cheaper['provider']}' is configured and was cheaper. "
                f"Consider routing your default model through the cheaper provider."
            ),
            "action": None,
        }
    ]


# ---------------------------------------------------------------------------
# Tool error-rate heuristic
# ---------------------------------------------------------------------------


def _tool_error_suggestions(tool_stats: list[ToolStat]) -> list[Suggestion]:
    """Suggest denylist tweak when a tool's error rate exceeds 10%."""
    suggestions: list[Suggestion] = []
    for t in tool_stats:
        if t["calls"] < 5:
            continue
        rate = t["errors"] / t["calls"]
        if rate <= 0.10:
            continue
        pct = int(rate * 100)
        suggestions.append(
            {
                "id": f"tool-error-{t['name']}",
                "title": f"High error rate for tool '{t['name']}'",
                "body": (
                    f"'{t['name']}' had a {pct}% error rate "
                    f"({t['errors']}/{t['calls']} calls). "
                    f"Consider adding it to the agent bash denylist or reviewing "
                    f"its configuration."
                ),
                "action": {
                    "type": "env_set",
                    "key": "AGENT_BASH_DENYLIST",
                    "hint": t["name"],
                },
            }
        )
    return suggestions[:3]  # cap at 3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_insights(since_ts: int, user_id: str) -> Insights:
    """Return a fresh or cached :class:`Insights` dict.

    Args:
        since_ts: Unix timestamp in **milliseconds**; rows older than this
            are excluded.
        user_id: Caller's user id for multi-user scoping.

    Returns:
        ``{tools, providers, suggestions}`` typed dict.
    """
    key = (_bucket(since_ts), user_id)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    tool_stats = _tool_stats(since_ts, user_id)
    provider_stats = _provider_stats(since_ts, user_id)

    suggestions: list[Suggestion] = []
    suggestions.extend(_bash_repeat_suggestions(since_ts, user_id))
    suggestions.extend(_provider_cost_suggestions(provider_stats))
    suggestions.extend(_tool_error_suggestions(tool_stats))

    result: Insights = {
        "tools": tool_stats,
        "providers": provider_stats,
        "suggestions": suggestions,
    }
    _cache[key] = result
    return result


def bust_cache() -> None:
    """Clear the in-process insights cache (useful in tests)."""
    _cache.clear()
