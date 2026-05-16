"""Tests for the GET /v1/preview/file endpoint.

Covers:
- Path traversal → 403
- File > 1 MiB → 413
- Normal file → 200 with correct language hint
- Missing path param → 400
- File not found → 404
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_settings
from config.settings import Settings


def _client_for(tmp_path: Path, monkeypatch) -> tuple[TestClient, Settings]:
    """Return a TestClient and Settings pointed at tmp_path as workspace."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    settings = Settings()
    # Use tmp_path as the agent workspace so Workspace.create() resolves there.
    object.__setattr__(settings, "agent_default_workspace", str(tmp_path))
    # No auth required (ANTHROPIC_AUTH_TOKEN is "" from conftest).
    object.__setattr__(settings, "anthropic_auth_token", "")

    app = create_app(lifespan_enabled=False)
    app.dependency_overrides[get_settings] = lambda: settings

    client = TestClient(app, client=("127.0.0.1", 50000))
    return client, settings


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    """Yield a (client, workspace_root) pair."""
    client, _ = _client_for(tmp_path, monkeypatch)
    yield client, tmp_path


# ------------------------------------------------------------------ happy path


def test_preview_normal_python_file(workspace) -> None:
    client, root = workspace
    f = root / "hello.py"
    f.write_text("print('hello')\n", encoding="utf-8")

    r = client.get(f"/v1/preview/file?path={f}")
    assert r.status_code == 200
    data = r.json()
    assert data["language"] == "python"
    assert "print" in data["content"]
    assert data["truncated"] is False
    assert data["size"] == f.stat().st_size


def test_preview_language_hint_by_extension(workspace) -> None:
    client, root = workspace
    cases = [
        ("script.js", "javascript"),
        ("style.css", "css"),
        ("config.yaml", "yaml"),
        ("data.json", "json"),
        ("README.md", "markdown"),
        ("query.sql", "sql"),
        ("main.rs", "rust"),
        ("server.go", "go"),
        ("run.sh", "bash"),
        ("page.ts", "typescript"),
        ("index.html", "markup"),
        ("unknown.xyz", "plaintext"),
    ]
    for filename, expected_lang in cases:
        p = root / filename
        p.write_text("content", encoding="utf-8")
        r = client.get(f"/v1/preview/file?path={p}")
        assert r.status_code == 200, f"{filename}: got {r.status_code}"
        assert r.json()["language"] == expected_lang, f"{filename}: wrong lang"


def test_preview_accepts_session_id_param(workspace) -> None:
    client, root = workspace
    f = root / "notes.txt"
    f.write_text("hello world", encoding="utf-8")

    r = client.get(f"/v1/preview/file?path={f}&session_id=abc123")
    assert r.status_code == 200


# ------------------------------------------------------------------ path traversal


def test_preview_path_traversal_blocked(workspace) -> None:
    client, root = workspace
    # Attempt to read a file outside the workspace via ../..
    traversal = str(root / ".." / ".." / "etc" / "passwd")
    r = client.get(f"/v1/preview/file?path={traversal}")
    assert r.status_code == 403


def test_preview_path_traversal_via_dotdot(workspace) -> None:
    client, _root = workspace
    # Relative traversal that would leave workspace.
    r = client.get("/v1/preview/file?path=../../etc/passwd")
    assert r.status_code == 403


def test_preview_absolute_path_outside_workspace_blocked(workspace) -> None:
    client, _root = workspace
    # /tmp is almost certainly outside tmp_path's unique subdir.
    r = client.get("/v1/preview/file?path=/tmp")
    assert r.status_code in (403, 404)


# ------------------------------------------------------------------ size limit


def test_preview_file_over_1mb_returns_413(workspace) -> None:
    client, root = workspace
    big = root / "big.txt"
    # Write just over 1 MiB.
    big.write_bytes(b"x" * (1 * 1024 * 1024 + 1))

    r = client.get(f"/v1/preview/file?path={big}")
    assert r.status_code == 413
    data = r.json()
    # FastAPI wraps detail in {"detail": ...} for HTTPException.
    detail = data.get("detail", data)
    if isinstance(detail, dict):
        assert detail.get("truncated") is True


def test_preview_file_exactly_1mb_is_allowed(workspace) -> None:
    client, root = workspace
    exact = root / "exact.bin"
    exact.write_bytes(b"a" * (1 * 1024 * 1024))

    r = client.get(f"/v1/preview/file?path={exact}")
    assert r.status_code == 200


# ------------------------------------------------------------------ error cases


def test_preview_missing_path_param_returns_error(workspace) -> None:
    client, _ = workspace
    r = client.get("/v1/preview/file")
    # FastAPI returns 422 for missing required query param.
    assert r.status_code == 422


def test_preview_file_not_found_returns_404(workspace) -> None:
    client, root = workspace
    r = client.get(f"/v1/preview/file?path={root}/nonexistent.py")
    assert r.status_code == 404


def test_preview_path_must_be_a_file_not_directory(workspace) -> None:
    client, root = workspace
    subdir = root / "subdir"
    subdir.mkdir()
    r = client.get(f"/v1/preview/file?path={subdir}")
    assert r.status_code == 404
