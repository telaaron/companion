"""SQLite-backed local datastore for the dashboard.

Persists projects, sessions, messages, usage events, file edits, and audit
log entries to ``~/.cache/free-claude-code/data.sqlite3``. Single-process,
single-user — opens its own connection per call to keep things simple
(no connection pool needed at this scale).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

_DB_PATH = Path.home() / ".cache" / "free-claude-code" / "data.sqlite3"
_LOCK = threading.Lock()
_INIT_DONE = False


def _ensure_path() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _connect():
    _ensure_path()
    init_schema()
    conn = sqlite3.connect(str(_DB_PATH), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_schema() -> None:
    """Create tables if they don't exist. Idempotent."""
    global _INIT_DONE
    if _INIT_DONE:
        return
    with _LOCK:
        if _INIT_DONE:
            return
        _ensure_path()
        conn = sqlite3.connect(str(_DB_PATH), isolation_level=None, timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    shared_context TEXT NOT NULL DEFAULT '',
                    color TEXT NOT NULL DEFAULT '#6366f1',
                    workspace_path TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    title TEXT NOT NULL DEFAULT 'untitled',
                    model TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                    ON sessions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_project
                    ON sessions(project_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id)
                        ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, created_at);

                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    kind TEXT NOT NULL,        -- 'chat' | 'image_gen' | 'vision'
                    provider TEXT NOT NULL,    -- 'deepseek' | 'openrouter' | 'gemini' | ...
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    images INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    project_id TEXT,
                    session_id TEXT,
                    request_id TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts DESC);
                CREATE INDEX IF NOT EXISTS idx_usage_kind ON usage_events(kind, ts DESC);
                CREATE INDEX IF NOT EXISTS idx_usage_project
                    ON usage_events(project_id, ts DESC);

                CREATE TABLE IF NOT EXISTS file_edits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    op TEXT NOT NULL,          -- 'edit' | 'write' | 'create' | 'delete'
                    path TEXT NOT NULL,
                    bytes_delta INTEGER NOT NULL DEFAULT 0,
                    project_id TEXT,
                    session_id TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_file_edits_ts ON file_edits(ts DESC);
                CREATE INDEX IF NOT EXISTS idx_file_edits_path ON file_edits(path);

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    category TEXT NOT NULL,    -- 'obsidian' | 'env' | 'config' | 'system'
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_category
                    ON audit_log(category, ts DESC);

                CREATE TABLE IF NOT EXISTS agent_jobs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    project_id TEXT,
                    model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|error|cancelled
                    error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    finished_at INTEGER,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_session
                    ON agent_jobs(session_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_status
                    ON agent_jobs(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS agent_job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES agent_jobs(id)
                        ON DELETE CASCADE,
                    seq INTEGER NOT NULL,         -- monotonic within job
                    ts INTEGER NOT NULL,
                    event_type TEXT NOT NULL,     -- 'sse' | 'job_started' | 'job_finished' | 'job_error'
                    data TEXT NOT NULL DEFAULT '' -- raw SSE chunk OR json payload
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_job_events_seq
                    ON agent_job_events(job_id, seq);

                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    kind,           -- 'message' | 'file' | 'note'
                    title,
                    body,
                    ref,            -- session_id / path / id
                    project_id UNINDEXED,
                    ts UNINDEXED,
                    tokenize='porter unicode61'
                );
                """
            )
            # Lightweight column migrations: older DBs may predate later fields.
            import contextlib

            for column_sql in (
                "ALTER TABLE projects ADD COLUMN workspace_path TEXT NOT NULL DEFAULT ''",
            ):
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(column_sql)
            _INIT_DONE = True
        finally:
            conn.close()


def _ts() -> int:
    return int(time.time() * 1000)


# ============================================================ Projects


def list_projects() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_project(project_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_project(
    *,
    project_id: str | None = None,
    name: str,
    description: str = "",
    shared_context: str = "",
    color: str = "#6366f1",
    workspace_path: str = "",
) -> dict[str, Any]:
    pid = project_id or f"prj_{uuid.uuid4().hex[:12]}"
    now = _ts()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM projects WHERE id=?", (pid,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE projects
                   SET name=?, description=?, shared_context=?, color=?,
                       workspace_path=?, updated_at=?
                 WHERE id=?
                """,
                (
                    name,
                    description,
                    shared_context,
                    color,
                    workspace_path,
                    now,
                    pid,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO projects
                  (id, name, description, shared_context, color,
                   workspace_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    name,
                    description,
                    shared_context,
                    color,
                    workspace_path,
                    now,
                    now,
                ),
            )
    got = get_project(pid)
    assert got is not None
    return got


def delete_project(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


# ============================================================ Sessions


def list_sessions(*, project_id: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 500"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE project_id=? "
                "ORDER BY updated_at DESC LIMIT 500",
                (project_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_session(
    *,
    session_id: str | None = None,
    title: str = "untitled",
    model: str = "",
    project_id: str | None = None,
) -> dict[str, Any]:
    sid = session_id or f"ses_{uuid.uuid4().hex[:12]}"
    now = _ts()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM sessions WHERE id=?", (sid,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE sessions
                   SET title=?, model=?, project_id=?, updated_at=?
                 WHERE id=?
                """,
                (title, model, project_id, now, sid),
            )
        else:
            conn.execute(
                """
                INSERT INTO sessions (id, project_id, title, model,
                                      created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sid, project_id, title, model, now, now),
            )
    got = get_session(sid)
    assert got is not None
    return got


def delete_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))


def append_message(*, session_id: str, role: str, content: str) -> dict[str, Any]:
    mid = f"msg_{uuid.uuid4().hex[:12]}"
    now = _ts()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (mid, session_id, role, content, now),
        )
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
    # Index into memory_fts so the Search tool can find this turn later.
    # Look up project_id for project-scoped search filtering.
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT title, project_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        title = (row["title"] if row else "") or "untitled"
        pid = row["project_id"] if row else None
        memory_index(
            kind="message",
            title=f"{role}: {title}",
            body=content,
            ref=mid,
            project_id=pid,
        )
    except Exception:
        pass
    return {
        "id": mid,
        "session_id": session_id,
        "role": role,
        "content": content,
        "created_at": now,
    }


def list_messages(session_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================ Usage events


def record_usage(
    *,
    kind: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    images: int = 0,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
    project_id: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a usage event. Best effort: any DB error is logged but never
    raised — tracking must not break the request path."""
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events
                  (ts, kind, provider, model, input_tokens, output_tokens,
                   images, cost_usd, duration_ms, project_id, session_id,
                   request_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _ts(),
                    kind,
                    provider,
                    model,
                    int(input_tokens),
                    int(output_tokens),
                    int(images),
                    float(cost_usd),
                    int(duration_ms),
                    project_id,
                    session_id,
                    request_id,
                    json.dumps(metadata or {}),
                ),
            )
    except Exception as exc:
        logger.warning("USAGE: failed to record event ({})", exc)


def list_usage(
    *,
    limit: int = 200,
    since_ts: int | None = None,
    kind: str | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if since_ts is not None:
        where.append("ts >= ?")
        args.append(since_ts)
    if kind is not None:
        where.append("kind = ?")
        args.append(kind)
    if project_id is not None:
        where.append("project_id = ?")
        args.append(project_id)
    sql = "SELECT * FROM usage_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def usage_summary(*, since_ts: int = 0) -> dict[str, Any]:
    """Aggregate stats across the configured time window."""
    with _connect() as conn:
        totals = conn.execute(
            """
            SELECT
              COALESCE(SUM(input_tokens), 0)  AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(images), 0)        AS images,
              COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
              COUNT(*)                        AS events
            FROM usage_events
            WHERE ts >= ?
            """,
            (since_ts,),
        ).fetchone()
        by_provider = conn.execute(
            """
            SELECT provider,
                   COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(images), 0)        AS images,
                   COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
                   COUNT(*)                        AS events
            FROM usage_events
            WHERE ts >= ?
            GROUP BY provider
            ORDER BY cost_usd DESC
            """,
            (since_ts,),
        ).fetchall()
        by_model = conn.execute(
            """
            SELECT model,
                   provider,
                   COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
                   COUNT(*)                        AS events
            FROM usage_events
            WHERE ts >= ?
            GROUP BY model, provider
            ORDER BY cost_usd DESC
            """,
            (since_ts,),
        ).fetchall()
        by_kind = conn.execute(
            """
            SELECT kind, COUNT(*) AS events,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd
            FROM usage_events
            WHERE ts >= ?
            GROUP BY kind
            """,
            (since_ts,),
        ).fetchall()
    return {
        "totals": dict(totals),
        "by_provider": [dict(r) for r in by_provider],
        "by_model": [dict(r) for r in by_model],
        "by_kind": [dict(r) for r in by_kind],
    }


# ============================================================ File edits


def record_file_edit(
    *,
    op: str,
    path: str,
    bytes_delta: int = 0,
    project_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO file_edits
                  (ts, op, path, bytes_delta, project_id, session_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _ts(),
                    op,
                    path,
                    int(bytes_delta),
                    project_id,
                    session_id,
                    json.dumps(metadata or {}),
                ),
            )
    except Exception as exc:
        logger.warning("FILE_EDIT: failed to record ({})", exc)


def list_file_edits(
    *, limit: int = 200, project_id: str | None = None
) -> list[dict[str, Any]]:
    with _connect() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT * FROM file_edits ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM file_edits WHERE project_id=? ORDER BY ts DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
    return [dict(r) for r in rows]


# ============================================================ Audit log


def record_audit(
    *,
    category: str,
    event: str,
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, category, event, detail, metadata)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    _ts(),
                    category,
                    event,
                    detail,
                    json.dumps(metadata or {}),
                ),
            )
    except Exception as exc:
        logger.warning("AUDIT: failed to record ({})", exc)


def list_audit(
    *, limit: int = 200, category: str | None = None
) -> list[dict[str, Any]]:
    with _connect() as conn:
        if category is None:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE category=? ORDER BY ts DESC LIMIT ?",
                (category, limit),
            ).fetchall()
    return [dict(r) for r in rows]


# ============================================================ Agent jobs


def create_agent_job(
    *,
    session_id: str | None,
    project_id: str | None,
    model: str,
    request_payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a new agent job in ``pending`` state and return the row."""
    job_id = f"job_{uuid.uuid4().hex[:14]}"
    now = _ts()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_jobs
              (id, session_id, project_id, model, status, error,
               created_at, started_at, finished_at, request_json, metadata)
            VALUES (?, ?, ?, ?, 'pending', '', ?, NULL, NULL, ?, ?)
            """,
            (
                job_id,
                session_id,
                project_id,
                model,
                now,
                json.dumps(request_payload),
                json.dumps(metadata or {}),
            ),
        )
    return get_agent_job(job_id) or {}


def get_agent_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_agent_jobs(
    *, session_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    with _connect() as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT * FROM agent_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_jobs WHERE session_id=?"
                " ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
    return [dict(r) for r in rows]


def mark_agent_job_status(job_id: str, status: str, *, error: str = "") -> None:
    """Update lifecycle status. Allowed: running, done, error, cancelled."""
    now = _ts()
    with _connect() as conn:
        if status == "running":
            conn.execute(
                "UPDATE agent_jobs SET status=?, started_at=? WHERE id=?",
                (status, now, job_id),
            )
        elif status in ("done", "error", "cancelled"):
            conn.execute(
                "UPDATE agent_jobs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, now, error, job_id),
            )
        else:
            conn.execute("UPDATE agent_jobs SET status=? WHERE id=?", (status, job_id))


def append_agent_job_event(job_id: str, *, event_type: str, data: str) -> int:
    """Append one event to ``agent_job_events``. Returns the new seq number."""
    now = _ts()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM agent_job_events WHERE job_id=?",
            (job_id,),
        ).fetchone()
        next_seq = (row[0] if row else -1) + 1
        conn.execute(
            "INSERT INTO agent_job_events (job_id, seq, ts, event_type, data)"
            " VALUES (?, ?, ?, ?, ?)",
            (job_id, next_seq, now, event_type, data),
        )
    return next_seq


def list_agent_job_events(
    job_id: str, *, after_seq: int = -1, limit: int = 5000
) -> list[dict[str, Any]]:
    """Return events for ``job_id`` with ``seq > after_seq``, ordered by seq."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT seq, ts, event_type, data FROM agent_job_events"
            " WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
            (job_id, after_seq, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_agent_job_seq(job_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM agent_job_events WHERE job_id=?",
            (job_id,),
        ).fetchone()
    return int(row[0]) if row else -1


# ============================================================ Memory / RAG


def memory_index(
    *,
    kind: str,
    title: str,
    body: str,
    ref: str,
    project_id: str | None = None,
) -> None:
    """Add or refresh a memory row in FTS. Keyed by (kind, ref)."""
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM memory_fts WHERE kind=? AND ref=?", (kind, ref))
            conn.execute(
                "INSERT INTO memory_fts (kind, title, body, ref, project_id, ts)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (kind, title or "", body or "", ref, project_id or "", _ts()),
            )
    except Exception as exc:
        logger.warning("MEMORY: index failed ({})", exc)


def memory_search(
    query: str,
    *,
    kind: str | None = None,
    project_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Keyword-rank search across memory_fts. Returns ordered by FTS rank."""
    safe_query = (query or "").strip()
    if not safe_query:
        return []
    where = ["memory_fts MATCH ?"]
    args: list[Any] = [safe_query]
    if kind:
        where.append("kind=?")
        args.append(kind)
    if project_id:
        where.append("project_id=?")
        args.append(project_id)
    sql = (
        "SELECT kind, title, body, ref, project_id, ts FROM memory_fts WHERE "
        + " AND ".join(where)
        + " ORDER BY rank LIMIT ?"
    )
    args.append(limit)
    try:
        with _connect() as conn:
            rows = conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []  # malformed FTS query
    return [dict(r) for r in rows]


def memory_count() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()
    return int(row[0]) if row else 0


def db_path() -> Path:
    """Return the SQLite file path (for diagnostics)."""
    return _DB_PATH


def reset_for_tests(path: Path | None = None) -> None:
    """Test helper: switch the DB to a fresh path and re-init schema."""
    global _DB_PATH, _INIT_DONE
    if path is not None:
        _DB_PATH = path
    _INIT_DONE = False
    init_schema()


def all_paths_for_tests() -> Iterable[Path]:
    """Test helper: list known on-disk SQLite paths (main + WAL/shm)."""
    yield _DB_PATH
    yield _DB_PATH.with_suffix(_DB_PATH.suffix + "-wal")
    yield _DB_PATH.with_suffix(_DB_PATH.suffix + "-shm")
