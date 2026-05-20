"""Dashboard API routes — projects, sessions, usage, env vault, root files,
skills, file edits, audit log, model discovery.

These endpoints back the browser UI mounted at ``/ui``. They live behind
the same auth as the Anthropic-compat routes (``ANTHROPIC_AUTH_TOKEN``).
All paths are JSON. Reads are cheap; writes are rate-limited only by
the global proxy rate limiter (single-user assumption).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel, Field

from config.settings import Settings
from providers.registry import ProviderRegistry

from . import datastore
from .dependencies import CurrentUserId, get_settings, require_api_key
from .pricing import known_image_prices, known_token_prices, pricing_snapshot

dashboard_router = APIRouter()

_ENV_FILE = Path.home() / ".config" / "free-claude-code" / ".env"
_ROOT_FILES_ALLOWLIST = (
    "AGENTS.md",
    "CLAUDE.md",
    "PLAN.md",
    "ROADMAP.md",
    "README.md",
    ".env.example",
)
_SECRET_KEY_HINTS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
)


# ============================================================ Identity


@dashboard_router.get("/v1/me")
async def me_route(
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return the caller's resolved user_id and daily-journal banner flag.

    ``has_today_journal`` is ``True`` when a journal entry already exists for
    today's date, so the UI knows whether to suppress the journal banner.
    Useful for the UI to display the active user and detect whether
    multi-user mode is in effect (``user_id != "default"``).
    """
    import datetime

    from api.agent.journal import has_journal_entry

    today = datetime.date.today()
    try:
        has_journal = has_journal_entry(today)
    except Exception:
        has_journal = False
    return {"user_id": user_id, "has_today_journal": has_journal}


# ============================================================ Projects


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    shared_context: str = Field(default="", max_length=20000)
    color: str = Field(default="#6366f1", max_length=16)
    workspace_path: str = Field(default="", max_length=4096)


@dashboard_router.get("/v1/projects")
async def list_projects(
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    return {"projects": datastore.list_projects(user_id=user_id)}


@dashboard_router.post("/v1/projects")
async def create_project(
    body: ProjectIn,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    return datastore.upsert_project(
        name=body.name,
        description=body.description,
        shared_context=body.shared_context,
        color=body.color,
        workspace_path=body.workspace_path,
        user_id=user_id,
    )


@dashboard_router.get("/v1/projects/{project_id}")
async def get_project(
    project_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    project = datastore.get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


@dashboard_router.put("/v1/projects/{project_id}")
async def update_project(
    project_id: str, body: ProjectIn, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    return datastore.upsert_project(
        project_id=project_id,
        name=body.name,
        description=body.description,
        shared_context=body.shared_context,
        color=body.color,
        workspace_path=body.workspace_path,
    )


@dashboard_router.delete("/v1/projects/{project_id}")
async def delete_project_route(
    project_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    datastore.delete_project(project_id)
    return {"ok": True}


# ============================================================ Project memories


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    source_session_id: str | None = None


@dashboard_router.get("/v1/projects/{project_id}/memories")
async def list_project_memories(
    project_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    if datastore.get_project(project_id) is None:
        raise HTTPException(404, "project not found")
    return {"memories": datastore.list_memories(project_id)}


@dashboard_router.post("/v1/projects/{project_id}/memories")
async def pin_project_memory(
    project_id: str,
    body: MemoryIn,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    if datastore.get_project(project_id) is None:
        raise HTTPException(404, "project not found")
    try:
        memory = datastore.pin_memory(
            project_id=project_id,
            content=body.content,
            source_session_id=body.source_session_id,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return memory


@dashboard_router.delete("/v1/projects/{project_id}/memories/{memory_id}")
async def delete_project_memory(
    project_id: str,
    memory_id: str,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    mem = datastore.get_memory(memory_id)
    if mem is None or mem.get("project_id") != project_id:
        raise HTTPException(404, "memory not found")
    datastore.delete_memory(memory_id)
    return {"ok": True}


# ============================================================ Sessions


class SessionIn(BaseModel):
    title: str = Field(default="untitled", max_length=200)
    model: str = Field(default="", max_length=200)
    project_id: str | None = None


@dashboard_router.get("/v1/sessions")
async def list_sessions_route(
    user_id: CurrentUserId,
    project_id: str | None = None,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    return {"sessions": datastore.list_sessions(project_id=project_id, user_id=user_id)}


@dashboard_router.post("/v1/sessions")
async def create_session_route(
    body: SessionIn,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    return datastore.upsert_session(
        title=body.title, model=body.model, project_id=body.project_id, user_id=user_id
    )


@dashboard_router.get("/v1/sessions/{session_id}")
async def get_session_route(
    session_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    session = datastore.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    messages = datastore.list_messages(session_id)
    return {**session, "messages": messages}


@dashboard_router.get("/v1/sessions/{session_id}/usage")
async def session_usage_route(
    session_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    if datastore.get_session(session_id) is None:
        raise HTTPException(404, "session not found")
    return datastore.session_usage(session_id)


@dashboard_router.put("/v1/sessions/{session_id}")
async def update_session_route(
    session_id: str, body: SessionIn, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    return datastore.upsert_session(
        session_id=session_id,
        title=body.title,
        model=body.model,
        project_id=body.project_id,
    )


@dashboard_router.delete("/v1/sessions/{session_id}")
async def delete_session_route(
    session_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    datastore.delete_session(session_id)
    return {"ok": True}


# ============================================================ Auto-rename


_AUTO_RENAME_SYSTEM = (
    "Summarize this exchange as a 3-6 word title. "
    "Output the title only, no quotes, no punctuation."
)
_AUTO_RENAME_TIMEOUT_S = 10.0
_AUTO_RENAME_MAX_CHARS = 60


class AutoRenameIn(BaseModel):
    first_user_message: str = Field(default="", max_length=4000)
    first_assistant_message: str = Field(default="", max_length=4000)


def _parse_auto_rename_title(raw: str) -> str:
    """Strip whitespace, quotes, and punctuation; cap at 60 chars."""
    title = raw.strip().strip("\"'`").strip()
    # Remove trailing punctuation.
    title = re.sub(r"[.!?,;:]+$", "", title).strip()
    return title[:_AUTO_RENAME_MAX_CHARS]


async def _call_rename_llm(
    body: AutoRenameIn,
    settings: Settings,
    request: Request | None,
) -> str:
    """POST to /v1/messages and return the raw title text (or '' on failure)."""
    base_url = settings.public_base_url.rstrip("/")
    model = settings.model
    auth_token = settings.anthropic_auth_token

    messages = [
        {"role": "user", "content": body.first_user_message[:600]},
        {"role": "assistant", "content": body.first_assistant_message[:600]},
        {
            "role": "user",
            "content": "Now give a 3-6 word title for the exchange above.",
        },
    ]
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 40,
        "stream": False,
        "system": _AUTO_RENAME_SYSTEM,
        "messages": messages,
        "metadata": {"fcc_internal": "auto_rename"},
    }
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        async with httpx.AsyncClient(timeout=_AUTO_RENAME_TIMEOUT_S) as client:
            resp = await client.post(
                f"{base_url}/v1/messages",
                json=payload,
                headers=headers,
            )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        # Anthropic-format: {"content": [{"type": "text", "text": "..."}], ...}
        content = data.get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text") or ""
        return ""
    except Exception as exc:
        logger.debug("auto_rename: LLM call failed: {}", exc)
        return ""


@dashboard_router.post("/v1/sessions/{session_id}/auto-rename")
async def auto_rename_session(
    session_id: str,
    body: AutoRenameIn,
    request: Request,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Generate a short title for a session using the configured chat provider.

    Calls ``/v1/messages`` directly via httpx (no agent loop, no tools).
    Persists the result via ``datastore.upsert_session`` and returns ``{title}``.
    Returns an empty title string when the LLM call fails — the client should
    fall back to its derived title.
    """
    session = datastore.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    raw = await _call_rename_llm(body, settings, request)
    title = _parse_auto_rename_title(raw)

    if title:
        datastore.upsert_session(
            session_id=session_id,
            title=title,
            model=session.get("model") or "",
            project_id=session.get("project_id"),
        )
        logger.debug(
            "auto_rename: session={} title={!r}",
            session_id,
            title,
        )

    return {"title": title}


class MessageIn(BaseModel):
    role: str
    content: str


@dashboard_router.post("/v1/sessions/{session_id}/messages")
async def append_session_message(
    session_id: str, body: MessageIn, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    if datastore.get_session(session_id) is None:
        raise HTTPException(404, "session not found")
    return datastore.append_message(
        session_id=session_id, role=body.role, content=body.content
    )


@dashboard_router.delete("/v1/sessions/{session_id}/messages/{message_id}")
async def delete_session_message(
    session_id: str, message_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    if datastore.get_session(session_id) is None:
        raise HTTPException(404, "session not found")
    datastore.delete_message(message_id)
    return {"ok": True}


# ============================================================ Usage


_RANGE_TO_MS: dict[str, int] = {
    "1h": 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
    "all": 0,
}


def _since_ts(range_key: str) -> int:
    delta = _RANGE_TO_MS.get(range_key, _RANGE_TO_MS["7d"])
    if delta <= 0:
        return 0
    return int(time.time() * 1000) - delta


@dashboard_router.get("/v1/usage")
async def usage_route(
    range: str = "7d",
    project_id: str | None = None,
    kind: str | None = None,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    since_ts = _since_ts(range)
    summary = datastore.usage_summary(since_ts=since_ts)
    events = datastore.list_usage(
        since_ts=since_ts or None, kind=kind, project_id=project_id, limit=200
    )
    return {
        "range": range,
        "since_ts": since_ts,
        "summary": summary,
        "recent_events": events,
        "pricing": pricing_snapshot(),
    }


@dashboard_router.get("/v1/pricing")
async def pricing_route(_auth=Depends(require_api_key)) -> dict[str, Any]:
    return {
        "token_prices": known_token_prices(),
        "image_prices": known_image_prices(),
    }


# ============================================================ Insights


@dashboard_router.get("/v1/insights")
async def insights_route(
    user_id: CurrentUserId,
    range: str = "7d",
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return rule-based insights over audit_log + usage_events.

    Results are cached for 5 minutes per ``(range, user_id)`` pair.
    Supported ``range`` values: ``1h``, ``24h``, ``7d`` (default), ``30d``, ``all``.
    """
    from api.agent.insights import compute_insights

    since_ts = _since_ts(range)
    data = compute_insights(since_ts=since_ts, user_id=user_id)
    return {"range": range, "since_ts": since_ts, **data}


# ============================================================ Daily journal


class JournalIn(BaseModel):
    date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="ISO date (YYYY-MM-DD) for the journal entry.",
    )
    answers: dict[str, str] = Field(
        default_factory=dict,
        description="Map of question-id → answer text.",
    )


@dashboard_router.post("/v1/journal")
async def journal_post(
    body: JournalIn,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Write (or overwrite) the journal entry for ``date``.

    Accepts ``{date: YYYY-MM-DD, answers: {focus: ..., remember: ..., mood: ...}}``.
    Returns the vault-relative path of the written file.
    """
    import datetime

    from api.agent.journal import (
        DEFAULT_QUESTIONS,
        journal_entry_path,
        resolve_journal_vault,
        write_journal_entry,
    )

    try:
        entry_date = datetime.date.fromisoformat(body.date)
    except ValueError as exc:
        raise HTTPException(400, f"invalid date: {exc}") from exc

    try:
        written_path = write_journal_entry(
            entry_date,
            body.answers,
            questions=DEFAULT_QUESTIONS,
        )
    except OSError as exc:
        raise HTTPException(500, f"failed to write journal entry: {exc}") from exc

    vault_root, subpath = resolve_journal_vault()
    rel_path = journal_entry_path(entry_date, vault_root=vault_root, subpath=subpath)
    try:
        rel_str = str(rel_path.relative_to(vault_root))
    except ValueError:
        rel_str = str(written_path)

    return {
        "ok": True,
        "date": body.date,
        "path": str(written_path),
        "vault_relative": rel_str,
    }


@dashboard_router.get("/v1/journal")
async def journal_get(
    date: str = "",
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return the journal entry for ``date`` (defaults to today) as markdown.

    Returns ``{date, content, exists}`` — ``content`` is ``null`` when the
    entry has not been written yet.
    """
    import datetime

    from api.agent.journal import read_journal_entry

    if not date:
        entry_date = datetime.date.today()
    else:
        try:
            entry_date = datetime.date.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(400, f"invalid date: {exc}") from exc

    try:
        content = read_journal_entry(entry_date)
    except OSError as exc:
        raise HTTPException(500, f"failed to read journal entry: {exc}") from exc

    return {
        "date": entry_date.isoformat(),
        "content": content,
        "exists": content is not None,
    }


# ============================================================ File edits / audit


@dashboard_router.get("/v1/files")
async def file_edits_route(
    project_id: str | None = None,
    limit: int = 200,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    return {
        "edits": datastore.list_file_edits(
            project_id=project_id, limit=max(1, min(limit, 1000))
        )
    }


@dashboard_router.get("/v1/audit")
async def audit_route(
    user_id: CurrentUserId,
    category: str | None = None,
    limit: int = 200,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    return {
        "events": datastore.list_audit(
            category=category, limit=max(1, min(limit, 1000)), user_id=user_id
        )
    }


# ============================================================ Env vault


_ENV_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _looks_secret(key: str) -> bool:
    lk = key.lower()
    return any(hint in lk for hint in _SECRET_KEY_HINTS)


def _mask_value(value: str) -> str:
    """Mask a secret value while keeping the head/tail visible for sanity."""
    if not value:
        return ""
    stripped = value.strip().strip('"').strip("'")
    if len(stripped) <= 8:
        return "•" * len(stripped)
    return f"{stripped[:4]}{'•' * (len(stripped) - 8)}{stripped[-4:]}"


def _parse_env_text(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            continue
        key, raw = match.group(1), match.group(2)
        value = raw.strip().strip('"').strip("'")
        secret = _looks_secret(key)
        entries.append(
            {
                "key": key,
                "value": value,
                "masked": _mask_value(value) if secret else value,
                "secret": secret,
            }
        )
    return entries


@dashboard_router.get("/v1/env")
async def env_route(_auth=Depends(require_api_key)) -> dict[str, Any]:
    if not _ENV_FILE.is_file():
        return {"path": str(_ENV_FILE), "entries": [], "exists": False}
    text = _ENV_FILE.read_text(encoding="utf-8", errors="replace")
    return {
        "path": str(_ENV_FILE),
        "entries": _parse_env_text(text),
        "raw": text,
        "exists": True,
    }


class EnvSetIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(default="", max_length=20000)


@dashboard_router.put("/v1/env")
async def env_set(body: EnvSetIn, _auth=Depends(require_api_key)) -> dict[str, Any]:
    """Upsert a single env key in ``~/.config/free-claude-code/.env``.

    Existing comments and key order are preserved. The value is written
    verbatim without quoting; clients are expected to send the raw value.
    The proxy must be restarted for the change to take effect.
    """
    key = body.key.strip()
    if not re.match(r"^[A-Z][A-Z0-9_]*$", key):
        raise HTTPException(400, "invalid env key shape")
    _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = (
        _ENV_FILE.read_text(encoding="utf-8", errors="replace")
        if _ENV_FILE.is_file()
        else ""
    )
    lines = text.splitlines()
    written = False
    new_lines: list[str] = []
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for line in lines:
        if not written and pat.match(line):
            new_lines.append(f"{key}={body.value}")
            written = True
        else:
            new_lines.append(line)
    if not written:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"{key}={body.value}")
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    _ENV_FILE.write_text(new_text, encoding="utf-8")
    datastore.record_audit(
        category="env",
        event="upsert",
        detail=key,
        metadata={"secret": _looks_secret(key)},
    )
    logger.info("ENV: upsert key={} (secret={})", key, _looks_secret(key))
    # Invalidate cached Settings so the next request reflects the new env.
    # We also push the value into os.environ so live readers see the change
    # without waiting for a process restart.
    from config.settings import get_settings as _get_cached_settings

    os.environ[key] = body.value
    _get_cached_settings.cache_clear()
    return {"key": key, "ok": True}


@dashboard_router.delete("/v1/env/{key}")
async def env_delete(key: str, _auth=Depends(require_api_key)) -> dict[str, Any]:
    if not _ENV_FILE.is_file():
        return {"ok": True}
    text = _ENV_FILE.read_text(encoding="utf-8", errors="replace")
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    new_text = pat.sub("", text)
    new_text = re.sub(r"\n{3,}", "\n\n", new_text)
    _ENV_FILE.write_text(new_text, encoding="utf-8")
    datastore.record_audit(category="env", event="delete", detail=key)
    return {"ok": True}


# ============================================================ Preferences


@dashboard_router.get("/v1/preferences")
async def list_preferences(_auth=Depends(require_api_key)) -> dict[str, Any]:
    """Return all long-term user preferences from the prefs file."""
    from api.agent.extras.preferences import _load_prefs, _prefs_path

    path = _prefs_path()
    prefs = _load_prefs(path)
    entries = [{"key": k, "value": v} for k, v in prefs.items()]
    return {"preferences": entries, "path": str(path)}


@dashboard_router.delete("/v1/preferences/{key}")
async def delete_preference(key: str, _auth=Depends(require_api_key)) -> dict[str, Any]:
    """Delete a single preference by key."""
    from api.agent.extras.preferences import _load_prefs, _prefs_path, _save_prefs

    path = _prefs_path()
    prefs = _load_prefs(path)
    if key not in prefs:
        return {"ok": True, "found": False}
    del prefs[key]
    _save_prefs(path, prefs)
    datastore.record_audit(category="preferences", event="delete", detail=key)
    return {"ok": True, "found": True}


# ============================================================ Root files


def _repo_root() -> Path:
    """Best-effort repo root: directory containing AGENTS.md."""
    candidates = [
        Path.cwd(),
        Path.cwd() / ".claude" / "worktrees",
        Path(__file__).resolve().parents[1],
    ]
    for c in candidates:
        if (c / "AGENTS.md").is_file():
            return c
    return Path.cwd()


@dashboard_router.get("/v1/root-files")
async def root_files_list(_auth=Depends(require_api_key)) -> dict[str, Any]:
    root = _repo_root()
    files = []
    for name in _ROOT_FILES_ALLOWLIST:
        path = root / name
        if path.is_file():
            files.append(
                {
                    "name": name,
                    "size": path.stat().st_size,
                    "modified_at": int(path.stat().st_mtime * 1000),
                }
            )
    return {"root": str(root), "files": files}


@dashboard_router.get("/v1/root-files/{name}")
async def root_file_get(name: str, _auth=Depends(require_api_key)) -> dict[str, Any]:
    if name not in _ROOT_FILES_ALLOWLIST:
        raise HTTPException(404, "file not in allowlist")
    path = _repo_root() / name
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return {
        "name": name,
        "content": path.read_text(encoding="utf-8", errors="replace"),
        "size": path.stat().st_size,
    }


class RootFileIn(BaseModel):
    content: str = Field(default="", max_length=2_000_000)


@dashboard_router.put("/v1/root-files/{name}")
async def root_file_set(
    name: str, body: RootFileIn, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    if name not in _ROOT_FILES_ALLOWLIST:
        raise HTTPException(404, "file not in allowlist")
    path = _repo_root() / name
    before = path.stat().st_size if path.is_file() else 0
    path.write_text(body.content, encoding="utf-8")
    after = path.stat().st_size
    datastore.record_file_edit(
        op="write" if before > 0 else "create",
        path=str(path),
        bytes_delta=after - before,
        metadata={"source": "ui"},
    )
    return {"name": name, "size": after}


# ============================================================ Skills


_SKILL_DIRS: tuple[Path, ...] = (
    Path.home() / ".claude" / "skills",
    Path.home() / ".claude" / "plugins" / "skills",
    Path.home() / ".claude" / "plugins" / "marketplaces",
)


def _scan_skills() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in _SKILL_DIRS:
        if not base.is_dir():
            continue
        # Match SKILL.md anywhere two-to-four levels deep.
        for skill_md in base.rglob("SKILL.md"):
            try:
                rel = skill_md.relative_to(base)
            except ValueError:
                continue
            parts = rel.parts
            if not parts:
                continue
            name = parts[-2] if len(parts) >= 2 else parts[0]
            if name in seen:
                continue
            seen.add(name)
            try:
                head = skill_md.read_text(encoding="utf-8", errors="replace")[:4096]
            except OSError:
                continue
            description = ""
            for line in head.splitlines():
                if line.lower().startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("\"'")
                    break
            skills.append(
                {
                    "name": name,
                    "path": str(skill_md),
                    "description": description[:300],
                    "size": skill_md.stat().st_size,
                }
            )
    skills.sort(key=lambda s: s["name"])
    return skills


@dashboard_router.get("/v1/skills")
async def skills_route(_auth=Depends(require_api_key)) -> dict[str, Any]:
    return {"skills": _scan_skills(), "search_paths": [str(p) for p in _SKILL_DIRS]}


# ============================================================ Skill marketplace (new)

# Repo-root skills dir (two levels up from api/dashboard_routes.py)
_REPO_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"

# Catalog cache: (fetched_at_epoch, payload)
_catalog_cache: tuple[float, dict[str, Any]] | None = None
_CATALOG_CACHE_TTL_S = 24 * 60 * 60  # 24 hours
_CATALOG_FETCH_TIMEOUT_S = 10.0


def _local_skills_list() -> list[dict[str, Any]]:
    """Return installed skills from the repo-root ``skills/`` directory."""
    from api.agent.extras.skills import scan_skills

    skills = scan_skills(_REPO_SKILLS_ROOT)
    return [
        {
            "name": s.name,
            "slug": s.slug,
            "description": s.description,
            "entry": s.entry,
            "path": str(s.path),
        }
        for s in skills
    ]


@dashboard_router.get("/v1/skills/local")
async def skills_local_route(_auth=Depends(require_api_key)) -> dict[str, Any]:
    """Return skills installed in the repo-root ``skills/`` directory."""
    return {"skills": _local_skills_list()}


async def _fetch_catalog(catalog_url: str) -> dict[str, Any]:
    """Fetch and return the remote catalog JSON, or empty on failure."""
    global _catalog_cache

    now = time.time()
    if _catalog_cache is not None:
        fetched_at, payload = _catalog_cache
        if now - fetched_at < _CATALOG_CACHE_TTL_S:
            return payload

    try:
        async with httpx.AsyncClient(timeout=_CATALOG_FETCH_TIMEOUT_S) as client:
            resp = await client.get(catalog_url)
        if resp.status_code != 200:
            logger.warning(
                "SKILLS: catalog fetch returned HTTP {} from {}",
                resp.status_code,
                catalog_url,
            )
            return {"skills": []}
        payload = resp.json()
        if not isinstance(payload, dict):
            payload = {"skills": []}
        _catalog_cache = (now, payload)
        return payload
    except Exception as exc:
        logger.warning("SKILLS: catalog fetch failed ({}): {}", catalog_url, exc)
        return {"skills": []}


@dashboard_router.get("/v1/skills/catalog")
async def skills_catalog_route(
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return the remote skills catalog, cached 24 h on disk.

    Returns ``{skills: []}`` when ``SKILLS_CATALOG_URL`` is not configured
    or the remote is unreachable.
    """
    catalog_url = settings.skills_catalog_url
    if not catalog_url:
        return {"skills": [], "source": None}

    payload = await _fetch_catalog(catalog_url)
    payload["source"] = catalog_url
    return payload


def _validate_tar_path(member_name: str, slug: str) -> bool:
    """Return True if the tar member path stays within ``skills/{slug}/``."""
    # Normalise to forward slashes
    norm = member_name.replace("\\", "/").lstrip("/")
    # Must start with slug/ (or be slug itself)
    prefix = slug + "/"
    if norm == slug:
        return True
    if not norm.startswith(prefix):
        return False
    # Reject any traversal segments
    rest = norm[len(prefix) :]
    return all(part not in ("", ".", "..") for part in rest.split("/"))


@dashboard_router.post("/v1/skills/install/{slug}")
async def install_skill(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Download and extract a skill tarball from the catalog.

    The request body may supply ``{"url": "..."}`` to override the download
    URL; otherwise the catalog entry for ``slug`` is used.

    Security
    --------
    - ``slug`` must be a safe identifier (alphanumeric + hyphens/underscores).
    - Every tar member is validated to stay within ``skills/{slug}/``.
    - Path-traversal attempts cause a 400 error.
    """
    # Validate slug shape
    if not re.match(r"^[a-zA-Z0-9_-]+$", slug):
        raise HTTPException(400, f"invalid slug: {slug!r}")

    # Determine download URL
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    download_url: str | None = body.get("url") or None

    if not download_url:
        catalog_url = settings.skills_catalog_url
        if not catalog_url:
            raise HTTPException(
                400, "SKILLS_CATALOG_URL not configured and no 'url' provided in body"
            )
        catalog = await _fetch_catalog(catalog_url)
        entries = catalog.get("skills") or []
        entry = next((e for e in entries if e.get("slug") == slug), None)
        if entry is None:
            raise HTTPException(404, f"skill {slug!r} not found in catalog")
        download_url = entry.get("tarball_url") or entry.get("url")
        if not download_url:
            raise HTTPException(502, f"catalog entry for {slug!r} has no download URL")

    # Download the tarball
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(download_url)
        if resp.status_code != 200:
            raise HTTPException(
                502, f"download failed with HTTP {resp.status_code} from {download_url}"
            )
        tarball_bytes = resp.content
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"download error: {exc}") from exc

    # Extract into a temp dir, validate all member paths, then move to skills/
    dest_dir = _REPO_SKILLS_ROOT / slug
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            try:
                with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tf:
                    # Validate every member before extracting
                    for member in tf.getmembers():
                        if not _validate_tar_path(member.name, slug):
                            raise HTTPException(
                                400,
                                f"tar member {member.name!r} escapes skill directory",
                            )
                    tf.extractall(tmp_path)
            except tarfile.TarError as exc:
                raise HTTPException(400, f"invalid tarball: {exc}") from exc

            # Move extracted content to skills/ root
            extracted_skill = tmp_path / slug
            if not extracted_skill.is_dir():
                # Tarball may have been flat (no top-level slug/ folder)
                extracted_skill = tmp_path

            import shutil

            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(str(extracted_skill), str(dest_dir))

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"install failed: {exc}") from exc

    datastore.record_audit(
        category="skills",
        event="install",
        detail=slug,
        metadata={"url": download_url},
    )
    logger.info("SKILLS: installed slug={} from {}", slug, download_url)
    return {"ok": True, "slug": slug, "path": str(dest_dir)}


@dashboard_router.delete("/v1/skills/local/{slug}")
async def uninstall_skill(
    slug: str,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Remove an installed skill from ``skills/{slug}``."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", slug):
        raise HTTPException(400, f"invalid slug: {slug!r}")

    import shutil

    dest_dir = _REPO_SKILLS_ROOT / slug
    if not dest_dir.is_dir():
        raise HTTPException(404, f"skill {slug!r} not installed")

    # Verify the target resolves inside _REPO_SKILLS_ROOT (extra safety)
    try:
        dest_dir.resolve().relative_to(_REPO_SKILLS_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(400, "path traversal detected") from exc

    shutil.rmtree(dest_dir)
    datastore.record_audit(category="skills", event="uninstall", detail=slug)
    logger.info("SKILLS: uninstalled slug={}", slug)
    return {"ok": True, "slug": slug}


# ============================================================ Models discovery


@dashboard_router.get("/v1/models/upstream")
async def upstream_models_route(
    request: Request,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return the model list discovered at upstream providers, grouped per
    provider, so the UI can show real switchable models (e.g. all DeepSeek
    checkpoints) rather than a hardcoded set."""
    registry = getattr(request.app.state, "provider_registry", None)
    if not isinstance(registry, ProviderRegistry):
        return {"providers": []}
    cached = registry.cached_model_ids()
    grouped: list[dict[str, Any]] = []
    for provider_id, model_ids in sorted(cached.items()):
        grouped.append(
            {
                "provider": provider_id,
                "models": sorted(model_ids),
            }
        )
    return {
        "providers": grouped,
        "configured": [
            {
                "ref": ref.model_ref,
                "provider": ref.provider_id,
                "model": ref.model_id,
                "sources": list(ref.sources),
            }
            for ref in settings.configured_chat_model_refs()
        ],
    }


# ============================================================ Settings snapshot


_PROCESS_START = time.time()


@dashboard_router.get("/v1/settings")
async def settings_route(
    settings: Settings = Depends(get_settings), _auth=Depends(require_api_key)
) -> dict[str, Any]:
    """Non-sensitive settings snapshot for the dashboard."""
    return {
        "model": settings.model,
        "model_opus": settings.model_opus,
        "model_sonnet": settings.model_sonnet,
        "model_haiku": settings.model_haiku,
        "model_subagent": settings.model_subagent,
        "model_fallback_chain": settings.model_fallback_chain,
        "thinking": {
            "default_enabled": settings.enable_model_thinking,
            "budget_max": settings.thinking_budget_max,
        },
        "deepseek_image_fallback": {
            "provider": settings.deepseek_image_fallback_provider or None,
            "model": settings.deepseek_image_fallback_model or None,
        },
        "image_gen": {
            "provider": settings.image_gen_provider or None,
            "model": settings.image_gen_model or None,
        },
        "agent_mode": {
            "enabled": settings.agent_mode_enabled,
            "max_turns": settings.agent_max_turns,
            "workspace": settings.agent_default_workspace or None,
            "bash_denylist": settings.agent_bash_denylist or None,
            "bash_extra_env": settings.agent_bash_extra_env or None,
            "tool_call_limit_per_min": settings.agent_tool_call_limit_per_minute,
            "global_tool_call_limit_per_min": settings.agent_global_tool_call_limit_per_minute,
        },
        "host": settings.host,
        "port": settings.port,
        "anthropic_auth_token_set": bool(settings.anthropic_auth_token),
        "env_file": str(_ENV_FILE),
        "process": {
            "pid": os.getpid(),
            "uptime_s": int(time.time() - _PROCESS_START),
        },
    }


@dashboard_router.get("/v1/fs/quick-paths")
async def fs_quick_paths(_auth=Depends(require_api_key)) -> dict[str, Any]:
    """Return common shortcut directories for the folder picker.

    Helps the project workspace dropdown jump to ``~/Desktop``, ``~/Documents``,
    ``~/Downloads``, the user's home, and the current proxy CWD without typing.
    Only existing directories are returned.
    """
    home = Path.home()
    candidates = [
        ("Home", home),
        ("Desktop", home / "Desktop"),
        ("Documents", home / "Documents"),
        ("Downloads", home / "Downloads"),
        ("Projects", home / "Projects"),
        ("Code", home / "Code"),
        ("Sites", home / "Sites"),
        ("Dev", home / "Dev"),
        ("CWD", Path(os.getcwd())),
    ]
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for label, path in candidates:
        if not path.is_dir():
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "path": key})
    return {"paths": out}


@dashboard_router.get("/v1/fs/browse")
async def fs_browse(
    path: str = "",
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """List sub-directories of ``path`` for the workspace picker.

    Returns the canonical absolute path + a list of child directories. ``path``
    may be empty (defaults to ``$HOME``) or ``~`` for the home dir. Hidden
    folders (starting with ``.``) are filtered out by default. Used by the
    Project editor's workspace dropdown.
    """
    try:
        if not path:
            base = Path.home()
        else:
            base = Path(path).expanduser().resolve(strict=False)
    except OSError as exc:
        raise HTTPException(400, f"invalid path: {exc}") from exc
    if not base.exists() or not base.is_dir():
        raise HTTPException(404, f"not a directory: {base}")

    children: list[dict[str, Any]] = []
    try:
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):
                continue
            if not entry.is_dir():
                continue
            children.append({"name": entry.name, "path": str(entry)})
    except PermissionError as exc:
        raise HTTPException(403, f"permission denied: {exc}") from exc

    return {
        "path": str(base),
        "parent": str(base.parent) if base != base.parent else None,
        "children": children,
    }


@dashboard_router.get("/v1/mcp")
async def list_mcp(_auth=Depends(require_api_key)) -> dict[str, Any]:
    """Return live status of every MCP server the proxy knows about."""
    from api.agent import mcp

    return {
        "discovered": mcp.discover_claude_mcp_servers(),
        "running": mcp.list_clients(),
    }


@dashboard_router.post("/v1/mcp/{name}/restart")
async def restart_mcp(name: str, _auth=Depends(require_api_key)) -> dict[str, Any]:
    """Stop + start the named MCP server (re-runs tool discovery)."""
    from api.agent import mcp

    desktop = {s["name"]: s for s in mcp.discover_claude_mcp_servers()}
    if name not in desktop:
        raise HTTPException(404, f"unknown MCP server: {name}")
    return await mcp.register_mcp_server(**desktop[name])


@dashboard_router.post("/v1/mcp/{name}/stop")
async def stop_mcp(name: str, _auth=Depends(require_api_key)) -> dict[str, Any]:
    from api.agent import mcp

    if name not in mcp._CLIENTS:
        raise HTTPException(404, "server not running")
    await mcp._CLIENTS[name].stop()
    return {"ok": True, "name": name}


@dashboard_router.get("/v1/capabilities")
async def capabilities_route(
    settings: Settings = Depends(get_settings), _auth=Depends(require_api_key)
) -> dict[str, Any]:
    """Return the capability snapshot grouped by status for the UI."""
    from .capabilities import scan as scan_capabilities
    from .capabilities import to_payload as capabilities_to_payload

    return capabilities_to_payload(scan_capabilities(settings))


# ============================================================ Agent jobs (background)


class AgentJobIn(BaseModel):
    """Body for starting a server-managed agent job.

    Decoupled from the request connection: closing the tab does NOT cancel
    the job. UI subscribes to events via SSE and can reconnect with
    ``Last-Event-ID`` to resume.
    """

    model: str = Field(min_length=1, max_length=200)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    system: Any = None
    max_tokens: int = Field(default=4096, ge=1, le=1_000_000)
    project_id: str | None = None
    metadata: dict[str, Any] | None = None


@dashboard_router.post("/v1/sessions/{session_id}/jobs")
async def start_session_job(
    session_id: str,
    body: AgentJobIn,
    request: Request,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Kick off a background agent run for ``session_id``. Returns immediately."""
    from api.agent.jobs import start_job
    from api.dependencies import resolve_provider

    settings = request.app.state.__dict__.get("_settings_override") or get_settings()
    # Resolve provider once at start; jobs.py calls the getter when running.
    provider_id_route_resolver = request.app  # captured for provider_getter below

    def _provider_getter() -> Any:
        return resolve_provider(
            _provider_id_for(body.model),
            app=provider_id_route_resolver,
            settings=settings,
        )

    metadata = dict(body.metadata or {})
    metadata.setdefault("session_id", session_id)
    if body.project_id:
        metadata.setdefault("project_id", body.project_id)

    job = start_job(
        session_id=session_id,
        project_id=body.project_id,
        model=body.model,
        messages=body.messages,
        system=body.system,
        max_tokens=body.max_tokens,
        provider_getter=_provider_getter,
        metadata=metadata,
        user_id=user_id,
    )
    return job


def _provider_id_for(model: str) -> str:
    """Extract the provider prefix from a gateway model ref like ``deepseek/foo``."""
    if "/" in model:
        return model.split("/", 1)[0]
    return model


@dashboard_router.get("/v1/jobs/{job_id}")
async def get_job_status(job_id: str, _auth=Depends(require_api_key)) -> dict[str, Any]:
    job = datastore.get_agent_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@dashboard_router.post("/v1/jobs/{job_id}/cancel")
async def cancel_job_route(
    job_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    from api.agent.jobs import cancel_job

    ok = cancel_job(job_id)
    return {"job_id": job_id, "cancel_signaled": ok}


@dashboard_router.get("/v1/sessions/{session_id}/jobs")
async def list_session_jobs(
    session_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    return {"jobs": datastore.list_agent_jobs(session_id=session_id)}


@dashboard_router.get("/v1/jobs/{job_id}/events")
async def stream_job_events(
    job_id: str, request: Request, _auth=Depends(require_api_key)
):
    """SSE stream: replay stored events from after Last-Event-ID, then tail live."""
    from fastapi.responses import StreamingResponse

    from api.agent.jobs import event_stream

    job = datastore.get_agent_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")

    after_seq = -1
    last_event_id = request.headers.get("last-event-id") or request.query_params.get(
        "last_event_id"
    )
    if last_event_id is not None:
        try:
            after_seq = int(last_event_id)
        except ValueError:
            after_seq = -1

    return StreamingResponse(
        event_stream(job_id, after_seq=after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@dashboard_router.get("/v1/jobs/{job_id}/output")
async def get_job_output_route(
    job_id: str, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    """Return extracted text + tool-call output for a job.

    Parses stored Anthropic-SSE chunks to produce human-readable content
    blocks suitable for display in the routines history panel.
    """
    job = datastore.get_agent_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")

    events = datastore.list_agent_job_events(job_id, limit=10_000)

    text_blocks: list[str] = []
    tool_blocks: list[dict[str, Any]] = []

    # Track state per content-block index across events
    text_by_index: dict[int, str] = {}
    tool_name_by_index: dict[int, str] = {}
    tool_input_by_index: dict[int, str] = {}
    stop_order: list[tuple[int, str]] = []  # (index, block_type)

    for ev in events:
        if ev["event_type"] != "sse" or not ev["data"]:
            continue

        # Each stored chunk is one Anthropic-SSE frame
        for raw_block in ev["data"].split("\n\n"):
            raw_block = raw_block.strip()
            if not raw_block:
                continue
            data_line = None
            for line in raw_block.split("\n"):
                if line.startswith("data:"):
                    data_line = line[len("data:") :].strip()
                    break
            if not data_line:
                continue
            try:
                msg = json.loads(data_line)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")
            idx = msg.get("index", 0)

            if msg_type == "content_block_start":
                cb = msg.get("content_block", {})
                if cb.get("type") == "text":
                    text_by_index[idx] = ""
                    stop_order.append((idx, "text"))
                elif cb.get("type") == "tool_use":
                    tool_name_by_index[idx] = cb.get("name", "?")
                    tool_input_by_index[idx] = ""
                    stop_order.append((idx, "tool_use"))

            elif msg_type == "content_block_delta":
                delta = msg.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_by_index[idx] = text_by_index.get(idx, "") + delta.get(
                        "text", ""
                    )
                elif delta.get("type") == "input_json_delta":
                    tool_input_by_index[idx] = tool_input_by_index.get(
                        idx, ""
                    ) + delta.get("partial_json", "")

            elif msg_type == "content_block_stop":
                if text_by_index.get(idx):
                    text_blocks.append(text_by_index.pop(idx))
                elif idx in tool_name_by_index:
                    tool_blocks.append(
                        {
                            "name": tool_name_by_index.pop(idx, "?"),
                            "input": tool_input_by_index.pop(idx, ""),
                        }
                    )

    # Preserve creation order
    ordered_text: list[str] = []
    ordered_tools: list[dict[str, Any]] = []
    seen: set[int] = set()
    for idx, bt in stop_order:
        if idx in seen:
            continue
        seen.add(idx)
        if bt == "text" and idx in text_by_index:
            ordered_text.append(text_by_index[idx])
        elif bt == "tool_use":
            ordered_tools.append(
                {
                    "name": tool_name_by_index.get(idx, "?"),
                    "input": tool_input_by_index.get(idx, ""),
                }
            )

    return {
        "job_id": job_id,
        "status": job.get("status", "unknown"),
        "model": job.get("model", ""),
        "text_blocks": ordered_text if ordered_text else text_blocks,
        "tool_blocks": ordered_tools if ordered_tools else tool_blocks,
    }


# ============================================================ Routines

_VALID_TRIGGER_TYPES = frozenset({"cron", "manual", "webhook"})


class RoutineIn(BaseModel):
    """Body for creating or updating a routine."""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    trigger_type: str = Field(default="cron", max_length=20)
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(...)
    enabled: bool = Field(default=True)
    project_id: str | None = None


class RoutinePatchIn(BaseModel):
    """Body for partially updating a routine (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    trigger_type: str | None = Field(default=None, max_length=20)
    trigger_config: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    enabled: bool | None = None


def _validate_routine_payload(payload: dict[str, Any]) -> None:
    """Validate that payload contains model + messages (required by jobs API)."""
    if not isinstance(payload.get("model"), str) or not payload["model"].strip():
        raise HTTPException(
            400, "payload.model is required and must be a non-empty string"
        )
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(400, "payload.messages is required and must be a list")


def _validate_trigger_type(trigger_type: str) -> None:
    if trigger_type not in _VALID_TRIGGER_TYPES:
        raise HTTPException(
            400,
            f"trigger_type must be one of: {', '.join(sorted(_VALID_TRIGGER_TYPES))}",
        )


def _compute_next_run_for_routine(
    trigger_type: str, trigger_config: dict[str, Any]
) -> int | None:
    """Compute next_run_ms for a new/updated cron routine."""
    if trigger_type != "cron":
        return None
    expression = trigger_config.get("expression", "")
    tz = trigger_config.get("tz", "UTC") or "UTC"
    if not expression:
        return None
    from api.agent.routines import _compute_next_run_ms

    return _compute_next_run_ms(expression, tz, int(time.time() * 1000))


@dashboard_router.get("/v1/routines/preview-cron")
async def preview_cron(
    expr: str = "",
    tz: str = "UTC",
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return next 5 fire times for a cron expression.

    Query params: ``expr`` (cron expression), ``tz`` (IANA timezone, default UTC).
    Returns ``{fires: [epoch_ms, ...]}`` — empty list on invalid expression.
    """
    if not expr:
        raise HTTPException(400, "expr is required")
    try:
        from croniter import CroniterBadCronError, croniter

        if not croniter.is_valid(expr):
            raise HTTPException(400, f"invalid cron expression: {expr!r}")

        from api.agent.routines import _compute_next_run_ms

        fires: list[int] = []
        cursor_ms = int(time.time() * 1000)
        for _ in range(5):
            next_ms = _compute_next_run_ms(expr, tz, cursor_ms)
            fires.append(next_ms)
            cursor_ms = next_ms
        return {"fires": fires}
    except HTTPException:
        raise
    except CroniterBadCronError as exc:
        raise HTTPException(400, f"invalid cron expression: {exc}") from exc
    except Exception as exc:
        raise HTTPException(400, f"cron preview failed: {exc}") from exc


@dashboard_router.get("/v1/routines")
async def list_routines_route(
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return all routines for the current user."""
    return {"routines": datastore.list_routines(user_id=user_id)}


@dashboard_router.post("/v1/routines")
async def create_routine_route(
    body: RoutineIn,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Create a new routine. ``payload`` must contain ``model`` and ``messages``."""
    _validate_trigger_type(body.trigger_type)
    _validate_routine_payload(body.payload)
    next_run_ms = _compute_next_run_for_routine(body.trigger_type, body.trigger_config)
    routine = datastore.create_routine(
        name=body.name,
        description=body.description,
        trigger_type=body.trigger_type,
        trigger_config=body.trigger_config,
        payload=body.payload,
        enabled=body.enabled,
        project_id=body.project_id,
        user_id=user_id,
        next_run_ms=next_run_ms,
    )
    return routine


@dashboard_router.patch("/v1/routines/{routine_id}")
async def patch_routine_route(
    routine_id: str,
    body: RoutinePatchIn,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Partially update a routine. Scope-checks that the routine belongs to the caller."""
    routine = datastore.get_routine(routine_id)
    if routine is None:
        raise HTTPException(404, "routine not found")
    if routine.get("user_id") != user_id:
        raise HTTPException(403, "not your routine")

    if body.trigger_type is not None:
        _validate_trigger_type(body.trigger_type)
    if body.payload is not None:
        _validate_routine_payload(body.payload)

    # Recompute next_run_ms if trigger changed.
    new_trigger_type = (
        body.trigger_type
        if body.trigger_type is not None
        else routine.get("trigger_type", "cron")
    )
    new_trigger_config: dict[str, Any] | None = None
    if body.trigger_config is not None or body.trigger_type is not None:
        import json as _json

        existing_tc: dict[str, Any] = _json.loads(routine.get("trigger_config") or "{}")
        new_trigger_config = (
            body.trigger_config if body.trigger_config is not None else existing_tc
        )
        next_run_ms = _compute_next_run_for_routine(
            new_trigger_type, new_trigger_config
        )
    else:
        next_run_ms = None

    updated = datastore.update_routine(
        routine_id,
        name=body.name,
        description=body.description,
        trigger_type=body.trigger_type,
        trigger_config=new_trigger_config,
        payload=body.payload,
        enabled=body.enabled,
        next_run_ms=next_run_ms,
    )
    if updated is None:
        raise HTTPException(404, "routine not found")
    return updated


@dashboard_router.delete("/v1/routines/{routine_id}")
async def delete_routine_route(
    routine_id: str,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Delete a routine owned by the current user."""
    routine = datastore.get_routine(routine_id)
    if routine is None:
        raise HTTPException(404, "routine not found")
    if routine.get("user_id") != user_id:
        raise HTTPException(403, "not your routine")
    datastore.delete_routine(routine_id)
    return {"ok": True}


@dashboard_router.post("/v1/routines/{routine_id}/run")
async def run_routine_now_route(
    routine_id: str,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Manually fire a routine immediately (UI "Run now" button).

    Unlike the webhook trigger, this does **not** require an ``X-Routine-Secret``
    header — it is already protected by the standard API key auth.  Returns the
    created job row so the UI can deep-link to it.
    """
    routine = datastore.get_routine(routine_id)
    if routine is None:
        raise HTTPException(404, "routine not found")
    if routine.get("user_id") != user_id:
        raise HTTPException(403, "not your routine")

    from api.agent.routines import trigger_routine_now

    try:
        job = await trigger_routine_now(routine_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"trigger failed: {exc}") from exc

    return {"ok": True, "job": job}


@dashboard_router.get("/v1/routines/{routine_id}/runs")
async def list_routine_runs_route(
    routine_id: str,
    user_id: CurrentUserId,
    limit: int = 25,
    offset: int = 0,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return paginated run history for a routine."""
    routine = datastore.get_routine(routine_id)
    if routine is None:
        raise HTTPException(404, "routine not found")
    if routine.get("user_id") != user_id:
        raise HTTPException(403, "not your routine")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    runs = datastore.list_routine_runs(routine_id, limit=limit, offset=offset)
    total = datastore.count_routine_runs(routine_id)
    return {"runs": runs, "total": total, "limit": limit, "offset": offset}


@dashboard_router.post("/v1/routines/{routine_id}/trigger")
async def trigger_routine(
    routine_id: str,
    request: Request,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Webhook trigger: fire a routine immediately.

    The caller must supply the ``X-Routine-Secret`` header with the value
    stored in ``trigger_config.secret``.  Comparison is constant-time to
    prevent timing attacks.  Returns the created job row on success.
    """
    import secrets as _secrets

    routine = datastore.get_routine(routine_id)
    if routine is None:
        raise HTTPException(404, "routine not found")

    trigger_config: dict[str, Any] = json.loads(routine.get("trigger_config") or "{}")
    expected_secret: str = trigger_config.get("secret") or ""
    if expected_secret:
        supplied = request.headers.get("x-routine-secret") or ""
        if not _secrets.compare_digest(
            supplied.encode("utf-8"), expected_secret.encode("utf-8")
        ):
            raise HTTPException(403, "invalid routine secret")

    from api.agent.routines import trigger_routine_now

    try:
        job = await trigger_routine_now(routine_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"trigger failed: {exc}") from exc

    return {"ok": True, "job": job}


# ============================================================ Routine templates

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "routines"
_templates_cache: dict[str, dict[str, Any]] | None = None


def _load_templates() -> dict[str, dict[str, Any]]:
    """Load and cache all YAML templates from the routines/ directory."""
    global _templates_cache
    if _templates_cache is not None:
        return _templates_cache
    cache: dict[str, dict[str, Any]] = {}
    if _TEMPLATES_DIR.is_dir():
        for path in sorted(_TEMPLATES_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "slug" in data:
                    cache[data["slug"]] = data
            except Exception:
                logger.warning("Failed to parse routine template {}", path)
    _templates_cache = cache
    return cache


def _substitute_vars(text: str, inputs: dict[str, str]) -> str:
    """Replace ``{{var}}`` placeholders with values from *inputs*."""
    for key, value in inputs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _materialize_template(
    template: dict[str, Any], inputs: dict[str, str]
) -> dict[str, Any]:
    """Deep-substitute ``{{var}}`` in all string values of the template payload."""
    raw = json.dumps(template.get("payload", {}))
    substituted = _substitute_vars(raw, inputs)
    return json.loads(substituted)


class FromTemplateIn(BaseModel):
    """Body for POST /v1/routines/from-template."""

    slug: str
    inputs: dict[str, str] = Field(default_factory=dict)


@dashboard_router.get("/v1/routines/templates")
async def list_routine_templates(
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return all available routine templates parsed from ``routines/*.yaml``."""
    templates = list(_load_templates().values())
    return {"templates": templates}


@dashboard_router.post("/v1/routines/from-template")
async def create_routine_from_template(
    body: FromTemplateIn,
    user_id: CurrentUserId,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Materialize a template by substituting ``{{var}}`` placeholders, then create the routine."""
    templates = _load_templates()
    template = templates.get(body.slug)
    if template is None:
        raise HTTPException(404, f"template '{body.slug}' not found")

    # Validate required inputs.
    # An input is required when its ``default`` key is absent or explicitly None.
    # An empty-string default is valid — the placeholder resolves to "".
    _SENTINEL = object()
    missing: list[str] = []
    merged_inputs: dict[str, str] = {}
    for inp in template.get("inputs", []):
        name = inp.get("name", "")
        default = inp.get("default", _SENTINEL)
        value = body.inputs.get(name, "")
        if not value:
            if default is _SENTINEL or default is None:
                missing.append(name)
                continue
            value = str(default)
        merged_inputs[name] = value

    if missing:
        raise HTTPException(422, f"missing required inputs: {', '.join(missing)}")

    payload = _materialize_template(template, merged_inputs)
    _validate_routine_payload(payload)

    trigger = template.get("trigger", {})
    trigger_type = trigger.get("type", "cron")
    trigger_config: dict[str, Any] = {}
    if trigger_type == "cron":
        trigger_config["expression"] = trigger.get("expression", "0 9 * * *")
        if "tz" in trigger:
            trigger_config["tz"] = trigger["tz"]

    next_run_ms = _compute_next_run_for_routine(trigger_type, trigger_config)

    routine = datastore.create_routine(
        name=template.get("name", body.slug),
        description=template.get("description", ""),
        trigger_type=trigger_type,
        trigger_config=trigger_config,
        payload=payload,
        enabled=True,
        user_id=user_id,
        next_run_ms=next_run_ms,
    )
    return routine


# ============================================================ Provider test


_PROVIDER_TEST_TIMEOUT_S = 5.0
_PROVIDER_TEST_MESSAGES = [{"role": "user", "content": "hi"}]


@dashboard_router.post("/v1/providers/{provider_id}/test")
async def test_provider(
    provider_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Send a minimal 1-token completion to ``provider_id`` and report timing.

    Returns ``{ok, duration_ms, echo_model, error}`` — never HTTP 500.
    Wraps every exception so the UI always gets a JSON payload.
    """
    from api.dependencies import resolve_provider
    from providers.exceptions import UnknownProviderTypeError

    t0 = time.monotonic()

    try:
        provider = resolve_provider(provider_id, app=request.app, settings=settings)
    except UnknownProviderTypeError:
        return {
            "ok": False,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "echo_model": None,
            "error": f"unknown provider: {provider_id}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "echo_model": None,
            "error": str(exc),
        }

    # Build a minimal Anthropic-shaped request object the provider can accept.
    class _MinimalRequest:
        messages = _PROVIDER_TEST_MESSAGES
        max_tokens = 16
        system = None
        tools = None
        temperature = None
        top_p = None
        top_k = None
        metadata = None
        stop_sequences = None
        model = ""
        stream = True
        thinking = None
        tool_choice = None

    try:
        echo_model: str | None = None
        chunks: list[str] = []

        async def _run() -> None:
            nonlocal echo_model
            async for chunk in provider.stream_response(
                _MinimalRequest(), request_id="provider-test"
            ):
                chunks.append(chunk)
                # Extract the echoed model from the first message_start event.
                if echo_model is None and '"message_start"' in chunk:
                    import json as _json

                    for line in chunk.splitlines():
                        if line.startswith("data:"):
                            try:
                                evt = _json.loads(line[5:])
                                echo_model = evt.get("message", {}).get("model") or None
                            except Exception:
                                pass

        await asyncio.wait_for(_run(), timeout=_PROVIDER_TEST_TIMEOUT_S)
        duration_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ok": True,
            "duration_ms": duration_ms,
            "echo_model": echo_model,
            "error": None,
        }

    except TimeoutError:
        return {
            "ok": False,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "echo_model": None,
            "error": "timed out after 5 s",
        }
    except Exception as exc:
        return {
            "ok": False,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "echo_model": None,
            "error": str(exc),
        }


# ============================================================ File preview


_PREVIEW_SIZE_LIMIT = 1 * 1024 * 1024  # 1 MiB

# Map file extensions to Prism language identifiers.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "markup",
    ".htm": "markup",
    ".xml": "markup",
    ".svg": "markup",
    ".css": "css",
    ".sql": "sql",
    ".rs": "rust",
    ".go": "go",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".md": "markdown",
    ".markdown": "markdown",
}


def _lang_for_path(path: Path) -> str:
    """Return a Prism language id for a file path, or 'plaintext'."""
    return _EXT_TO_LANG.get(path.suffix.lower(), "plaintext")


@dashboard_router.get("/v1/preview/file")
async def preview_file(
    path: str,
    session_id: str = "",
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Return a file's contents for the preview side panel.

    Path is resolved through the configured workspace so path traversal
    is blocked.  Files larger than 1 MiB are refused with HTTP 413.
    """
    from core.tools.workspace import Workspace, WorkspaceViolation

    if not path:
        raise HTTPException(400, "path is required")

    workspace_root = settings.agent_default_workspace or os.getcwd()
    workspace = Workspace.create(workspace_root, allow_outside_root=False)

    try:
        resolved = workspace.resolve(path)
    except WorkspaceViolation as exc:
        logger.info("PREVIEW: path traversal blocked path={!r} reason={}", path, exc)
        raise HTTPException(403, f"path outside workspace: {exc}") from exc

    if not resolved.is_file():
        raise HTTPException(404, "file not found")

    size = resolved.stat().st_size
    if size > _PREVIEW_SIZE_LIMIT:
        raise HTTPException(
            413,
            detail={
                "message": "file too large for preview",
                "size": size,
                "limit": _PREVIEW_SIZE_LIMIT,
                "truncated": True,
            },
        )

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(500, f"cannot read file: {exc}") from exc

    language = _lang_for_path(resolved)
    logger.debug(
        "PREVIEW: path={} size={} language={} session_id={}",
        path,
        size,
        language,
        session_id,
    )
    return {
        "content": content,
        "language": language,
        "size": size,
        "truncated": False,
        "path": str(resolved),
    }


# ============================================================ Memory / RAG index

_rescan_in_flight = False


@dashboard_router.get("/v1/index/status")
async def index_status(
    request: Request, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    """Return current RAG index metrics."""
    from api.agent.indexer import index_status as _index_status

    status = _index_status()
    status["rescan_in_flight"] = _rescan_in_flight
    return status


@dashboard_router.post("/v1/index/rescan")
async def index_rescan(
    request: Request, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    """Trigger a full re-scan of all configured index paths."""
    global _rescan_in_flight

    indexer = getattr(request.app.state, "indexer", None)
    paths: list[str] = getattr(request.app.state, "index_paths", [])

    if indexer is None or not paths:
        return {"ok": False, "reason": "Indexer not running — set MEMORY_INDEX_PATHS"}

    if _rescan_in_flight:
        return {"ok": False, "reason": "Rescan already in flight"}

    async def _run() -> None:
        global _rescan_in_flight
        _rescan_in_flight = True
        try:
            await indexer.rescan(paths)
        finally:
            _rescan_in_flight = False

    asyncio.create_task(_run())
    return {"ok": True}


# ============================================================ Voice mode


_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
_ELEVENLABS_TIMEOUT_S = 30.0
_WHISPER_CPP_TIMEOUT_S = 60.0

# NIM ASR base URL
_NIM_ASR_BASE_URL = "https://integrate.api.nvidia.com/v1/audio/transcriptions"
_NIM_ASR_TIMEOUT_S = 60.0


async def _transcribe_via_whisper_cpp(
    audio_bytes: bytes,
    binary: str,
    model: str,
) -> dict[str, Any]:
    """Run ``whisper.cpp`` in a subprocess and return ``{text, duration_ms}``.

    The audio bytes are written to a temp file; whisper.cpp reads it and
    emits the transcript on stdout.  We pass ``--output-json`` so the output
    is machine-readable.
    """
    t0 = time.monotonic()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    binary,
                    "-m",
                    model,
                    "-f",
                    tmp_path,
                    "--output-json",
                    "--no-timestamps",
                ],
                capture_output=True,
                text=True,
                timeout=_WHISPER_CPP_TIMEOUT_S,
            ),
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        if result.returncode != 0:
            logger.warning(
                "VOICE: whisper.cpp exited {} stderr={}",
                result.returncode,
                result.stderr[:200],
            )
            raise HTTPException(500, "whisper.cpp transcription failed")

        # whisper.cpp --output-json writes a JSON file next to the input.
        # When it also echoes JSON to stdout, parse from there first.
        import json as _json

        stdout = result.stdout.strip()
        text = ""
        if stdout:
            try:
                data = _json.loads(stdout)
                # JSON format: {"transcription": [{"text": "..."}]}
                segs = data.get("transcription") or []
                text = " ".join(s.get("text", "") for s in segs).strip()
            except _json.JSONDecodeError:
                # Fallback: plain text output
                text = stdout.strip()

        # Also check for a sidecar .json file
        if not text:
            sidecar = Path(tmp_path).with_suffix(".wav.json")
            if sidecar.is_file():
                try:
                    data = _json.loads(sidecar.read_text())
                    segs = data.get("transcription") or []
                    text = " ".join(s.get("text", "") for s in segs).strip()
                except Exception:
                    pass
                finally:
                    sidecar.unlink(missing_ok=True)

        return {"text": text, "duration_ms": duration_ms}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def _transcribe_via_nim(
    audio_bytes: bytes,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    """POST audio to NVIDIA NIM ASR endpoint and return ``{text, duration_ms}``."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_NIM_ASR_TIMEOUT_S) as client:
            resp = await client.post(
                _NIM_ASR_BASE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.wav", audio_bytes, "audio/wav")},
                data={"model": model},
            )
        duration_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            logger.warning("VOICE: NIM ASR returned HTTP {}", resp.status_code)
            raise HTTPException(502, "NIM transcription endpoint error")
        data = resp.json()
        text = data.get("text") or ""
        return {"text": text, "duration_ms": duration_ms}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"NIM transcription failed: {exc}") from exc


@dashboard_router.post("/v1/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    """Transcribe an uploaded audio blob.

    Accepts any audio format supported by the configured backend (typically
    webm/opus from the browser MediaRecorder API).

    Routing:
    - ``WHISPER_BINARY`` set → whisper.cpp subprocess
    - ``WHISPER_DEVICE=nvidia_nim`` → NVIDIA NIM ASR endpoint
    - Otherwise → HTTP 503 (voice mode not configured)

    Returns ``{text: str, duration_ms: int}``.
    """
    if not settings.voice_note_enabled:
        raise HTTPException(503, "voice mode is disabled (VOICE_NOTE_ENABLED=false)")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "empty audio upload")

    whisper_binary = settings.whisper_binary.strip()
    if whisper_binary:
        return await _transcribe_via_whisper_cpp(
            audio_bytes,
            binary=whisper_binary,
            model=settings.whisper_model,
        )

    if settings.whisper_device == "nvidia_nim":
        api_key = settings.nvidia_nim_api_key.strip()
        return await _transcribe_via_nim(
            audio_bytes,
            model=settings.whisper_model,
            api_key=api_key,
        )

    raise HTTPException(
        503,
        "Voice transcription not configured. "
        "Set WHISPER_BINARY (path to whisper.cpp) or WHISPER_DEVICE=nvidia_nim.",
    )


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str = Field(default="", max_length=200)


@dashboard_router.post("/v1/tts")
async def text_to_speech(
    body: TtsIn,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
) -> Response:
    """Convert text to speech audio.

    Provider selection via ``TTS_PROVIDER`` setting:
    - ``mac_say``  — macOS ``say`` command (zero-cost, returns AIFF audio)
    - ``elevenlabs`` — ElevenLabs API (mp3 audio, per-character billed)
    - ``none`` → HTTP 503

    Returns raw audio bytes with the appropriate Content-Type.
    """
    provider = settings.tts_provider
    text = body.text.strip()

    if not text:
        raise HTTPException(400, "text is required")

    if provider == "none":
        raise HTTPException(503, "TTS is disabled (TTS_PROVIDER=none)")

    if provider == "mac_say":
        return await _tts_mac_say(text, voice=body.voice or "")

    if provider == "elevenlabs":
        return await _tts_elevenlabs(
            text,
            voice_id=body.voice or settings.elevenlabs_voice_id,
            api_key=settings.elevenlabs_api_key,
        )

    raise HTTPException(503, f"Unknown TTS provider: {provider!r}")


async def _tts_mac_say(text: str, voice: str) -> Response:
    """Synthesize speech via macOS ``say`` and return AIFF bytes."""
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
        out_path = tmp.name

    cmd = ["say", "-o", out_path]
    if voice:
        cmd += ["-v", voice]
    cmd.append(text)

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
            ),
        )
        if result.returncode != 0:
            raise HTTPException(
                500,
                f"say command failed (exit {result.returncode}): "
                + result.stderr.decode(errors="replace")[:200],
            )
        audio_bytes = Path(out_path).read_bytes()
        return Response(content=audio_bytes, media_type="audio/aiff")
    finally:
        Path(out_path).unlink(missing_ok=True)


async def _tts_elevenlabs(text: str, voice_id: str, api_key: str) -> Response:
    """POST to ElevenLabs TTS API and return mp3 bytes."""
    if not api_key:
        raise HTTPException(503, "ELEVENLABS_API_KEY is not configured")
    url = _ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    try:
        async with httpx.AsyncClient(timeout=_ELEVENLABS_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": "eleven_monolingual_v1",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
            )
        if resp.status_code != 200:
            raise HTTPException(
                502,
                f"ElevenLabs API returned HTTP {resp.status_code}",
            )
        return Response(content=resp.content, media_type="audio/mpeg")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"ElevenLabs TTS failed: {exc}") from exc
