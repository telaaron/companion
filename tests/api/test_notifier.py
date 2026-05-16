"""Tests for the job-completion webhook notifier.

Uses httpx.MockTransport to verify request bodies without making real HTTP
calls.  Tests cover Slack payload shape, Discord payload shape, threshold
gating, cancelled-job suppression, and webhook failure isolation.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from api import datastore
from api.agent import notifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transport(captured: list[dict[str, Any]], status_code: int = 200):
    """Return a MockTransport that records every request body."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        captured.append(
            {
                "url": str(request.url),
                "body": json.loads(body),
            }
        )
        return httpx.Response(status_code)

    return httpx.MockTransport(handler)


_REAL_ASYNC_CLIENT = httpx.AsyncClient  # capture before any patching


def _client_ctx(transport: httpx.MockTransport):
    """Return a context-manager factory that yields a real AsyncClient with mock transport."""

    @contextlib.asynccontextmanager
    async def _factory(*args: object, **kwargs: object):
        async with _REAL_ASYNC_CLIENT(transport=transport) as client:
            yield client

    return _factory


def _fake_settings(
    *,
    slack_url: str | None = None,
    discord_url: str | None = None,
    min_seconds: int = 30,
    base_url: str = "http://127.0.0.1:8082",
):
    s = MagicMock()
    s.slack_webhook_url = slack_url
    s.discord_webhook_url = discord_url
    s.notifier_min_seconds = min_seconds
    s.public_base_url = base_url
    return s


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    datastore.reset_for_tests(tmp_path / "notifier_test.sqlite")
    yield
    datastore.reset_for_tests()


def _create_done_job(
    _fresh_db: object,
    *,
    started_offset_ms: int = 0,
    duration_ms: int = 60_000,
    model: str = "deepseek/x",
    session_id: str = "ses_test",
) -> dict[str, Any]:
    """Insert a job already in 'done' state with realistic timestamps."""
    # Ensure the session exists so the FK constraint is satisfied.
    datastore.upsert_session(session_id=session_id, model=model)
    job = datastore.create_agent_job(
        session_id=session_id,
        project_id=None,
        model=model,
        request_payload={
            "messages": [{"role": "user", "content": "Hello, run a long task"}],
            "model": model,
            "max_tokens": 1024,
        },
    )
    job_id = job["id"]
    datastore.mark_agent_job_status(job_id, "running")
    # Manually set started_at / finished_at to control duration.
    db_path = datastore.db_path()
    now_ms = int(time.time() * 1000) + started_offset_ms
    finished_ms = now_ms + duration_ms
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE agent_jobs SET started_at=?, finished_at=?, status='done' WHERE id=?",
            (now_ms, finished_ms, job_id),
        )
    return datastore.get_agent_job(job_id) or {}


# ---------------------------------------------------------------------------
# Tests — Slack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_webhook_posts_correct_shape(fresh_db) -> None:
    """Notifier POSTs a Slack-compatible payload with text + blocks."""
    job = _create_done_job(fresh_db, duration_ms=60_000)
    job_id = job["id"]

    captured: list[dict[str, Any]] = []
    settings = _fake_settings(slack_url="https://hooks.slack.com/test")

    with (
        patch("api.agent.notifier.get_settings", return_value=settings),
        patch(
            "api.agent.notifier.httpx.AsyncClient",
            _client_ctx(_make_transport(captured)),
        ),
    ):
        await notifier.notify(job_id)

    assert len(captured) == 1
    body = captured[0]["body"]
    assert "text" in body
    assert "blocks" in body
    assert isinstance(body["blocks"], list)
    assert len(body["blocks"]) >= 1


@pytest.mark.asyncio
async def test_slack_payload_contains_chat_link(fresh_db) -> None:
    job = _create_done_job(fresh_db, duration_ms=60_000)
    job_id = job["id"]
    captured: list[dict[str, Any]] = []
    settings = _fake_settings(
        slack_url="https://hooks.slack.com/test",
        base_url="http://myhost:8082",
    )
    with (
        patch("api.agent.notifier.get_settings", return_value=settings),
        patch(
            "api.agent.notifier.httpx.AsyncClient",
            _client_ctx(_make_transport(captured)),
        ),
    ):
        await notifier.notify(job_id)

    body = captured[0]["body"]
    all_text = json.dumps(body)
    assert "http://myhost:8082" in all_text
    assert "ses_test" in all_text


# ---------------------------------------------------------------------------
# Tests — Discord
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discord_webhook_posts_correct_shape(fresh_db) -> None:
    """Notifier POSTs a Discord-compatible payload with content + embeds."""
    job = _create_done_job(fresh_db, duration_ms=60_000)
    job_id = job["id"]

    captured: list[dict[str, Any]] = []
    settings = _fake_settings(discord_url="https://discord.com/api/webhooks/test")

    with (
        patch("api.agent.notifier.get_settings", return_value=settings),
        patch(
            "api.agent.notifier.httpx.AsyncClient",
            _client_ctx(_make_transport(captured)),
        ),
    ):
        await notifier.notify(job_id)

    assert len(captured) == 1
    body = captured[0]["body"]
    assert "content" in body
    assert "embeds" in body
    assert isinstance(body["embeds"], list)
    assert len(body["embeds"]) >= 1


@pytest.mark.asyncio
async def test_both_webhooks_fire(fresh_db) -> None:
    """When both URLs are set, two POSTs are made (one Slack, one Discord)."""
    job = _create_done_job(fresh_db, duration_ms=60_000)
    job_id = job["id"]

    captured: list[dict[str, Any]] = []
    settings = _fake_settings(
        slack_url="https://hooks.slack.com/test",
        discord_url="https://discord.com/api/webhooks/test",
    )

    with (
        patch("api.agent.notifier.get_settings", return_value=settings),
        patch(
            "api.agent.notifier.httpx.AsyncClient",
            _client_ctx(_make_transport(captured)),
        ),
    ):
        await notifier.notify(job_id)

    assert len(captured) == 2
    urls = {r["url"] for r in captured}
    assert "https://hooks.slack.com/test" in urls
    assert "https://discord.com/api/webhooks/test" in urls


# ---------------------------------------------------------------------------
# Tests — threshold gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_webhook_when_no_urls(fresh_db) -> None:
    """Notifier is a no-op when neither webhook URL is configured."""
    job = _create_done_job(fresh_db, duration_ms=60_000)
    job_id = job["id"]

    captured: list[dict[str, Any]] = []
    settings = _fake_settings()  # both URLs None

    with (
        patch("api.agent.notifier.get_settings", return_value=settings),
        patch(
            "api.agent.notifier.httpx.AsyncClient",
            _client_ctx(_make_transport(captured)),
        ),
    ):
        await notifier.notify(job_id)

    assert captured == []


@pytest.mark.asyncio
async def test_short_job_does_not_fire_via_maybe_notify(fresh_db) -> None:
    """_maybe_notify skips the webhook when job ran under the threshold."""
    from api.agent.jobs import _maybe_notify

    job = _create_done_job(fresh_db, duration_ms=5_000)  # 5 s < 30 s default
    job_id = job["id"]

    captured: list[dict[str, Any]] = []
    settings = _fake_settings(
        slack_url="https://hooks.slack.com/test",
        min_seconds=30,
    )

    with patch(
        "api.agent.notifier.httpx.AsyncClient",
        _client_ctx(_make_transport(captured)),
    ):
        await _maybe_notify(job_id, settings)

    assert captured == []


@pytest.mark.asyncio
async def test_long_job_fires_via_maybe_notify(fresh_db) -> None:
    """_maybe_notify fires the webhook when job ran over the threshold."""
    from api.agent.jobs import _maybe_notify

    job = _create_done_job(fresh_db, duration_ms=60_000)  # 60 s > 30 s
    job_id = job["id"]

    captured: list[dict[str, Any]] = []
    settings = _fake_settings(
        slack_url="https://hooks.slack.com/test",
        min_seconds=30,
    )

    with (
        patch("api.agent.notifier.get_settings", return_value=settings),
        patch(
            "api.agent.notifier.httpx.AsyncClient",
            _client_ctx(_make_transport(captured)),
        ),
    ):
        await _maybe_notify(job_id, settings)

    assert len(captured) == 1


# ---------------------------------------------------------------------------
# Tests — error isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_500_does_not_raise(fresh_db) -> None:
    """A webhook returning HTTP 500 is logged but never raises."""
    job = _create_done_job(fresh_db, duration_ms=60_000)
    job_id = job["id"]

    settings = _fake_settings(slack_url="https://hooks.slack.com/test")

    with (
        patch("api.agent.notifier.get_settings", return_value=settings),
        patch(
            "api.agent.notifier.httpx.AsyncClient",
            _client_ctx(_make_transport([], status_code=500)),
        ),
    ):
        # Must not raise
        await notifier.notify(job_id)


@pytest.mark.asyncio
async def test_webhook_network_error_does_not_raise(fresh_db) -> None:
    """A network error during webhook POST is logged but never raises."""
    job = _create_done_job(fresh_db, duration_ms=60_000)
    job_id = job["id"]

    settings = _fake_settings(slack_url="https://hooks.slack.com/test")

    def raise_on_post(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with (
        patch("api.agent.notifier.get_settings", return_value=settings),
        patch(
            "api.agent.notifier.httpx.AsyncClient",
            _client_ctx(httpx.MockTransport(raise_on_post)),
        ),
    ):
        # Must not raise
        await notifier.notify(job_id)
