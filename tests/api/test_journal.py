"""Tests for the daily journal feature.

Coverage:
- POST /v1/journal writes an entry to a tmp vault path.
- GET /v1/journal reads it back.
- GET /v1/me has_today_journal flag flips after the entry is written.
- Empty answers create a valid stub file (skip-today flow).
- GET /v1/journal?date=... returns a specific past date.
- Malformed date -> 400.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api import datastore
from api.app import create_app
from api.dependencies import get_settings
from config.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides: Any) -> Settings:
    """Create a Settings instance with sensible test defaults."""
    s = Settings()
    object.__setattr__(s, "anthropic_auth_token", "")
    for k, v in overrides.items():
        object.__setattr__(s, k, v)
    return s


def _make_client(
    tmp_path: Path,
    vault_root: Path,
    subpath: str,
    monkeypatch: pytest.MonkeyPatch,
    port: int,
) -> TestClient:
    """Return a TestClient with the journal vault patched to ``vault_root``."""
    import api.agent.journal as _jmod

    monkeypatch.setattr(_jmod, "resolve_journal_vault", lambda: (vault_root, subpath))
    monkeypatch.setenv("HOME", str(tmp_path))
    datastore.reset_for_tests(tmp_path / f"j{port}.sqlite")

    app = create_app(lifespan_enabled=False)
    settings = _make_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, client=("127.0.0.1", port))


# ---------------------------------------------------------------------------
# POST /v1/journal
# ---------------------------------------------------------------------------


def test_journal_post_creates_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/journal writes a markdown file to the vault."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    subpath = "Companion/Journal"
    today = datetime.date.today().isoformat()

    c = _make_client(tmp_path, vault_root, subpath, monkeypatch, 50097)
    with c:
        r = c.post(
            "/v1/journal",
            json={
                "date": today,
                "answers": {
                    "focus": "Ship the journal feature",
                    "remember": "Review PR at 3pm",
                    "mood": "Energised",
                },
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["date"] == today

    entry_path = vault_root / subpath / f"{today}.md"
    assert entry_path.is_file(), f"Expected {entry_path} to exist"
    content = entry_path.read_text(encoding="utf-8")
    assert "Ship the journal feature" in content
    assert "Review PR at 3pm" in content
    assert "Energised" in content
    assert f"date: {today}" in content

    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# GET /v1/journal
# ---------------------------------------------------------------------------


def test_journal_get_reads_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /v1/journal returns the entry written by POST."""
    vault_root = tmp_path / "vault2"
    vault_root.mkdir()
    subpath = "Companion/Journal"
    today = datetime.date.today().isoformat()

    c = _make_client(tmp_path, vault_root, subpath, monkeypatch, 50096)
    with c:
        # GET before any entry -> exists=False
        r = c.get(f"/v1/journal?date={today}")
        assert r.status_code == 200
        body = r.json()
        assert body["exists"] is False
        assert body["content"] is None

        # POST to create
        c.post(
            "/v1/journal",
            json={"date": today, "answers": {"focus": "Hello journal", "mood": "ok"}},
        )

        # GET after -> exists=True with content
        r2 = c.get(f"/v1/journal?date={today}")
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["exists"] is True
        assert "Hello journal" in (body2["content"] or "")

    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# GET /v1/me -- has_today_journal flag
# ---------------------------------------------------------------------------


def test_me_has_today_journal_false_initially(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """has_today_journal is False before any entry is written."""
    vault_root = tmp_path / "vault3"
    vault_root.mkdir()

    c = _make_client(tmp_path, vault_root, "Companion/Journal", monkeypatch, 50095)
    with c:
        r = c.get("/v1/me")
        assert r.status_code == 200
        assert r.json()["has_today_journal"] is False

    datastore.reset_for_tests()


def test_me_has_today_journal_true_after_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """has_today_journal flips to True after POST /v1/journal."""
    vault_root = tmp_path / "vault4"
    vault_root.mkdir()
    subpath = "Companion/Journal"
    today = datetime.date.today().isoformat()

    c = _make_client(tmp_path, vault_root, subpath, monkeypatch, 50094)
    with c:
        # Initially false
        r = c.get("/v1/me")
        assert r.json()["has_today_journal"] is False

        # Write entry
        r2 = c.post(
            "/v1/journal",
            json={"date": today, "answers": {"focus": "test"}},
        )
        assert r2.status_code == 200

        # Now true
        r3 = c.get("/v1/me")
        assert r3.json()["has_today_journal"] is True

    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# Empty answers -> stub file (skip today)
# ---------------------------------------------------------------------------


def test_journal_post_empty_answers_creates_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST with empty answers creates a stub file so the banner won't reappear."""
    vault_root = tmp_path / "vault5"
    vault_root.mkdir()
    subpath = "Companion/Journal"
    today = datetime.date.today().isoformat()

    c = _make_client(tmp_path, vault_root, subpath, monkeypatch, 50093)
    with c:
        r = c.post("/v1/journal", json={"date": today, "answers": {}})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True

        entry_path = vault_root / subpath / f"{today}.md"
        assert entry_path.is_file()

        # Banner flag must flip
        r2 = c.get("/v1/me")
        assert r2.json()["has_today_journal"] is True

    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_journal_post_invalid_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST with a non-ISO date returns 422 (Pydantic pattern validation)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    datastore.reset_for_tests(tmp_path / "j6.sqlite")

    app = create_app(lifespan_enabled=False)
    settings = _make_settings()
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app, client=("127.0.0.1", 50092)) as c:
        r = c.post("/v1/journal", json={"date": "not-a-date", "answers": {}})
        assert r.status_code == 422

    datastore.reset_for_tests()


def test_journal_get_invalid_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /v1/journal with a bad date returns 400."""
    monkeypatch.setenv("HOME", str(tmp_path))
    datastore.reset_for_tests(tmp_path / "j7.sqlite")

    app = create_app(lifespan_enabled=False)
    settings = _make_settings()
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app, client=("127.0.0.1", 50091)) as c:
        r = c.get("/v1/journal?date=banana")
        assert r.status_code == 400

    datastore.reset_for_tests()


def test_journal_get_defaults_to_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /v1/journal with no date param defaults to today."""
    vault_root = tmp_path / "vault8"
    vault_root.mkdir()

    c = _make_client(tmp_path, vault_root, "Companion/Journal", monkeypatch, 50090)
    with c:
        r = c.get("/v1/journal")
        assert r.status_code == 200
        body = r.json()
        assert body["date"] == datetime.date.today().isoformat()
        assert body["exists"] is False

    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# Journal helpers unit tests
# ---------------------------------------------------------------------------


def test_write_and_read_journal_entry(tmp_path: Path) -> None:
    """write_journal_entry + read_journal_entry round-trip."""
    from api.agent.journal import (
        DEFAULT_QUESTIONS,
        read_journal_entry,
        write_journal_entry,
    )

    vault_root = tmp_path / "vault"
    subpath = "J"
    date = datetime.date(2026, 5, 18)
    answers = {"focus": "Build it", "remember": "Check docs", "mood": "Fine"}

    path = write_journal_entry(
        date,
        answers,
        questions=DEFAULT_QUESTIONS,
        vault_root=vault_root,
        subpath=subpath,
    )
    assert path.is_file()

    content = read_journal_entry(date, vault_root=vault_root, subpath=subpath)
    assert content is not None
    assert "Build it" in content
    assert "date: 2026-05-18" in content


def test_read_journal_entry_missing(tmp_path: Path) -> None:
    """read_journal_entry returns None when file does not exist."""
    from api.agent.journal import read_journal_entry

    result = read_journal_entry(
        datetime.date(2025, 1, 1),
        vault_root=tmp_path / "nonexistent",
        subpath="",
    )
    assert result is None


def test_has_journal_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """has_journal_entry returns False before writing, True after."""
    from api.agent.journal import (
        DEFAULT_QUESTIONS,
        has_journal_entry,
        write_journal_entry,
    )

    vault_root = tmp_path / "hj"
    subpath = "Daily"
    date = datetime.date.today()

    monkeypatch.setattr(
        "api.agent.journal.resolve_journal_vault",
        lambda: (vault_root, subpath),
    )

    assert not has_journal_entry(date)

    write_journal_entry(
        date,
        {"focus": "x"},
        questions=DEFAULT_QUESTIONS,
        vault_root=vault_root,
        subpath=subpath,
    )
    assert has_journal_entry(date)
