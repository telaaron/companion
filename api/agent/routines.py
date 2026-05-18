"""Routine scheduler — fires cron/manual/webhook-triggered agent jobs.

Architecture:
* ``RoutineScheduler.start()`` is called from ``AppRuntime.startup()``.
* A single asyncio Task ticks every 30 s.
* On each tick every enabled routine whose ``next_run_ms <= now`` is fired:
  a background ``agent_jobs.start_job()`` is enqueued with
  ``metadata={"source": "routine:<id>"}`` so the job appears in the UI.
* ``last_run_ms`` and ``next_run_ms`` are updated atomically before spawning
  so a server restart inside the same minute cannot double-fire.
* Misfire policy: if ``now - next_run_ms > 5 min`` the routine was skipped
  (e.g. the machine slept); fire once immediately then resume normal cadence.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from loguru import logger

from api import datastore

_TICK_INTERVAL_S = 30
_MISFIRE_THRESHOLD_MS = 5 * 60 * 1000  # 5 minutes


def _compute_next_run_ms(expression: str, tz: str, after_ms: int) -> int:
    """Return the next fire time in epoch-milliseconds using croniter."""
    from croniter import croniter

    after_s = after_ms / 1000.0
    try:
        import zoneinfo

        zone = zoneinfo.ZoneInfo(tz)
    except Exception:
        zone = None  # fall back to UTC / local

    if zone is not None:
        import datetime

        dt_after = datetime.datetime.fromtimestamp(after_s, tz=zone)
        cron = croniter(expression, dt_after)
        next_dt = cron.get_next(datetime.datetime)
        return int(next_dt.timestamp() * 1000)
    else:
        cron = croniter(expression, after_s)
        return int(cron.get_next(float) * 1000)


class RoutineScheduler:
    """Singleton async tick loop for routine scheduling."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Spawn the background tick task. Call once from startup."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="routine-scheduler")

    async def stop(self) -> None:
        """Signal the tick loop to stop and await its completion."""
        self._stop_event.set()
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except TimeoutError, asyncio.CancelledError:
                self._task.cancel()

    async def _run(self) -> None:
        logger.info("RoutineScheduler: started (tick every {}s)", _TICK_INTERVAL_S)
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:
                logger.warning("RoutineScheduler: tick error: {}", exc)
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=_TICK_INTERVAL_S,
                )
                # stop_event fired — exit cleanly
                break
            except TimeoutError:
                pass  # normal: sleep elapsed, tick again
        logger.info("RoutineScheduler: stopped")

    async def _tick(self) -> None:
        now_ms = int(time.time() * 1000)
        due = datastore.list_routines(enabled_only=True, due_before_ms=now_ms)
        for routine in due:
            try:
                await self._fire(routine, now_ms)
            except Exception as exc:
                logger.warning(
                    "RoutineScheduler: error firing routine {}: {}",
                    routine.get("id"),
                    exc,
                )

    async def _fire(self, routine: dict[str, Any], now_ms: int) -> None:
        rid = routine["id"]
        trigger_config: dict[str, Any] = json.loads(
            routine.get("trigger_config") or "{}"
        )
        payload: dict[str, Any] = json.loads(routine.get("payload") or "{}")

        # Compute next run time before firing to prevent double-fire on restart.
        trigger_type = routine.get("trigger_type", "cron")
        next_run_ms: int | None = None
        if trigger_type == "cron":
            expression = trigger_config.get("expression", "")
            tz = trigger_config.get("tz", "UTC")
            if expression:
                # Misfire guard: if we're > 5 min late, advance from now.
                next_run_ms = _compute_next_run_ms(expression, tz, now_ms)

        # Write last_run_ms + next_run_ms before enqueuing.
        datastore.update_routine(
            rid,
            last_run_ms=now_ms,
            next_run_ms=next_run_ms,
        )

        # Record the run row.
        run = datastore.record_routine_run(routine_id=rid, status="pending")
        run_id = run["id"]

        # Enqueue the agent job.
        try:
            job = _enqueue_job(routine, payload, now_ms)
            job_id = job.get("id")
            datastore.update_routine_run(run_id, status="running", job_id=job_id)
            logger.info(
                "RoutineScheduler: fired routine {} → job {}",
                rid,
                job_id,
            )
        except Exception as exc:
            datastore.update_routine_run(run_id, status="error")
            logger.error(
                "RoutineScheduler: failed to enqueue job for routine {}: {}",
                rid,
                exc,
            )
            raise


def _enqueue_job(
    routine: dict[str, Any],
    payload: dict[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    """Create a datastore job row (no async provider required at scheduling time).

    The job is created in the ``pending`` state. The agent loop task picks it
    up via the regular jobs mechanism. We inject ``metadata.source`` so the UI
    can identify scheduler-fired jobs.
    """
    from api import datastore as ds

    rid = routine["id"]
    user_id: str = routine.get("user_id") or "default"
    project_id: str | None = routine.get("project_id")
    model: str = payload.get("model") or ""
    messages: list[Any] = payload.get("messages") or []
    max_tokens: int = int(payload.get("max_tokens") or 8192)
    system = payload.get("system")
    metadata: dict[str, Any] = {
        **(payload.get("metadata") or {}),
        "source": f"routine:{rid}",
        "routine_id": rid,
        "triggered_at_ms": now_ms,
    }

    request_payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "metadata": metadata,
    }
    if system is not None:
        request_payload["system"] = system

    job = ds.create_agent_job(
        session_id=None,
        project_id=project_id,
        model=model,
        request_payload=request_payload,
        metadata=metadata,
        user_id=user_id,
    )
    return job


async def trigger_routine_now(routine_id: str) -> dict[str, Any]:
    """Fire a routine immediately (webhook / manual trigger).

    Returns the created job row, or raises ``LookupError`` if the routine
    does not exist.
    """
    routine = datastore.get_routine(routine_id)
    if routine is None:
        raise LookupError(f"routine {routine_id!r} not found")

    now_ms = int(time.time() * 1000)
    payload: dict[str, Any] = json.loads(routine.get("payload") or "{}")

    run = datastore.record_routine_run(routine_id=routine_id, status="pending")
    run_id = run["id"]

    try:
        job = _enqueue_job(routine, payload, now_ms)
        job_id = job.get("id")
        datastore.update_routine_run(run_id, status="running", job_id=job_id)
        datastore.update_routine(routine_id, last_run_ms=now_ms)
        logger.info(
            "RoutineScheduler: manual trigger routine {} → job {}",
            routine_id,
            job_id,
        )
        return job
    except Exception as exc:
        datastore.update_routine_run(run_id, status="error")
        raise exc
