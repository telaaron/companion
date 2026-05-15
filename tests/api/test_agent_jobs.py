"""Tests for background agent jobs — survival across client disconnect.

The promise of the job-queue architecture is: once a job starts, the work
keeps running regardless of whether the client is still connected. These
tests cover the datastore primitives + the event-stream behaviour.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from api import datastore
from api.agent.jobs import event_stream


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch):
    """Point the datastore at a throwaway SQLite file for this test."""
    datastore.reset_for_tests(tmp_path / "jobs.sqlite")
    yield
    datastore.reset_for_tests()


def test_create_and_get_agent_job(fresh_db) -> None:
    job = datastore.create_agent_job(
        session_id=None,
        project_id=None,
        model="deepseek/x",
        request_payload={"messages": []},
    )
    fetched = datastore.get_agent_job(job["id"])
    assert fetched is not None
    assert fetched["model"] == "deepseek/x"
    assert fetched["status"] == "pending"


def test_lifecycle_transitions(fresh_db) -> None:
    job = datastore.create_agent_job(
        session_id=None, project_id=None, model="x", request_payload={}
    )
    job_id = job["id"]
    datastore.mark_agent_job_status(job_id, "running")
    running = datastore.get_agent_job(job_id)
    assert running is not None
    assert running["status"] == "running"
    datastore.mark_agent_job_status(job_id, "done")
    row = datastore.get_agent_job(job_id)
    assert row is not None
    assert row["status"] == "done"
    assert row["finished_at"] is not None


def test_events_append_and_replay(fresh_db) -> None:
    job = datastore.create_agent_job(
        session_id=None, project_id=None, model="x", request_payload={}
    )
    job_id = job["id"]
    seq_a = datastore.append_agent_job_event(
        job_id, event_type="sse", data="event: a\ndata: 1\n\n"
    )
    seq_b = datastore.append_agent_job_event(
        job_id, event_type="sse", data="event: b\ndata: 2\n\n"
    )
    assert seq_a == 0
    assert seq_b == 1
    all_events = datastore.list_agent_job_events(job_id)
    assert len(all_events) == 2
    after_zero = datastore.list_agent_job_events(job_id, after_seq=0)
    assert len(after_zero) == 1
    assert after_zero[0]["seq"] == 1


@pytest.mark.asyncio
async def test_event_stream_replays_then_closes_when_done(fresh_db) -> None:
    job = datastore.create_agent_job(
        session_id=None, project_id=None, model="x", request_payload={}
    )
    job_id = job["id"]
    datastore.append_agent_job_event(
        job_id, event_type="sse", data="event: a\ndata: 1\n\n"
    )
    datastore.append_agent_job_event(
        job_id, event_type="sse", data="event: b\ndata: 2\n\n"
    )
    datastore.mark_agent_job_status(job_id, "done")
    datastore.append_agent_job_event(
        job_id, event_type="job_finished", data='{"status":"done"}'
    )

    chunks = [chunk async for chunk in event_stream(job_id, after_seq=-1)]
    body = "".join(chunks)
    assert "id: 0" in body
    assert "id: 1" in body
    assert "job_finished" in body


@pytest.mark.asyncio
async def test_event_stream_resumes_from_after_seq(fresh_db) -> None:
    job = datastore.create_agent_job(
        session_id=None, project_id=None, model="x", request_payload={}
    )
    job_id = job["id"]
    for i in range(3):
        datastore.append_agent_job_event(
            job_id, event_type="sse", data=f"event: turn\ndata: {i}\n\n"
        )
    datastore.mark_agent_job_status(job_id, "done")
    datastore.append_agent_job_event(job_id, event_type="job_finished", data="{}")

    chunks = [chunk async for chunk in event_stream(job_id, after_seq=1)]
    body = "".join(chunks)
    assert "id: 0" not in body
    assert "id: 1" not in body
    assert "id: 2" in body


@pytest.mark.asyncio
async def test_event_stream_tails_live_events(fresh_db) -> None:
    job = datastore.create_agent_job(
        session_id=None, project_id=None, model="x", request_payload={}
    )
    job_id = job["id"]
    datastore.mark_agent_job_status(job_id, "running")

    async def emit_after_delay() -> None:
        await asyncio.sleep(0.4)
        datastore.append_agent_job_event(
            job_id, event_type="sse", data="event: a\ndata: live\n\n"
        )
        await asyncio.sleep(0.2)
        datastore.mark_agent_job_status(job_id, "done")
        datastore.append_agent_job_event(
            job_id, event_type="job_finished", data='{"status":"done"}'
        )

    emitter = asyncio.create_task(emit_after_delay())
    chunks = [chunk async for chunk in event_stream(job_id, after_seq=-1)]
    await emitter
    body = "".join(chunks)
    assert "live" in body
    assert "job_finished" in body
