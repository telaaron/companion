"""Tests for the legacy /app prototype now redirecting to /ui."""

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


def test_app_root_redirects_to_ui(local_app) -> None:
    response = _local_client(local_app).get("/app", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/ui/"


def test_app_slash_redirects_to_ui(local_app) -> None:
    response = _local_client(local_app).get("/app/", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/ui/"


def test_legacy_admin_banner_links_to_ui(local_app) -> None:
    response = _local_client(local_app).get("/admin")
    assert response.status_code == 200
    # banner content references the new UI mount
    assert b"/app" in response.content or b"/ui" in response.content
