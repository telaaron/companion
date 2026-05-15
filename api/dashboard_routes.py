"""Dashboard API routes — projects, sessions, usage, env vault, root files,
skills, file edits, audit log, model discovery.

These endpoints back the browser UI mounted at ``/ui``. They live behind
the same auth as the Anthropic-compat routes (``ANTHROPIC_AUTH_TOKEN``).
All paths are JSON. Reads are cheap; writes are rate-limited only by
the global proxy rate limiter (single-user assumption).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from config.settings import Settings
from providers.registry import ProviderRegistry

from . import datastore
from .dependencies import get_settings, require_api_key
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


# ============================================================ Projects


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    shared_context: str = Field(default="", max_length=20000)
    color: str = Field(default="#6366f1", max_length=16)
    workspace_path: str = Field(default="", max_length=4096)


@dashboard_router.get("/v1/projects")
async def list_projects(_auth=Depends(require_api_key)) -> dict[str, Any]:
    return {"projects": datastore.list_projects()}


@dashboard_router.post("/v1/projects")
async def create_project(
    body: ProjectIn, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    return datastore.upsert_project(
        name=body.name,
        description=body.description,
        shared_context=body.shared_context,
        color=body.color,
        workspace_path=body.workspace_path,
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


# ============================================================ Sessions


class SessionIn(BaseModel):
    title: str = Field(default="untitled", max_length=200)
    model: str = Field(default="", max_length=200)
    project_id: str | None = None


@dashboard_router.get("/v1/sessions")
async def list_sessions_route(
    project_id: str | None = None, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    return {"sessions": datastore.list_sessions(project_id=project_id)}


@dashboard_router.post("/v1/sessions")
async def create_session_route(
    body: SessionIn, _auth=Depends(require_api_key)
) -> dict[str, Any]:
    return datastore.upsert_session(
        title=body.title, model=body.model, project_id=body.project_id
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
    category: str | None = None,
    limit: int = 200,
    _auth=Depends(require_api_key),
) -> dict[str, Any]:
    return {
        "events": datastore.list_audit(
            category=category, limit=max(1, min(limit, 1000))
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


@dashboard_router.get("/v1/capabilities")
async def capabilities_route(
    settings: Settings = Depends(get_settings), _auth=Depends(require_api_key)
) -> dict[str, Any]:
    """Return the capability snapshot grouped by status for the UI."""
    from .capabilities import scan as scan_capabilities
    from .capabilities import to_payload as capabilities_to_payload

    return capabilities_to_payload(scan_capabilities(settings))
