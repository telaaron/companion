"""Background agent jobs — decouple the agent loop from the client connection.

Architecture:

* A POST starts an :class:`AgentJob` which spawns an ``asyncio.Task`` that
  runs :func:`run_agent_streaming` to completion. SSE chunks are persisted
  into ``agent_job_events`` one row per chunk.
* Clients consume the result via :func:`event_stream` (an async generator)
  which first replays all stored events (``seq > after_seq``) and then tails
  new ones as they get appended. If the client disconnects the job keeps
  running; reconnect with ``Last-Event-ID`` resumes from the last seq.
* This is the antidote to "tab closed → request cancelled" — the job lifetime
  is server-owned, not browser-owned.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from api import datastore
from api.agent.agent_loop_streaming import run_agent_streaming
from api.agent.global_rate_limit import configure as configure_global_tool_limiter
from api.agent.workspace_resolver import parse_bash_denylist, resolve_workspace
from api.models.anthropic import Message, MessagesRequest
from config.settings import Settings, get_settings
from providers.base import BaseProvider

# Active tasks keyed by job_id — kept so we can cancel from /v1/jobs/{id}/cancel.
_ACTIVE_TASKS: dict[str, asyncio.Task[None]] = {}
_TAIL_POLL_SECONDS = 0.25
_TAIL_IDLE_TIMEOUT_S = 600  # how long to wait for new events after the job is done


def start_job(
    *,
    session_id: str | None,
    project_id: str | None,
    model: str,
    messages: list[dict[str, Any]],
    system: str | list[dict[str, Any]] | None,
    max_tokens: int,
    provider_getter,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a job row + spawn the background task. Returns the persisted row."""
    request_payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "system": system,
        "metadata": metadata or {},
    }
    job = datastore.create_agent_job(
        session_id=session_id,
        project_id=project_id,
        model=model,
        request_payload=request_payload,
        metadata=metadata,
    )
    job_id = job["id"]
    settings = get_settings()
    # Spawn into the running event loop. Caller must be inside FastAPI's loop.
    task = asyncio.create_task(
        _run_job(job_id, settings=settings, provider_getter=provider_getter),
        name=f"agent-job-{job_id}",
    )
    _ACTIVE_TASKS[job_id] = task
    task.add_done_callback(lambda _t: _ACTIVE_TASKS.pop(job_id, None))
    return job


async def _run_job(job_id: str, *, settings: Settings, provider_getter) -> None:
    """Job lifecycle. Persists every SSE chunk + a terminal marker."""
    datastore.mark_agent_job_status(job_id, "running")
    datastore.append_agent_job_event(
        job_id, event_type="job_started", data=json.dumps({"ts": int(time.time())})
    )
    try:
        job = datastore.get_agent_job(job_id)
        if not job:
            raise RuntimeError(f"job {job_id} vanished after creation")
        payload = json.loads(job["request_json"])

        # Inject project shared_context as the system prompt when the job is
        # scoped to a project. This is what makes the project's brief actually
        # reach the model — the UI used to send it as nothing.
        project_id = job.get("project_id") or (payload.get("metadata") or {}).get(
            "project_id"
        )
        if project_id:
            project = datastore.get_project(project_id)
            if project:
                shared = (project.get("shared_context") or "").strip()
                if shared:
                    existing = payload.get("system")
                    if existing is None:
                        payload["system"] = shared
                    elif isinstance(existing, str):
                        payload["system"] = shared + "\n\n" + existing
                    elif isinstance(existing, list):
                        payload["system"] = [
                            {"type": "text", "text": shared},
                            *existing,
                        ]

        request = _build_messages_request(payload)

        # Route through the model_router so providers see their native model
        # name (e.g. ``deepseek-v4-flash``), not the gateway-prefixed form
        # (``deepseek/deepseek-v4-flash``). Without this DeepSeek rejects with
        # "Invalid request sent to provider".
        from api.model_router import ModelRouter

        router = ModelRouter(settings)
        try:
            routed = router.resolve_messages_request(request)
            request = routed.request
        except Exception as exc:
            logger.warning("Job {} routing failed: {}", job_id, exc)

        workspace = resolve_workspace(request, settings)
        denylist = parse_bash_denylist(settings.agent_bash_denylist)
        extra_env = parse_bash_denylist(settings.agent_bash_extra_env)
        global_limiter = configure_global_tool_limiter(
            settings.agent_global_tool_call_limit_per_minute
        )

        provider: BaseProvider = provider_getter()
        provider.preflight_stream(request)

        async for chunk in run_agent_streaming(
            request,
            provider,
            workspace,
            max_turns=settings.agent_max_turns,
            request_id=job_id,
            bash_denylist=denylist,
            bash_extra_env_allowlist=extra_env,
            tool_call_limit_per_minute=settings.agent_tool_call_limit_per_minute,
            global_limiter=global_limiter,
        ):
            datastore.append_agent_job_event(job_id, event_type="sse", data=chunk)

        datastore.mark_agent_job_status(job_id, "done")
        datastore.append_agent_job_event(
            job_id, event_type="job_finished", data=json.dumps({"status": "done"})
        )
    except asyncio.CancelledError:
        datastore.mark_agent_job_status(job_id, "cancelled")
        datastore.append_agent_job_event(
            job_id, event_type="job_finished", data=json.dumps({"status": "cancelled"})
        )
        raise
    except Exception as exc:
        logger.exception("Agent job {} failed: {}", job_id, exc)
        datastore.mark_agent_job_status(job_id, "error", error=str(exc))
        datastore.append_agent_job_event(
            job_id,
            event_type="job_error",
            data=json.dumps({"error": str(exc), "type": type(exc).__name__}),
        )


def _build_messages_request(payload: dict[str, Any]) -> MessagesRequest:
    messages = payload.get("messages") or []
    msg_objs: list[Message] = [Message(**m) for m in messages if isinstance(m, dict)]
    return MessagesRequest(
        model=payload.get("model") or "",
        max_tokens=payload.get("max_tokens") or 1024,
        messages=msg_objs,
        system=payload.get("system"),
        metadata=payload.get("metadata") or None,
    )


def cancel_job(job_id: str) -> bool:
    """Cancel a running job. Returns True if the task was present + cancelled."""
    task = _ACTIVE_TASKS.get(job_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


async def event_stream(job_id: str, *, after_seq: int = -1) -> AsyncIterator[str]:
    """Yield raw SSE chunks for ``job_id`` — replay then tail until job ends.

    Emits both stored SSE events and a sentinel ``: heartbeat`` comment every
    few seconds when idle, so reverse proxies don't time out the connection.
    Closes when the job reaches a terminal status AND we've drained all events.
    """
    last_seq = after_seq
    idle_since = time.monotonic()
    while True:
        batch = datastore.list_agent_job_events(job_id, after_seq=last_seq)
        if batch:
            idle_since = time.monotonic()
            for ev in batch:
                last_seq = ev["seq"]
                yield _format_outbound_event(ev)
        else:
            yield ": heartbeat\n\n"
            job = datastore.get_agent_job(job_id)
            terminal_statuses = ("done", "error", "cancelled")
            if job and job.get("status") in terminal_statuses:
                # Job finished and no more events queued — close the stream.
                latest = datastore.latest_agent_job_seq(job_id)
                if latest <= last_seq:
                    return
            if time.monotonic() - idle_since > _TAIL_IDLE_TIMEOUT_S:
                # Safety: never tail forever if we somehow lose the terminal marker.
                return
        await asyncio.sleep(_TAIL_POLL_SECONDS)


def _format_outbound_event(ev: dict[str, Any]) -> str:
    """Wrap a stored event row into an SSE event with id+event+data fields.

    For ``event_type == 'sse'`` the stored ``data`` is a raw upstream SSE chunk
    (already includes ``event: ...\\ndata: ...\\n\\n``). We prepend an ``id:``
    line so the client's ``EventSource`` can resume with ``Last-Event-ID``.

    For lifecycle events we synthesise an SSE event explicitly.
    """
    seq = ev["seq"]
    if ev["event_type"] == "sse":
        body = ev["data"] or ""
        return f"id: {seq}\n{body}"
    payload = ev["data"] or "{}"
    return f"id: {seq}\nevent: {ev['event_type']}\ndata: {payload}\n\n"
