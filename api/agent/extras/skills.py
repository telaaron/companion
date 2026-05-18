"""Skill marketplace tools — SkillList and SkillRun.

Skills live in ``skills/{name}/`` at the repo root (or the path
configured via ``SKILLS_DIR``).  Each skill folder must contain a
``SKILL.md`` with YAML front-matter that defines at minimum::

    ---
    name: echo
    description: Echoes the input text back.
    entry: entry.py
    ---

``SkillRun`` runs ``python entry.py`` with ``args`` serialised as
JSON on stdin.  Stdout is captured and returned as a :class:`ToolResult`.
Stderr is appended on non-zero exit.  Hard-killed after 60 seconds.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.tools.result import ToolResult
from core.tools.workspace import Workspace

_SKILL_RUN_TIMEOUT_S = 60.0


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _skills_root() -> Path:
    """Return the configured or auto-detected skills root directory."""
    # Import lazily to avoid circular imports at module load time.
    try:
        from config.settings import get_settings

        configured = (get_settings().skills_dir or "").strip()
        if configured:
            return Path(configured)
    except Exception:
        pass

    # Auto-detect: look for a ``skills/`` dir relative to this file's repo root.
    here = Path(__file__).resolve()
    # Walk up from api/agent/extras/ → api/agent/ → api/ → repo root
    for parent in here.parents:
        candidate = parent / "skills"
        if candidate.is_dir():
            return candidate
        if (parent / "pyproject.toml").is_file():
            # Repo root found; skills/ may not exist yet
            return parent / "skills"
    return Path.cwd() / "skills"


def _safe_entry(skill_dir: Path, entry_rel: str) -> Path | None:
    """Resolve entry path and ensure it stays inside *skill_dir*.

    Returns None when the resolved path escapes the skill directory.
    """
    try:
        resolved = (skill_dir / entry_rel).resolve()
        skill_dir_resolved = skill_dir.resolve()
        resolved.relative_to(skill_dir_resolved)  # raises ValueError if outside
        return resolved
    except ValueError, OSError:
        return None


# ---------------------------------------------------------------------------
# Front-matter parser
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML-like front-matter block from a SKILL.md header.

    Only handles simple ``key: value`` pairs (no lists, no nesting).
    """
    lines = text.lstrip().splitlines()
    result: dict[str, str] = {}
    if not lines or lines[0].strip() != "---":
        # No front-matter; try parsing top-level key: value lines anyway.
        for line in lines[:20]:
            if ":" in line and not line.startswith("#"):
                k, _, v = line.partition(":")
                result[k.strip().lower()] = v.strip().strip("\"'")
        return result

    in_block = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            if in_block:
                break
            in_block = True
            continue
        if not in_block:
            in_block = True
        if ":" in stripped and not stripped.startswith("#"):
            k, _, v = stripped.partition(":")
            result[k.strip().lower()] = v.strip().strip("\"'")
    return result


# ---------------------------------------------------------------------------
# Skill dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    entry: str
    version: str
    args_schema: dict[str, Any]
    skill_dir: Path

    @classmethod
    def from_skill_md(cls, skill_md: Path) -> Skill | None:
        """Parse a SKILL.md and return a :class:`Skill`, or None on failure."""
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")[:8192]
        except OSError:
            return None
        fm = _parse_frontmatter(text)
        name = fm.get("name") or skill_md.parent.name
        if not name:
            return None
        return cls(
            name=name,
            description=fm.get("description", "")[:400],
            entry=fm.get("entry", "entry.py"),
            version=fm.get("version", "0.1.0"),
            args_schema=json.loads(fm["args_schema"])
            if "args_schema" in fm
            else {"type": "object", "properties": {}, "required": []},
            skill_dir=skill_md.parent,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "entry": self.entry,
            "version": self.version,
            "path": str(self.skill_dir),
        }


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


_LOADED_SKILLS: dict[str, Skill] = {}


def load_skills(root: Path | None = None) -> dict[str, Skill]:
    """Scan *root* for skill directories and return a mapping of name → Skill."""
    base = root or _skills_root()
    skills: dict[str, Skill] = {}
    if not base.is_dir():
        return skills
    for skill_md in sorted(base.rglob("SKILL.md")):
        try:
            skill_md.relative_to(base)
        except ValueError:
            continue
        skill = Skill.from_skill_md(skill_md)
        if skill is None:
            continue
        skills[skill.name] = skill
    return skills


def rescan_skills(root: Path | None = None) -> None:
    """Rescan and update the in-process skill cache."""
    global _LOADED_SKILLS
    _LOADED_SKILLS = load_skills(root)


def get_skill(name: str) -> Skill | None:
    """Return a skill by name, rescanning if not found."""
    if name in _LOADED_SKILLS:
        return _LOADED_SKILLS[name]
    rescan_skills()
    return _LOADED_SKILLS.get(name)


def list_skills() -> list[Skill]:
    """Return all cached skills."""
    return list(_LOADED_SKILLS.values())


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


async def execute_skill_list(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    """Return a JSON list of installed skills."""
    rescan_skills()
    skills = [s.to_dict() for s in list_skills()]
    return ToolResult(
        content=json.dumps({"skills": skills, "count": len(skills)}, indent=2),
        metadata={"count": len(skills)},
    )


async def execute_skill_run(
    input_data: dict[str, Any], workspace: Workspace
) -> ToolResult:
    """Run a named skill with the supplied args dict.

    Looks up ``skills/{name}/``, resolves the entry script, launches it as
    a Python subprocess with args serialised as JSON on stdin.  Returns stdout
    on success; stderr on non-zero exit.  Hard-killed after 60 seconds.
    """
    name = (input_data.get("name") or "").strip()
    if not name:
        return ToolResult(content="Error: 'name' is required", is_error=True)

    args = input_data.get("args") or {}
    if not isinstance(args, dict):
        try:
            args = json.loads(str(args))
        except Exception:
            args = {}

    skill = get_skill(name)
    if skill is None:
        return ToolResult(
            content=f"Error: skill '{name}' is not installed",
            is_error=True,
        )

    entry = _safe_entry(skill.skill_dir, skill.entry)
    if entry is None:
        return ToolResult(
            content=f"Error: skill '{name}' entry path escapes its folder — rejected",
            is_error=True,
        )
    if not entry.is_file():
        return ToolResult(
            content=f"Error: skill '{name}' entry file not found: {skill.entry}",
            is_error=True,
        )

    args_json = json.dumps(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(entry),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(skill.skill_dir),
            env={**os.environ},
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(args_json.encode()),
                timeout=_SKILL_RUN_TIMEOUT_S,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                content=f"Error: skill '{name}' timed out after {int(_SKILL_RUN_TIMEOUT_S)}s",
                is_error=True,
            )

        stdout = stdout_bytes.decode(errors="replace").strip()
        stderr = stderr_bytes.decode(errors="replace").strip()

        if proc.returncode != 0:
            detail = stdout or ""
            if stderr:
                detail = f"{detail}\nstderr: {stderr}".strip()
            return ToolResult(
                content=f"Error: skill '{name}' exited {proc.returncode}: {detail}",
                is_error=True,
            )

        return ToolResult(
            content=stdout or "(skill produced no output)",
            metadata={"skill": name, "exit_code": 0},
        )

    except Exception as exc:
        return ToolResult(
            content=f"Error: failed to launch skill '{name}': {exc}",
            is_error=True,
        )
