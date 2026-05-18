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
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from api import datastore
from api.agent import notifier
from api.agent.agent_loop_streaming import run_agent_streaming
from api.agent.global_rate_limit import configure as configure_global_tool_limiter
from api.agent.workspace_resolver import parse_bash_denylist, resolve_workspace
from api.models.anthropic import Message, MessagesRequest
from api.pricing import estimate_token_cost
from config.settings import Settings, get_settings
from core.tools.sse_collector import _iter_events
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
    user_id: str = "default",
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
        user_id=user_id,
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
    started_ms = int(time.time() * 1000)
    # Usage totals accumulated across all turns of this job.
    accumulated_usage: dict[str, int] = {}
    provider_id = ""
    provider_model = ""
    session_id: str | None = None
    project_id: str | None = None
    try:
        job = datastore.get_agent_job(job_id)
        if not job:
            raise RuntimeError(f"job {job_id} vanished after creation")
        payload = json.loads(job["request_json"])
        session_id = job.get("session_id")

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
                # Inject pinned memories as bullet-point context.
                memories = datastore.list_memories(project_id)
                if memories:
                    bullets = "\n".join(f"- {m['content']}" for m in memories)
                    memory_block = (
                        "Memory you must respect for this project:\n" + bullets
                    )
                    current_sys = payload.get("system")
                    if current_sys is None:
                        payload["system"] = memory_block
                    elif isinstance(current_sys, str):
                        payload["system"] = current_sys + "\n\n" + memory_block
                    elif isinstance(current_sys, list):
                        payload["system"] = [
                            *current_sys,
                            {"type": "text", "text": memory_block},
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
            provider_id = routed.resolved.provider_id
            provider_model = routed.resolved.provider_model
            request = routed.request
        except Exception as exc:
            logger.warning("Job {} routing failed: {}", job_id, exc)

        job_user_id = job.get("user_id") or "default"
        workspace = resolve_workspace(request, settings, user_id=job_user_id)
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
            _accumulate_chunk_usage(chunk, accumulated_usage)

        datastore.mark_agent_job_status(job_id, "done")
        datastore.append_agent_job_event(
            job_id, event_type="job_finished", data=json.dumps({"status": "done"})
        )
        _record_job_usage(
            job_id=job_id,
            provider_id=provider_id,
            provider_model=provider_model,
            usage=accumulated_usage,
            started_ms=started_ms,
            session_id=session_id,
            project_id=project_id,
        )
        await _maybe_notify(job_id, settings)
    except asyncio.CancelledError:
        datastore.mark_agent_job_status(job_id, "cancelled")
        datastore.append_agent_job_event(
            job_id, event_type="job_finished", data=json.dumps({"status": "cancelled"})
        )
        _record_job_usage(
            job_id=job_id,
            provider_id=provider_id,
            provider_model=provider_model,
            usage=accumulated_usage,
            started_ms=started_ms,
            session_id=session_id,
            project_id=project_id,
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
        _record_job_usage(
            job_id=job_id,
            provider_id=provider_id,
            provider_model=provider_model,
            usage=accumulated_usage,
            started_ms=started_ms,
            session_id=session_id,
            project_id=project_id,
        )
        await _maybe_notify(job_id, settings)


_SUMMABLE_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    }
)


def _accumulate_chunk_usage(chunk: str, aggregate: dict[str, int]) -> None:
    """Parse SSE chunk and extract usage counters from the final message_delta.

    ``run_agent_streaming`` emits a single synthetic ``message_delta`` at the
    end of the job (via ``_emit_final_lifecycle``) whose ``usage`` field holds
    the complete multi-turn aggregated token counts. We read only that event so
    we don't double-count the per-turn ``message_start`` usage that the
    streaming rewriter also forwards.

    Defensively ignores malformed events or missing fields — never crashes.
    """
    try:
        for event_type, data in _iter_events(chunk):
            if data is None:
                continue
            actual = data.get("type") or event_type
            if actual != "message_delta":
                continue
            usage = data.get("usage")
            if not isinstance(usage, dict):
                continue
            for key in _SUMMABLE_USAGE_KEYS:
                val = usage.get(key)
                if isinstance(val, int):
                    aggregate[key] = aggregate.get(key, 0) + val
    except Exception:
        pass  # never let usage parsing crash the job


def _record_job_usage(
    *,
    job_id: str,
    provider_id: str,
    provider_model: str,
    usage: dict[str, int],
    started_ms: int,
    session_id: str | None,
    project_id: str | None,
) -> None:
    """Persist accumulated usage to the datastore. Best-effort."""
    if not usage:
        return
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    duration_ms = max(0, int(time.time() * 1000) - started_ms)
    cost_usd = 0.0
    if provider_id and provider_model:
        with contextlib.suppress(Exception):
            cost_usd = estimate_token_cost(
                provider=provider_id,
                model=provider_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
    datastore.record_usage(
        kind="chat",
        provider=provider_id or "unknown",
        model=provider_model or "unknown",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        session_id=session_id,
        project_id=project_id,
        request_id=job_id,
        metadata={
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        },
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


async def _maybe_notify(job_id: str, settings: Settings) -> None:
    """Fire the job notifier when configured and the job was long enough."""
    if not settings.slack_webhook_url and not settings.discord_webhook_url:
        return
    job = datastore.get_agent_job(job_id)
    if not job:
        return
    started = job.get("started_at")
    finished = job.get("finished_at")
    if started is None or finished is None:
        return
    duration_s = (finished - started) / 1000.0
    if duration_s < settings.notifier_min_seconds:
        return
    await notifier.notify(job_id)


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
