"""SQLite-backed local datastore for the dashboard.

Persists projects, sessions, messages, usage events, file edits, and audit
log entries to ``~/.cache/companion/data.sqlite3``. Single-process,
single-user — opens its own connection per call to keep things simple
(no connection pool needed at this scale).

Legacy ``~/.cache/free-claude-code/data.sqlite3`` is migrated on first
``init_schema()`` when present and the new location is empty.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

_DB_PATH = Path.home() / ".cache" / "companion" / "data.sqlite3"
_LEGACY_DB_PATH = Path.home() / ".cache" / "free-claude-code" / "data.sqlite3"
_LOCK = threading.Lock()
_INIT_DONE = False


def _ensure_path() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_db() -> None:
    """One-shot copy of the pre-rename SQLite file (and its WAL siblings).

    Triggered when the new ``~/.cache/companion/`` dir has no datastore yet
    but the legacy ``~/.cache/free-claude-code/`` dir does. We copy rather
    than move so an aborted migration leaves the old data intact.
    """
    if _DB_PATH.exists() or not _LEGACY_DB_PATH.exists():
        return
    _ensure_path()
    # The WAL/SHM siblings live next to the main DB file; their names are
    # ``data.sqlite3-wal`` / ``data.sqlite3-shm`` (the parent dir holds the
    # ``free-claude-code`` identity, not the filename).
    for filename in ("data.sqlite3", "data.sqlite3-wal", "data.sqlite3-shm"):
        src = _LEGACY_DB_PATH.parent / filename
        if src.exists():
            shutil.copy2(src, _DB_PATH.parent / filename)
    logger.info("Datastore: migrated legacy {} -> {}", _LEGACY_DB_PATH, _DB_PATH)


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


_SCHEMA_VERSION = 4  # bump when adding new migrations below


def init_schema() -> None:
    """Create tables if they don't exist. Idempotent."""
    global _INIT_DONE
    if _INIT_DONE:
        return
    with _LOCK:
        if _INIT_DONE:
            return
        _ensure_path()
        _migrate_legacy_db()
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

                CREATE TABLE IF NOT EXISTS project_memories (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id)
                        ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    source_session_id TEXT,
                    created_at INTEGER NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'default'
                );
                CREATE INDEX IF NOT EXISTS idx_project_memories_project
                    ON project_memories(project_id);
                CREATE INDEX IF NOT EXISTS idx_project_memories_project_created
                    ON project_memories(project_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS routines (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    trigger_type TEXT NOT NULL DEFAULT 'cron',
                    trigger_config TEXT NOT NULL DEFAULT '{}',
                    payload TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    project_id TEXT,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    last_run_ms INTEGER,
                    next_run_ms INTEGER,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_routines_user
                    ON routines(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_routines_next_run
                    ON routines(enabled, next_run_ms);

                CREATE TABLE IF NOT EXISTS routine_runs (
                    id TEXT PRIMARY KEY,
                    routine_id TEXT NOT NULL REFERENCES routines(id)
                        ON DELETE CASCADE,
                    job_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    started_at INTEGER NOT NULL,
                    finished_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_routine_runs_routine
                    ON routine_runs(routine_id, started_at DESC);
                """
            )
            # ----------------------------------------------------------
            # Lightweight column migrations: older DBs may predate later
            # fields. Each ALTER TABLE is wrapped in contextlib.suppress
            # so it silently no-ops on databases that already have the
            # column. This block is idempotent by design.
            import contextlib

            for column_sql in (
                "ALTER TABLE projects ADD COLUMN workspace_path TEXT NOT NULL DEFAULT ''",
            ):
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(column_sql)

            # ----------------------------------------------------------
            # Version-gated migrations (PRAGMA user_version).
            # Each version block is a no-op when the DB is already at or
            # above the target version.
            row = conn.execute("PRAGMA user_version").fetchone()
            current_version = int(row[0]) if row else 0

            if current_version < 1:
                # v1: add workspace_path (already handled above via
                # suppress — left here as a guard in version history).
                conn.execute("PRAGMA user_version = 1")
                current_version = 1

            if current_version < 2:
                # v2: add user_id column to projects, sessions,
                # agent_jobs, audit_log, usage_events.  All existing
                # rows get the "default" bucket so single-user data is
                # preserved.  Indexes added for (user_id, updated_at)
                # on the two most-queried tables.
                for alter_sql in (
                    "ALTER TABLE projects ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'",
                    "ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'",
                    "ALTER TABLE agent_jobs ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'",
                    "ALTER TABLE audit_log ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'",
                    "ALTER TABLE usage_events ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'",
                ):
                    with contextlib.suppress(sqlite3.OperationalError):
                        conn.execute(alter_sql)
                # Composite indexes for per-user list queries.
                for idx_sql in (
                    "CREATE INDEX IF NOT EXISTS idx_sessions_user_updated ON sessions(user_id, updated_at DESC)",
                    "CREATE INDEX IF NOT EXISTS idx_projects_user_updated ON projects(user_id, updated_at DESC)",
                ):
                    with contextlib.suppress(sqlite3.OperationalError):
                        conn.execute(idx_sql)
                conn.execute("PRAGMA user_version = 2")
                current_version = 2

            if current_version < 3:
                # v3: add project_memories table for cross-session pinned
                # memories. The CREATE TABLE IF NOT EXISTS above handles fresh
                # DBs; this block ensures older DBs get the table too.
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS project_memories (
                            id TEXT PRIMARY KEY,
                            project_id TEXT NOT NULL,
                            content TEXT NOT NULL,
                            source_session_id TEXT,
                            created_at INTEGER NOT NULL,
                            user_id TEXT NOT NULL DEFAULT 'default'
                        );
                        CREATE INDEX IF NOT EXISTS idx_project_memories_project
                            ON project_memories(project_id);
                        CREATE INDEX IF NOT EXISTS idx_project_memories_project_created
                            ON project_memories(project_id, created_at DESC);
                        """
                    )
                conn.execute("PRAGMA user_version = 3")
                current_version = 3

            if current_version < 4:
                # v4: add routines + routine_runs tables for the scheduler.
                # The CREATE TABLE IF NOT EXISTS above handles fresh DBs;
                # this block ensures older DBs get the tables too.
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS routines (
                            id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            description TEXT NOT NULL DEFAULT '',
                            trigger_type TEXT NOT NULL DEFAULT 'cron',
                            trigger_config TEXT NOT NULL DEFAULT '{}',
                            payload TEXT NOT NULL DEFAULT '{}',
                            enabled INTEGER NOT NULL DEFAULT 1,
                            project_id TEXT,
                            user_id TEXT NOT NULL DEFAULT 'default',
                            last_run_ms INTEGER,
                            next_run_ms INTEGER,
                            created_at INTEGER NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_routines_user
                            ON routines(user_id, created_at DESC);
                        CREATE INDEX IF NOT EXISTS idx_routines_next_run
                            ON routines(enabled, next_run_ms);

                        CREATE TABLE IF NOT EXISTS routine_runs (
                            id TEXT PRIMARY KEY,
                            routine_id TEXT NOT NULL REFERENCES routines(id)
                                ON DELETE CASCADE,
                            job_id TEXT,
                            status TEXT NOT NULL DEFAULT 'pending',
                            started_at INTEGER NOT NULL,
                            finished_at INTEGER
                        );
                        CREATE INDEX IF NOT EXISTS idx_routine_runs_routine
                            ON routine_runs(routine_id, started_at DESC);
                        """
                    )
                conn.execute("PRAGMA user_version = 4")
                current_version = 4

            _INIT_DONE = True
        finally:
            conn.close()


def _ts() -> int:
    return int(time.time() * 1000)


# ============================================================ Projects


def list_projects(*, user_id: str = "default") -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,),
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
    user_id: str = "default",
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
                   workspace_path, created_at, updated_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    user_id,
                ),
            )
    got = get_project(pid)
    assert got is not None
    return got


def delete_project(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


# ============================================================ Sessions


def list_sessions(
    *, project_id: str | None = None, user_id: str = "default"
) -> list[dict[str, Any]]:
    with _connect() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE user_id=? "
                "ORDER BY updated_at DESC LIMIT 500",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE project_id=? AND user_id=? "
                "ORDER BY updated_at DESC LIMIT 500",
                (project_id, user_id),
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
    user_id: str = "default",
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
                                      created_at, updated_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, project_id, title, model, now, now, user_id),
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


def delete_message(message_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
        conn.execute("DELETE FROM memory_fts WHERE ref=?", (message_id,))


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
    user_id: str = "default",
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
                   request_id, metadata, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    user_id,
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
    user_id: str | None = None,
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
    if user_id is not None:
        where.append("user_id = ?")
        args.append(user_id)
    sql = "SELECT * FROM usage_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def session_usage(session_id: str) -> dict[str, Any]:
    """Return aggregate token + cost stats scoped to one session."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(input_tokens), 0)  AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
              COUNT(*)                        AS events
            FROM usage_events
            WHERE session_id=?
            """,
            (session_id,),
        ).fetchone()
    return (
        dict(row)
        if row
        else {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "events": 0,
        }
    )


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


def file_edit_for_path(path: str) -> dict[str, Any] | None:
    """Return the most recent ``file_edits`` row for ``path`` or ``None``.

    Used by the preview endpoint as a cross-project escape hatch: when the
    agent already wrote to a file (and therefore audited it), we trust
    that path for read-only preview even when it sits outside the
    configured workspace root.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM file_edits WHERE path=? ORDER BY ts DESC LIMIT 1",
            (path,),
        ).fetchone()
    return dict(row) if row else None


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
    user_id: str = "default",
) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, category, event, detail, metadata, user_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _ts(),
                    category,
                    event,
                    detail,
                    json.dumps(metadata or {}),
                    user_id,
                ),
            )
    except Exception as exc:
        logger.warning("AUDIT: failed to record ({})", exc)


def list_audit(
    *, limit: int = 200, category: str | None = None, user_id: str | None = None
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if category is not None:
        where.append("category=?")
        args.append(category)
    if user_id is not None:
        where.append("user_id=?")
        args.append(user_id)
    sql = "SELECT * FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


# ============================================================ Agent jobs


def create_agent_job(
    *,
    session_id: str | None,
    project_id: str | None,
    model: str,
    request_payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    """Insert a new agent job in ``pending`` state and return the row."""
    job_id = f"job_{uuid.uuid4().hex[:14]}"
    now = _ts()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_jobs
              (id, session_id, project_id, model, status, error,
               created_at, started_at, finished_at, request_json, metadata,
               user_id)
            VALUES (?, ?, ?, ?, 'pending', '', ?, NULL, NULL, ?, ?, ?)
            """,
            (
                job_id,
                session_id,
                project_id,
                model,
                now,
                json.dumps(request_payload),
                json.dumps(metadata or {}),
                user_id,
            ),
        )
    return get_agent_job(job_id) or {}


def get_agent_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_agent_jobs(
    *,
    session_id: str | None = None,
    limit: int = 100,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if session_id is not None:
        where.append("session_id=?")
        args.append(session_id)
    if user_id is not None:
        where.append("user_id=?")
        args.append(user_id)
    sql = "SELECT * FROM agent_jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
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
    """Append one event to ``agent_job_events``. Returns the new ``seq``.

    Implemented as a single ``INSERT … RETURNING`` so the write hits SQLite
    exactly once per chunk; the previous "SELECT MAX then INSERT" pattern
    doubled the write-lock pressure during high-rate SSE streams. The seq
    is computed inside the INSERT via a correlated subquery, which is safe
    because there is exactly one writer per ``job_id`` (the owning
    ``_run_job`` task).
    """
    now = _ts()
    with _connect() as conn:
        row = conn.execute(
            "INSERT INTO agent_job_events (job_id, seq, ts, event_type, data) "
            "VALUES ("
            "  ?,"
            "  (SELECT COALESCE(MAX(seq), -1) + 1 FROM agent_job_events"
            "    WHERE job_id = ?),"
            "  ?, ?, ?"
            ") RETURNING seq",
            (job_id, job_id, now, event_type, data),
        ).fetchone()
    return int(row[0]) if row else 0


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
    path_prefix: str | None = None,
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
    if path_prefix:
        where.append("ref LIKE ?")
        args.append(path_prefix + "%")
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


def memory_count_by_kind(kind: str) -> int:
    """Return the number of FTS rows with the given kind."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE kind=?", (kind,)
        ).fetchone()
    return int(row[0]) if row else 0


def memory_delete_path(path: str) -> None:
    """Remove all FTS chunks whose ref starts with *path* (kind='file')."""
    try:
        with _connect() as conn:
            conn.execute(
                "DELETE FROM memory_fts WHERE kind='file' AND ref LIKE ?",
                (path + "%",),
            )
    except Exception as exc:
        logger.warning("MEMORY: delete_path failed ({})", exc)


# ============================================================ Project memories (pinned)

_MEMORIES_HARD_CAP = 10
_MEMORIES_CHAR_CAP = 4000
_MEMORY_CONTENT_MAX = 4000


def pin_memory(
    *,
    project_id: str,
    content: str,
    source_session_id: str | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    """Insert a pinned memory for *project_id* and return the new row.

    Raises ``ValueError`` if the project has reached the hard cap (10 pins) or
    the combined content would exceed 4 000 characters.
    """
    content = content.strip()
    if not content:
        raise ValueError("content must not be empty")
    if len(content) > _MEMORY_CONTENT_MAX:
        raise ValueError(
            f"content exceeds maximum length of {_MEMORY_CONTENT_MAX} characters"
        )

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, content FROM project_memories WHERE project_id=? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        if len(existing) >= _MEMORIES_HARD_CAP:
            raise ValueError(
                f"project has reached the maximum of {_MEMORIES_HARD_CAP} pinned memories"
            )
        combined_len = sum(len(r["content"]) for r in existing) + len(content)
        if combined_len > _MEMORIES_CHAR_CAP:
            raise ValueError(
                f"combined memory content would exceed {_MEMORIES_CHAR_CAP} characters"
            )
        mid = f"mem_{uuid.uuid4().hex[:14]}"
        now = _ts()
        conn.execute(
            """
            INSERT INTO project_memories
              (id, project_id, content, source_session_id, created_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (mid, project_id, content, source_session_id, now, user_id),
        )
    row = get_memory(mid)
    assert row is not None
    return row


def get_memory(memory_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM project_memories WHERE id=?", (memory_id,)
        ).fetchone()
    return dict(row) if row else None


def list_memories(project_id: str) -> list[dict[str, Any]]:
    """Return all pinned memories for *project_id*, oldest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_memories WHERE project_id=? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_memory(memory_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_memories WHERE id=?", (memory_id,))


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


# ============================================================ Routines


def create_routine(
    *,
    name: str,
    description: str = "",
    trigger_type: str = "cron",
    trigger_config: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    enabled: bool = True,
    project_id: str | None = None,
    user_id: str = "default",
    next_run_ms: int | None = None,
) -> dict[str, Any]:
    """Insert a new routine and return the persisted row."""
    rid = f"rtn_{uuid.uuid4().hex[:14]}"
    now = _ts()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO routines
              (id, name, description, trigger_type, trigger_config,
               payload, enabled, project_id, user_id, last_run_ms,
               next_run_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                rid,
                name,
                description,
                trigger_type,
                json.dumps(trigger_config or {}),
                json.dumps(payload or {}),
                1 if enabled else 0,
                project_id,
                user_id,
                next_run_ms,
                now,
            ),
        )
    row = get_routine(rid)
    assert row is not None
    return row


def get_routine(routine_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM routines WHERE id=?", (routine_id,)
        ).fetchone()
    return dict(row) if row else None


def update_routine(
    routine_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    trigger_type: str | None = None,
    trigger_config: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    enabled: bool | None = None,
    next_run_ms: int | None = None,
    last_run_ms: int | None = None,
) -> dict[str, Any] | None:
    """Partial update of a routine. Only provided (non-None) fields change."""
    sets: list[str] = []
    args: list[Any] = []
    if name is not None:
        sets.append("name=?")
        args.append(name)
    if description is not None:
        sets.append("description=?")
        args.append(description)
    if trigger_type is not None:
        sets.append("trigger_type=?")
        args.append(trigger_type)
    if trigger_config is not None:
        sets.append("trigger_config=?")
        args.append(json.dumps(trigger_config))
    if payload is not None:
        sets.append("payload=?")
        args.append(json.dumps(payload))
    if enabled is not None:
        sets.append("enabled=?")
        args.append(1 if enabled else 0)
    if next_run_ms is not None:
        sets.append("next_run_ms=?")
        args.append(next_run_ms)
    if last_run_ms is not None:
        sets.append("last_run_ms=?")
        args.append(last_run_ms)
    if not sets:
        return get_routine(routine_id)
    args.append(routine_id)
    with _connect() as conn:
        conn.execute(
            f"UPDATE routines SET {', '.join(sets)} WHERE id=?",
            args,
        )
    return get_routine(routine_id)


def list_routines(
    *,
    user_id: str | None = None,
    enabled_only: bool = False,
    due_before_ms: int | None = None,
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if user_id is not None:
        where.append("user_id=?")
        args.append(user_id)
    if enabled_only:
        where.append("enabled=1")
    if due_before_ms is not None:
        where.append("next_run_ms <= ?")
        args.append(due_before_ms)
    sql = "SELECT * FROM routines"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def delete_routine(routine_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM routines WHERE id=?", (routine_id,))


def record_routine_run(
    *,
    routine_id: str,
    job_id: str | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    """Insert a routine_runs row and return it."""
    run_id = f"rrun_{uuid.uuid4().hex[:12]}"
    now = _ts()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO routine_runs (id, routine_id, job_id, status, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, routine_id, job_id, status, now),
        )
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM routine_runs WHERE id=?", (run_id,)
        ).fetchone()
    return dict(row) if row else {}


def update_routine_run(
    run_id: str,
    *,
    status: str,
    job_id: str | None = None,
    finished_at: int | None = None,
) -> None:
    now = finished_at if finished_at is not None else _ts()
    with _connect() as conn:
        if job_id is not None:
            conn.execute(
                "UPDATE routine_runs SET status=?, job_id=?, finished_at=? WHERE id=?",
                (status, job_id, now, run_id),
            )
        else:
            conn.execute(
                "UPDATE routine_runs SET status=?, finished_at=? WHERE id=?",
                (status, now, run_id),
            )


def list_routine_runs(
    routine_id: str,
    *,
    limit: int = 25,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return runs for a routine, newest first, paginated."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM routine_runs WHERE routine_id=?"
            " ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (routine_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_routine_runs(routine_id: str) -> int:
    """Return the total count of runs for a routine."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM routine_runs WHERE routine_id=?",
            (routine_id,),
        ).fetchone()
    return int(row[0]) if row else 0
