"""Daily journal helpers.

Provides:
- ``resolve_journal_path`` — locate the vault root from plugin config.
- ``journal_entry_path``   — canonical vault-relative + absolute path for a date.
- ``has_journal_entry``    — quick existence check (no I/O beyond stat).
- ``write_journal_entry``  — create or overwrite an entry file.
- ``read_journal_entry``   — return raw markdown text for a date.

The journal plugin YAML schema::

    kind: journal
    vault_root: ~/Documents/Obsidian Vault
    subpath: Companion/Journal

If no journal plugin is configured, ``vault_root`` falls back to ``~/journal``
and ``subpath`` is empty (entries land at ``~/journal/YYYY-MM-DD.md``).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from api.agent.plugins import PLUGINS_DIR

# Default question set — configurable via Settings.journal_questions in future.
DEFAULT_QUESTIONS: list[dict[str, str]] = [
    {"id": "focus", "prompt": "What's your focus today?", "type": "textarea"},
    {
        "id": "remember",
        "prompt": "Anything you want me to remember?",
        "type": "textarea",
    },
    {
        "id": "mood",
        "prompt": "How are you feeling? (mood / energy)",
        "type": "textarea",
    },
]

_SUBPATH_DEFAULT = "Companion/Journal"
_FALLBACK_VAULT = Path.home() / "journal"


def _load_journal_plugin() -> dict[str, Any] | None:
    """Scan PLUGINS_DIR for a ``kind: journal`` entry and return it."""
    if not PLUGINS_DIR.is_dir():
        return None

    from api.agent.plugins import _parse_yaml

    for path in sorted(PLUGINS_DIR.glob("*.yaml")):
        try:
            data = _parse_yaml(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("kind") == "journal":
            return data
    return None


def resolve_journal_vault() -> tuple[Path, str]:
    """Return ``(vault_root, subpath)`` from config or defaults."""
    plugin = _load_journal_plugin()
    if plugin:
        vault_root_raw = plugin.get("vault_root") or ""
        subpath = plugin.get("subpath") or _SUBPATH_DEFAULT
        if vault_root_raw:
            vault_root = Path(vault_root_raw).expanduser()
            logger.debug("JOURNAL: vault={} subpath={}", vault_root, subpath)
            return vault_root, subpath

    # Fallback: look for an obsidian_vault plugin and attach a Journal subpath.
    from api.agent.plugins import discover_plugins

    for p in discover_plugins():
        if p.get("kind") == "obsidian_vault":
            vp = p.get("vault_path") or ""
            if vp:
                vault_root = Path(vp).expanduser()
                subpath = _SUBPATH_DEFAULT
                logger.debug(
                    "JOURNAL: using obsidian_vault plugin vault={} subpath={}",
                    vault_root,
                    subpath,
                )
                return vault_root, subpath

    return _FALLBACK_VAULT, ""


def journal_entry_path(date: datetime.date, *, vault_root: Path, subpath: str) -> Path:
    """Return the absolute path for a journal entry date."""
    filename = f"{date.isoformat()}.md"
    if subpath:
        return vault_root / subpath / filename
    return vault_root / filename


def has_journal_entry(date: datetime.date) -> bool:
    """Return True when the journal file for ``date`` already exists."""
    vault_root, subpath = resolve_journal_vault()
    path = journal_entry_path(date, vault_root=vault_root, subpath=subpath)
    return path.is_file()


def write_journal_entry(
    date: datetime.date,
    answers: dict[str, str],
    *,
    questions: list[dict[str, str]] | None = None,
    vault_root: Path | None = None,
    subpath: str | None = None,
) -> Path:
    """Write (or overwrite) the journal entry for ``date``.

    Returns the absolute path of the written file.
    """
    if vault_root is None or subpath is None:
        _vault_root, _subpath = resolve_journal_vault()
        vault_root = vault_root or _vault_root
        subpath = subpath if subpath is not None else _subpath

    qs = questions or DEFAULT_QUESTIONS
    path = journal_entry_path(date, vault_root=vault_root, subpath=subpath)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "---",
        f"date: {date.isoformat()}",
        "---",
        "",
    ]
    for q in qs:
        qid = q.get("id", "")
        prompt = q.get("prompt", qid)
        answer = (answers.get(qid) or "").strip()
        # Use a heading derived from the question id (capitalised).
        heading = qid.replace("_", " ").title() if qid else prompt
        lines.append(f"## {heading}")
        lines.append(answer)
        lines.append("")

    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    logger.info("JOURNAL: wrote entry path={} date={}", path, date)
    return path


def read_journal_entry(
    date: datetime.date,
    *,
    vault_root: Path | None = None,
    subpath: str | None = None,
) -> str | None:
    """Return the raw markdown for a journal entry, or None if missing."""
    if vault_root is None or subpath is None:
        _vault_root, _subpath = resolve_journal_vault()
        vault_root = vault_root or _vault_root
        subpath = subpath if subpath is not None else _subpath

    path = journal_entry_path(date, vault_root=vault_root, subpath=subpath)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")
