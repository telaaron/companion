"""Skill marketplace tools — SkillList and SkillRun.

Skills live in ``skills/`` at the repo root. Each skill is a directory
containing a ``SKILL.md`` front-matter file and an entry script (Python).
The agent calls ``SkillList`` to enumerate installed skills and ``SkillRun``
to invoke one.

Security constraints
--------------------
- The resolved entry script must stay inside its own skill folder.
- Subprocess has a hard 60-second timeout; on timeout the process is killed
  with ``proc.kill()`` + ``await proc.wait()``.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from core.tools.result import ToolResult
from core.tools.workspace import Workspace

# Repo-root ``skills/`` directory (two levels up from this file: api/agent/extras/)
_SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"

_SKILL_TIMEOUT_S = 60.0
_FRONT_MATTER_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*?)\s*$")


# ---------------------------------------------------------------------------
# Skill dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Skill:
    """Parsed skill metadata."""

    name: str
    slug: str  # directory name
    description: str
    entry: str  # relative path within slug dir
    args_schema: dict[str, Any]
    path: Path  # absolute path to skill dir


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def _parse_front_matter(text: str) -> dict[str, str]:
    """Parse simple ``key: value`` front-matter from SKILL.md (no nesting)."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].rstrip()
        m = _FRONT_MATTER_RE.match(line)
        if m:
            result[m.group(1)] = m.group(2).strip().strip("\"'")
    return result


def scan_skills(skills_root: Path | None = None) -> list[Skill]:
    """Walk ``skills_root`` and return parsed :class:`Skill` objects."""
    root = skills_root or _SKILLS_ROOT
    if not root.is_dir():
        return []
    skills: list[Skill] = []
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            fm = _parse_front_matter(text)
            slug = skill_dir.name
            name = fm.get("name") or slug
            description = fm.get("description") or ""
            entry = fm.get("entry") or "skill.py"
            args_schema_raw = fm.get("args_schema") or ""
            try:
                args_schema = json.loads(args_schema_raw) if args_schema_raw else {}
            except Exception:
                args_schema = {}
            skills.append(
                Skill(
                    name=name,
                    slug=slug,
                    description=description,
                    entry=entry,
                    args_schema=args_schema,
                    path=skill_dir,
                )
            )
        except Exception as exc:
            logger.warning("SKILLS: failed to parse {} — {}", skill_dir, exc)
    return skills


def _resolve_entry(skill: Skill) -> Path:
    """Resolve the entry script, rejecting any path that escapes the skill dir."""
    candidate = (skill.path / skill.entry).resolve(strict=False)
    base = skill.path.resolve(strict=False)
    if base not in candidate.parents and candidate != base:
        raise ValueError(f"entry {skill.entry!r} resolves outside skill dir {base}")
    return candidate


# ---------------------------------------------------------------------------
# SkillList tool executor
# ---------------------------------------------------------------------------


async def skill_list_execute(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    """Return a JSON list of installed skills."""
    skills = scan_skills()
    payload = [
        {
            "name": s.name,
            "slug": s.slug,
            "description": s.description,
            "entry": s.entry,
        }
        for s in skills
    ]
    return ToolResult(
        content=json.dumps(
            {"installed_skills": payload, "count": len(payload)}, indent=2
        ),
        metadata={"count": len(payload)},
    )


# ---------------------------------------------------------------------------
# SkillRun tool executor
# ---------------------------------------------------------------------------


async def skill_run_execute(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    """Invoke a skill by name and return its stdout."""
    skill_name = (input_data.get("name") or "").strip()
    args = input_data.get("args") or {}

    if not skill_name:
        return ToolResult(content="Error: 'name' is required", is_error=True)

    skills = scan_skills()
    matched = next(
        (s for s in skills if s.slug == skill_name or s.name == skill_name), None
    )
    if matched is None:
        names = [s.slug for s in skills]
        return ToolResult(
            content=f"Error: skill {skill_name!r} not found. Installed: {names}",
            is_error=True,
        )

    try:
        entry = _resolve_entry(matched)
    except ValueError as exc:
        return ToolResult(content=f"Error: {exc}", is_error=True)

    if not entry.is_file():
        return ToolResult(
            content=f"Error: entry script not found: {entry}",
            is_error=True,
        )

    args_json = json.dumps(args)
    logger.info(
        "SKILL: run slug={} entry={} args_len={}", matched.slug, entry, len(args_json)
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "python",
            str(entry),
            args_json,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(matched.path),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=_SKILL_TIMEOUT_S
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                content=f"Error: skill {skill_name!r} timed out after {_SKILL_TIMEOUT_S:.0f}s",
                is_error=True,
            )
    except Exception as exc:
        return ToolResult(
            content=f"Error: failed to launch skill {skill_name!r}: {exc}",
            is_error=True,
        )

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")

    if proc.returncode != 0:
        combined = stdout + ("\n--- stderr ---\n" + stderr if stderr else "")
        return ToolResult(
            content=f"Error (exit {proc.returncode}): {combined}",
            is_error=True,
        )

    return ToolResult(
        content=stdout or "(no output)",
        metadata={"exit_code": 0, "skill": skill_name},
    )


# ---------------------------------------------------------------------------
# SkillCreate tool executor
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return s


async def skill_create_execute(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    """Scaffold a new skill folder under ``skills/``.

    This lets the agent build a skill collaboratively in chat (describe what
    the skill should do → the agent writes SKILL.md + an optional Python entry
    script). Mirrors the dashboard ``/v1/skills/create`` endpoint.
    """
    name = (input_data.get("name") or "").strip()
    if not name:
        return ToolResult(content="Error: 'name' is required", is_error=True)
    description = (input_data.get("description") or "").strip()
    instructions = (input_data.get("instructions") or "").strip()
    entry_code = (input_data.get("entry_code") or "").strip()

    slug = _slugify(name)
    if not slug:
        return ToolResult(content="Error: could not derive a slug", is_error=True)

    dest = _SKILLS_ROOT / slug
    if dest.exists():
        return ToolResult(
            content=f"Error: a skill named {slug!r} already exists", is_error=True
        )
    dest.mkdir(parents=True, exist_ok=True)

    fm = [f"name: {name}", f"description: {description}"]
    if entry_code:
        fm.append("entry: skill.py")
    skill_md = "\n".join(fm) + "\n\n" + (instructions or name) + "\n"
    (dest / "SKILL.md").write_text(skill_md, encoding="utf-8")
    if entry_code:
        (dest / "skill.py").write_text(entry_code.rstrip() + "\n", encoding="utf-8")

    logger.info("SKILL: created slug={} entry={}", slug, bool(entry_code))
    return ToolResult(
        content=f"Created skill '{name}' (slug: {slug}) at skills/{slug}/. "
        + ("Includes a runnable skill.py." if entry_code else "Documentation-only."),
        metadata={"slug": slug, "has_entry": bool(entry_code)},
    )
