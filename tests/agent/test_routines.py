"""Tests for api/agent/routines.py and the routines datastore helpers.

Tests are deliberately synchronous where possible so they don't require
a running event loop — only the RoutineScheduler._tick() path needs async.

Monkeypatches time.time() and croniter to control tick behavior.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path):
    """Return a fresh datastore module backed by a temp SQLite file."""
    from api import datastore

    db_file = tmp_path / "test_routines.sqlite3"
    datastore.reset_for_tests(db_file)
    return datastore


# ---------------------------------------------------------------------------
# Datastore helper tests
# ---------------------------------------------------------------------------


class TestRoutineDatastoreHelpers:
    def test_create_and_get_routine(self, tmp_path):
        ds = _make_db(tmp_path)
        r = ds.create_routine(
            name="daily journal",
            trigger_type="cron",
            trigger_config={"expression": "0 9 * * *", "tz": "Europe/Berlin"},
            payload={
                "model": "deepseek/deepseek-v4-flash",
                "messages": [{"role": "user", "content": "Run the morning journal."}],
            },
        )
        assert r["id"].startswith("rtn_")
        assert r["name"] == "daily journal"
        assert r["trigger_type"] == "cron"
        assert r["enabled"] == 1

        got = ds.get_routine(r["id"])
        assert got is not None
        assert got["id"] == r["id"]

    def test_list_routines_filters_by_user(self, tmp_path):
        ds = _make_db(tmp_path)
        ds.create_routine(name="r1", user_id="alice")
        ds.create_routine(name="r2", user_id="bob")
        ds.create_routine(name="r3", user_id="alice")

        alice = ds.list_routines(user_id="alice")
        assert len(alice) == 2
        assert all(r["user_id"] == "alice" for r in alice)

    def test_list_routines_enabled_only(self, tmp_path):
        ds = _make_db(tmp_path)
        ds.create_routine(name="active", enabled=True)
        ds.create_routine(name="inactive", enabled=False)

        enabled = ds.list_routines(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0]["name"] == "active"

    def test_list_routines_due_before_ms(self, tmp_path):
        ds = _make_db(tmp_path)
        past_ms = int(time.time() * 1000) - 60_000
        future_ms = int(time.time() * 1000) + 60_000
        now_ms = int(time.time() * 1000)

        ds.create_routine(name="past", enabled=True, next_run_ms=past_ms)
        ds.create_routine(name="future", enabled=True, next_run_ms=future_ms)

        due = ds.list_routines(enabled_only=True, due_before_ms=now_ms)
        assert len(due) == 1
        assert due[0]["name"] == "past"

    def test_update_routine(self, tmp_path):
        ds = _make_db(tmp_path)
        r = ds.create_routine(name="orig")
        ds.update_routine(r["id"], name="updated", enabled=False)
        updated = ds.get_routine(r["id"])
        assert updated is not None
        assert updated["name"] == "updated"
        assert updated["enabled"] == 0

    def test_delete_routine(self, tmp_path):
        ds = _make_db(tmp_path)
        r = ds.create_routine(name="todelete")
        ds.delete_routine(r["id"])
        assert ds.get_routine(r["id"]) is None

    def test_record_routine_run(self, tmp_path):
        ds = _make_db(tmp_path)
        r = ds.create_routine(name="r")
        run = ds.record_routine_run(routine_id=r["id"], status="pending")
        assert run["id"].startswith("rrun_")
        assert run["routine_id"] == r["id"]
        assert run["status"] == "pending"

    def test_update_routine_run(self, tmp_path):
        ds = _make_db(tmp_path)
        r = ds.create_routine(name="r")
        run = ds.record_routine_run(routine_id=r["id"], status="pending")
        ds.update_routine_run(run["id"], status="running", job_id="job_abc")
        # The update helper doesn't return a value — verify via a fresh query.
        from api import datastore

        with datastore._connect() as conn:
            row = conn.execute(
                "SELECT * FROM routine_runs WHERE id=?", (run["id"],)
            ).fetchone()
        assert row is not None
        d = dict(row)
        assert d["status"] == "running"
        assert d["job_id"] == "job_abc"
        # A 'running' update must NOT stamp finished_at (the perpetual-running
        # bug); finish time only lands on a terminal status.
        assert d["finished_at"] is None

    def test_update_routine_run_terminal_stamps_finished(self, tmp_path):
        ds = _make_db(tmp_path)
        r = ds.create_routine(name="r")
        run = ds.record_routine_run(routine_id=r["id"], status="pending")
        ds.update_routine_run(run["id"], status="running", job_id="job_x")
        ds.update_routine_run(run["id"], status="done")
        from api import datastore

        with datastore._connect() as conn:
            d = dict(
                conn.execute(
                    "SELECT * FROM routine_runs WHERE id=?", (run["id"],)
                ).fetchone()
            )
        assert d["status"] == "done"
        assert d["finished_at"] is not None

    def test_sync_routine_run_for_job(self, tmp_path):
        ds = _make_db(tmp_path)
        r = ds.create_routine(name="r")
        run = ds.record_routine_run(routine_id=r["id"], status="pending")
        ds.update_routine_run(run["id"], status="running", job_id="job_sync")
        # Job finishes → mirror onto the routine_run.
        ds.sync_routine_run_for_job("job_sync", "done")
        from api import datastore

        with datastore._connect() as conn:
            d = dict(
                conn.execute(
                    "SELECT * FROM routine_runs WHERE id=?", (run["id"],)
                ).fetchone()
            )
        assert d["status"] == "done"
        assert d["finished_at"] is not None
        # Non-terminal status is a no-op.
        ds.sync_routine_run_for_job("job_sync", "running")
        with datastore._connect() as conn:
            d2 = dict(
                conn.execute(
                    "SELECT status FROM routine_runs WHERE id=?", (run["id"],)
                ).fetchone()
            )
        assert d2["status"] == "done"


# ---------------------------------------------------------------------------
# RoutineScheduler tick tests
# ---------------------------------------------------------------------------


class TestRoutineSchedulerTick:
    """Test the scheduler tick fires routines that are due and advances next_run_ms."""

    def _make_overdue_routine(self, ds: Any) -> dict[str, Any]:
        """Create a cron routine whose next_run_ms is one minute in the past."""
        past_ms = int(time.time() * 1000) - 60_000
        return ds.create_routine(
            name="overdue",
            trigger_type="cron",
            trigger_config={"expression": "* * * * *", "tz": "UTC"},
            payload={
                "model": "deepseek/deepseek-v4-flash",
                "messages": [{"role": "user", "content": "tick"}],
            },
            enabled=True,
            next_run_ms=past_ms,
        )

    @pytest.mark.asyncio
    async def test_tick_fires_due_routine_and_creates_job(self, tmp_path):
        """A routine with next_run_ms in the past must produce a job row after tick."""
        ds = _make_db(tmp_path)
        routine = self._make_overdue_routine(ds)
        rid = routine["id"]

        from api.agent.routines import RoutineScheduler

        scheduler = RoutineScheduler()
        await scheduler._tick()

        # A job row should have been created.
        jobs = ds.list_agent_jobs()
        assert len(jobs) >= 1
        job = jobs[0]
        meta = json.loads(job.get("metadata") or "{}")
        assert meta.get("source") == f"routine:{rid}"
        assert meta.get("routine_id") == rid

    @pytest.mark.asyncio
    async def test_tick_advances_next_run_ms(self, tmp_path):
        """After firing, next_run_ms must be in the future."""
        ds = _make_db(tmp_path)
        routine = self._make_overdue_routine(ds)
        rid = routine["id"]

        now_ms = int(time.time() * 1000)

        from api.agent.routines import RoutineScheduler

        scheduler = RoutineScheduler()
        await scheduler._tick()

        updated = ds.get_routine(rid)
        assert updated is not None
        assert updated["last_run_ms"] is not None
        assert updated["next_run_ms"] is not None
        assert updated["next_run_ms"] > now_ms

    @pytest.mark.asyncio
    async def test_tick_does_not_fire_future_routine(self, tmp_path):
        """A routine with next_run_ms in the future must NOT be fired."""
        ds = _make_db(tmp_path)
        future_ms = int(time.time() * 1000) + 600_000  # 10 minutes ahead
        ds.create_routine(
            name="future",
            trigger_type="cron",
            trigger_config={"expression": "0 3 * * *", "tz": "UTC"},
            payload={"model": "deepseek/deepseek-v4-flash", "messages": []},
            enabled=True,
            next_run_ms=future_ms,
        )

        from api.agent.routines import RoutineScheduler

        scheduler = RoutineScheduler()
        await scheduler._tick()

        jobs = ds.list_agent_jobs()
        assert len(jobs) == 0

    @pytest.mark.asyncio
    async def test_tick_skips_disabled_routine(self, tmp_path):
        """Disabled routines must never fire, even when past-due."""
        ds = _make_db(tmp_path)
        past_ms = int(time.time() * 1000) - 60_000
        ds.create_routine(
            name="disabled",
            trigger_type="cron",
            trigger_config={"expression": "* * * * *", "tz": "UTC"},
            payload={"model": "deepseek/deepseek-v4-flash", "messages": []},
            enabled=False,
            next_run_ms=past_ms,
        )

        from api.agent.routines import RoutineScheduler

        scheduler = RoutineScheduler()
        await scheduler._tick()

        assert len(ds.list_agent_jobs()) == 0


# ---------------------------------------------------------------------------
# trigger_routine_now tests
# ---------------------------------------------------------------------------


class TestTriggerRoutineNow:
    @pytest.mark.asyncio
    async def test_trigger_now_enqueues_job(self, tmp_path):
        ds = _make_db(tmp_path)
        r = ds.create_routine(
            name="manual",
            trigger_type="manual",
            payload={
                "model": "deepseek/deepseek-v4-flash",
                "messages": [{"role": "user", "content": "go"}],
            },
        )

        from api.agent.routines import trigger_routine_now

        job = await trigger_routine_now(r["id"])
        assert job["id"].startswith("job_")
        meta = json.loads(job.get("metadata") or "{}")
        assert meta.get("source") == f"routine:{r['id']}"

    @pytest.mark.asyncio
    async def test_trigger_now_raises_for_missing_routine(self, tmp_path):
        _make_db(tmp_path)
        from api.agent.routines import trigger_routine_now

        with pytest.raises(LookupError):
            await trigger_routine_now("rtn_doesnotexist")


# ---------------------------------------------------------------------------
# compute_next_run_ms helper tests
# ---------------------------------------------------------------------------


class TestComputeNextRunMs:
    def test_returns_future_timestamp(self):
        from api.agent.routines import _compute_next_run_ms

        now_ms = int(time.time() * 1000)
        next_ms = _compute_next_run_ms("* * * * *", "UTC", now_ms)
        assert next_ms > now_ms

    def test_honours_tz(self):
        from api.agent.routines import _compute_next_run_ms

        now_ms = int(time.time() * 1000)
        utc_next = _compute_next_run_ms("0 9 * * *", "UTC", now_ms)
        berlin_next = _compute_next_run_ms("0 9 * * *", "Europe/Berlin", now_ms)
        # Berlin is UTC+1 or UTC+2 — the two values must differ.
        assert utc_next != berlin_next
