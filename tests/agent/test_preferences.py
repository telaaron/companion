"""Tests for api/agent/extras/preferences.py and the /v1/preferences routes.

Covers:
- PreferenceSet writes a new preference (atomic file write).
- PreferenceSet updates an existing preference in-place (no duplication).
- PreferenceList returns all entries.
- PreferenceDelete removes the key and reports found=True.
- PreferenceDelete on a missing key reports found=False without error.
- load_prefs_text returns formatted block when prefs exist; empty when none.
- load_prefs_text skips the header when prefs file is empty.
- Key validation: empty key returns is_error.
- Value validation: value too long returns is_error.
- Length cap: key > 200 chars returns is_error.
- Length cap: value > 200 chars returns is_error.
- Dashboard GET /v1/preferences returns the current list.
- Dashboard DELETE /v1/preferences/{key} removes an entry.
- Dashboard DELETE /v1/preferences/{key} on missing key returns ok=True.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path):
    from core.tools.workspace import Workspace

    ws = MagicMock(spec=Workspace)
    ws.root = tmp_path
    return ws


# ---------------------------------------------------------------------------
# Unit tests — preferences.py (file operations)
# ---------------------------------------------------------------------------


class TestPreferenceSet:
    @pytest.mark.asyncio
    async def test_set_new_preference(self, tmp_path: Path):
        from api.agent.extras.preferences import _load_prefs, execute_set

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)

        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            result = await execute_set(
                {"key": "language", "value": "Always respond in German"}, ws
            )

        assert not result.is_error
        assert "language" in result.content
        assert "Always respond in German" in result.content
        prefs = _load_prefs(prefs_file)
        assert prefs["language"] == "Always respond in German"

    @pytest.mark.asyncio
    async def test_set_updates_existing_key_in_place(self, tmp_path: Path):
        from api.agent.extras.preferences import _load_prefs, execute_set

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)

        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set(
                {"key": "language", "value": "Always respond in German"}, ws
            )
            await execute_set(
                {"key": "language", "value": "Always respond in French"}, ws
            )

        prefs = _load_prefs(prefs_file)
        assert prefs["language"] == "Always respond in French"
        # Only one entry for "language"
        text = prefs_file.read_text(encoding="utf-8")
        assert text.count("**language**") == 1

    @pytest.mark.asyncio
    async def test_set_multiple_keys(self, tmp_path: Path):
        from api.agent.extras.preferences import _load_prefs, execute_set

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)

        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set({"key": "language", "value": "German"}, ws)
            await execute_set({"key": "confirm_delete", "value": "Always ask"}, ws)

        prefs = _load_prefs(prefs_file)
        assert len(prefs) == 2
        assert prefs["confirm_delete"] == "Always ask"

    @pytest.mark.asyncio
    async def test_set_empty_key_returns_error(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_set

        ws = _make_workspace(tmp_path)
        result = await execute_set({"key": "", "value": "something"}, ws)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_set_key_too_long_returns_error(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_set

        ws = _make_workspace(tmp_path)
        result = await execute_set({"key": "x" * 201, "value": "v"}, ws)
        assert result.is_error
        assert "200" in result.content

    @pytest.mark.asyncio
    async def test_set_value_too_long_returns_error(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_set

        ws = _make_workspace(tmp_path)
        result = await execute_set({"key": "lang", "value": "v" * 201}, ws)
        assert result.is_error
        assert "200" in result.content

    @pytest.mark.asyncio
    async def test_set_creates_parent_dir(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_set

        prefs_file = tmp_path / "nested" / "dir" / "preferences.md"
        ws = _make_workspace(tmp_path)
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            result = await execute_set({"key": "tone", "value": "casual"}, ws)
        assert not result.is_error
        assert prefs_file.is_file()

    @pytest.mark.asyncio
    async def test_set_metadata_contains_key_and_value(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_set

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            result = await execute_set({"key": "tone", "value": "casual"}, ws)
        assert result.metadata is not None
        assert result.metadata["key"] == "tone"
        assert result.metadata["value"] == "casual"


class TestPreferenceDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_key(self, tmp_path: Path):
        from api.agent.extras.preferences import (
            _load_prefs,
            execute_delete,
            execute_set,
        )

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)

        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set({"key": "language", "value": "German"}, ws)
            result = await execute_delete({"key": "language"}, ws)

        assert not result.is_error
        assert result.metadata is not None
        assert result.metadata["found"] is True
        prefs = _load_prefs(prefs_file)
        assert "language" not in prefs

    @pytest.mark.asyncio
    async def test_delete_missing_key_no_error(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_delete

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            result = await execute_delete({"key": "nonexistent"}, ws)
        assert not result.is_error
        assert result.metadata is not None
        assert result.metadata["found"] is False

    @pytest.mark.asyncio
    async def test_delete_empty_key_returns_error(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_delete

        ws = _make_workspace(tmp_path)
        result = await execute_delete({"key": ""}, ws)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_delete_leaves_other_keys_intact(self, tmp_path: Path):
        from api.agent.extras.preferences import (
            _load_prefs,
            execute_delete,
            execute_set,
        )

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set({"key": "language", "value": "German"}, ws)
            await execute_set({"key": "confirm", "value": "Always ask"}, ws)
            await execute_delete({"key": "language"}, ws)

        prefs = _load_prefs(prefs_file)
        assert "language" not in prefs
        assert prefs["confirm"] == "Always ask"


class TestPreferenceList:
    @pytest.mark.asyncio
    async def test_list_empty_when_no_prefs(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_list

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            result = await execute_list({}, ws)
        assert not result.is_error
        assert result.metadata is not None
        assert result.metadata["preferences"] == {}

    @pytest.mark.asyncio
    async def test_list_returns_all_entries(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_list, execute_set

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set({"key": "language", "value": "German"}, ws)
            await execute_set({"key": "tone", "value": "casual"}, ws)
            result = await execute_list({}, ws)
        assert not result.is_error
        assert "language" in result.content
        assert "German" in result.content
        assert "tone" in result.content
        assert result.metadata is not None
        assert len(result.metadata["preferences"]) == 2


class TestLoadPrefsText:
    def test_empty_when_no_file(self, tmp_path: Path):
        from api.agent.extras.preferences import load_prefs_text

        prefs_file = tmp_path / "preferences.md"
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            text = load_prefs_text()
        assert text == ""

    @pytest.mark.asyncio
    async def test_contains_header_when_prefs_exist(self, tmp_path: Path):
        from api.agent.extras.preferences import execute_set, load_prefs_text

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set({"key": "language", "value": "German"}, ws)
            text = load_prefs_text()
        assert "Long-term user preferences" in text
        assert "language" in text
        assert "German" in text

    @pytest.mark.asyncio
    async def test_empty_after_all_deleted(self, tmp_path: Path):
        from api.agent.extras.preferences import (
            execute_delete,
            execute_set,
            load_prefs_text,
        )

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set({"key": "language", "value": "German"}, ws)
            await execute_delete({"key": "language"}, ws)
            text = load_prefs_text()
        assert text == ""


# ---------------------------------------------------------------------------
# Integration tests — dashboard routes
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path):
    """FastAPI test client with preferences_path patched to tmp_path."""
    from fastapi.testclient import TestClient

    from api.app import create_app

    prefs_file = tmp_path / "preferences.md"
    app = create_app(lifespan_enabled=False)

    with (
        patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file),
        TestClient(app) as c,
    ):
        yield c


class TestPreferencesRoutes:
    def test_get_empty(self, client):
        resp = client.get("/v1/preferences")
        assert resp.status_code == 200
        data = resp.json()
        assert data["preferences"] == []

    @pytest.mark.asyncio
    async def test_get_after_set(self, tmp_path: Path):
        from fastapi.testclient import TestClient

        from api.agent.extras.preferences import execute_set
        from api.app import create_app

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        app = create_app(lifespan_enabled=False)

        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set({"key": "language", "value": "German"}, ws)
            with TestClient(app) as c:
                resp = c.get("/v1/preferences")
        assert resp.status_code == 200
        entries = resp.json()["preferences"]
        assert any(e["key"] == "language" for e in entries)

    @pytest.mark.asyncio
    async def test_delete_existing(self, tmp_path: Path):
        from fastapi.testclient import TestClient

        from api.agent.extras.preferences import execute_set
        from api.app import create_app

        prefs_file = tmp_path / "preferences.md"
        ws = _make_workspace(tmp_path)
        app = create_app(lifespan_enabled=False)

        with patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file):
            await execute_set({"key": "language", "value": "German"}, ws)
            with TestClient(app) as c:
                resp = c.delete("/v1/preferences/language")
                assert resp.status_code == 200
                data = resp.json()
                assert data["ok"] is True
                assert data["found"] is True

                # Confirm it's gone
                resp2 = c.get("/v1/preferences")
                entries = resp2.json()["preferences"]
                assert not any(e["key"] == "language" for e in entries)

    def test_delete_missing_key(self, tmp_path: Path):
        from fastapi.testclient import TestClient

        from api.app import create_app

        prefs_file = tmp_path / "preferences.md"
        app = create_app(lifespan_enabled=False)
        with (
            patch("api.agent.extras.preferences._prefs_path", return_value=prefs_file),
            TestClient(app) as c,
        ):
            resp = c.delete("/v1/preferences/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["found"] is False
