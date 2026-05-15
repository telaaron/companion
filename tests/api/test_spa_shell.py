"""Tests for the unified /app SPA routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


def _local_client(app):
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture
def local_app(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return create_app(lifespan_enabled=False)


def test_spa_root_serves_shell(local_app) -> None:
    response = _local_client(local_app).get("/app")
    assert response.status_code == 200
    assert b"app-shell" in response.content
    assert b"shell.js" in response.content


def test_spa_root_with_slash_serves_shell(local_app) -> None:
    response = _local_client(local_app).get("/app/")
    assert response.status_code == 200
    assert b"app-shell" in response.content


@pytest.mark.parametrize(
    "page",
    ["chat", "projects", "usage", "files", "audit", "skills", "settings", "setup"],
)
def test_spa_page_routes_serve_shell(local_app, page: str) -> None:
    """Every client-side page returns the shell so the JS router can take over."""
    response = _local_client(local_app).get(f"/app/{page}")
    assert response.status_code == 200
    assert b"app-shell" in response.content


def test_spa_unknown_page_returns_404(local_app) -> None:
    response = _local_client(local_app).get("/app/definitely-not-a-page")
    assert response.status_code == 404


def test_spa_assets_shell_css_served(local_app) -> None:
    response = _local_client(local_app).get("/app/assets/shell.css")
    assert response.status_code == 200
    assert b"app-shell" in response.content


def test_spa_assets_shell_js_served(local_app) -> None:
    response = _local_client(local_app).get("/app/assets/shell.js")
    assert response.status_code == 200
    assert b"mountRoute" in response.content


@pytest.mark.parametrize(
    "module",
    [
        "home.js",
        "chat.js",
        "projects.js",
        "usage.js",
        "files.js",
        "audit.js",
        "skills.js",
        "settings.js",
        "setup.js",
        "notfound.js",
    ],
)
def test_spa_page_modules_served(local_app, module: str) -> None:
    response = _local_client(local_app).get(f"/app/assets/pages/{module}")
    assert response.status_code == 200
    body = response.content
    assert (
        b"export function render" in body or b"export async function render" in body
    ), f"{module} must export a render function"


def test_spa_assets_block_path_traversal(local_app) -> None:
    response = _local_client(local_app).get("/app/assets/..%2F..%2Fetc%2Fpasswd")
    # FastAPI's path converter normalises %2F to /, so this lands on the
    # catch-all + traversal guard. Either route returns a non-200.
    assert response.status_code in (403, 404)


def test_spa_root_loopback_only(local_app) -> None:
    remote = TestClient(local_app, client=("203.0.113.10", 50000))
    response = remote.get("/app")
    assert response.status_code == 403


def test_spa_assets_loopback_only(local_app) -> None:
    remote = TestClient(local_app, client=("203.0.113.10", 50000))
    response = remote.get("/app/assets/shell.css")
    assert response.status_code == 403


def test_legacy_admin_banner_links_to_app(local_app) -> None:
    response = _local_client(local_app).get("/admin")
    assert response.status_code == 200
    assert b"/app" in response.content
