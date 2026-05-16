"""Tests for per-job usage recording.

Verifies that ``_run_job`` accumulates token counts from SSE chunks emitted
by the streaming agent loop and writes a ``usage_events`` row after the job
completes. Also covers the helper functions directly.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from api import datastore
from api.agent.jobs import _accumulate_chunk_usage, _record_job_usage, start_job

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the datastore at a throwaway SQLite file for this test."""
    datastore.reset_for_tests(tmp_path / "usage_test.sqlite")
    yield
    datastore.reset_for_tests()


# ---------------------------------------------------------------------------
# Unit tests for _accumulate_chunk_usage
# ---------------------------------------------------------------------------


def _sse_chunk(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def test_accumulate_message_start_usage_ignored() -> None:
    """message_start usage is intentionally ignored to avoid double-counting.

    run_agent_streaming emits one final message_delta that already contains
    the complete multi-turn sum, so we only read message_delta events.
    """
    chunk = _sse_chunk(
        "message_start",
        {
            "type": "message_start",
            "message": {"id": "msg_1", "usage": {"input_tokens": 100}},
        },
    )
    agg: dict[str, int] = {}
    _accumulate_chunk_usage(chunk, agg)
    assert agg == {}  # message_start is NOT counted


def test_accumulate_message_delta_usage() -> None:
    chunk = _sse_chunk(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 50},
        },
    )
    agg: dict[str, int] = {}
    _accumulate_chunk_usage(chunk, agg)
    assert agg["output_tokens"] == 50


def test_accumulate_sums_across_multiple_message_delta_chunks() -> None:
    """Multiple message_delta chunks are summed together."""
    chunk1 = _sse_chunk(
        "message_delta",
        {"type": "message_delta", "delta": {}, "usage": {"input_tokens": 40}},
    )
    chunk2 = _sse_chunk(
        "message_delta",
        {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 30}},
    )
    agg: dict[str, int] = {}
    _accumulate_chunk_usage(chunk1, agg)
    _accumulate_chunk_usage(chunk2, agg)
    assert agg["input_tokens"] == 40
    assert agg["output_tokens"] == 30


def test_accumulate_reads_final_message_delta_only() -> None:
    """The final synthetic message_delta contains the complete aggregated sum.

    run_agent_streaming calls _emit_final_lifecycle which emits one
    message_delta with the full usage. message_start events are ignored.
    """
    agg: dict[str, int] = {}
    # Per-turn message_start events are ignored.
    for _ in range(3):
        chunk = _sse_chunk(
            "message_start",
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        )
        _accumulate_chunk_usage(chunk, agg)
    # Final synthetic message_delta with full aggregate.
    final = _sse_chunk(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"input_tokens": 30, "output_tokens": 15},
        },
    )
    _accumulate_chunk_usage(final, agg)
    assert agg["input_tokens"] == 30
    assert agg["output_tokens"] == 15


def test_accumulate_ignores_non_usage_chunks() -> None:
    chunk = _sse_chunk(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hello"},
        },
    )
    agg: dict[str, int] = {}
    _accumulate_chunk_usage(chunk, agg)
    assert agg == {}


def test_accumulate_is_defensive_against_malformed_chunk() -> None:
    agg: dict[str, int] = {}
    _accumulate_chunk_usage("not valid SSE", agg)
    _accumulate_chunk_usage("", agg)
    assert agg == {}


def test_accumulate_cache_tokens() -> None:
    chunk = _sse_chunk(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {},
            "usage": {
                "input_tokens": 20,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 3,
            },
        },
    )
    agg: dict[str, int] = {}
    _accumulate_chunk_usage(chunk, agg)
    assert agg["input_tokens"] == 20
    assert agg["cache_creation_input_tokens"] == 5
    assert agg["cache_read_input_tokens"] == 3


# ---------------------------------------------------------------------------
# Unit tests for _record_job_usage
# ---------------------------------------------------------------------------


def test_record_job_usage_writes_row(fresh_db: None) -> None:
    session = datastore.upsert_session(title="test")
    sid = session["id"]
    _record_job_usage(
        job_id="job_test_123",
        provider_id="deepseek",
        provider_model="deepseek-v4-flash",
        usage={"input_tokens": 200, "output_tokens": 80},
        started_ms=0,
        session_id=sid,
        project_id=None,
    )
    agg = datastore.session_usage(sid)
    assert agg["input_tokens"] == 200
    assert agg["output_tokens"] == 80
    assert agg["events"] == 1


def test_record_job_usage_no_op_when_empty(fresh_db: None) -> None:
    """Empty usage dict must not write a row (nothing to record)."""
    session = datastore.upsert_session(title="test2")
    sid = session["id"]
    _record_job_usage(
        job_id="job_empty",
        provider_id="deepseek",
        provider_model="deepseek-v4-flash",
        usage={},
        started_ms=0,
        session_id=sid,
        project_id=None,
    )
    agg = datastore.session_usage(sid)
    assert agg["events"] == 0


def test_record_job_usage_cost_is_nonzero_for_known_model(fresh_db: None) -> None:
    """deepseek-v4-flash has a known price, so cost_usd should be > 0."""
    session = datastore.upsert_session(title="cost_test")
    sid = session["id"]
    _record_job_usage(
        job_id="job_cost",
        provider_id="deepseek",
        provider_model="deepseek-v4-flash",
        usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        started_ms=0,
        session_id=sid,
        project_id=None,
    )
    agg = datastore.session_usage(sid)
    # cost_usd is a float aggregate — just check it is positive
    assert agg["cost_usd"] > 0.0


# ---------------------------------------------------------------------------
# Integration test: full job run populates usage
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Minimal streaming provider that emits a single text turn with usage."""

    def preflight_stream(self, *_: Any, **__: Any) -> None:
        return None

    def stream_response(
        self,
        request: Any,
        input_tokens: int = 0,
        *,
        request_id: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> AsyncIterator[str]:
        chunks = [
            _sse_chunk(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_test",
                        "model": "x",
                        "usage": {"input_tokens": 150},
                    },
                },
            ),
            _sse_chunk(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            _sse_chunk(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Hello!"},
                },
            ),
            _sse_chunk(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            ),
            _sse_chunk(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 60},
                },
            ),
            _sse_chunk("message_stop", {"type": "message_stop"}),
        ]

        async def _gen() -> AsyncIterator[str]:
            for c in chunks:
                yield c

        return _gen()


@pytest.mark.asyncio
async def test_job_run_records_usage(
    fresh_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run a full job via start_job and assert usage row appears in the store."""
    from api.model_router import ModelRouter, ResolvedModel, RoutedMessagesRequest
    from api.models.anthropic import MessagesRequest

    # Create a session so we can query session_usage later.
    session = datastore.upsert_session(title="integration_test")
    sid = session["id"]

    fake_provider = _FakeProvider()

    # Patch ModelRouter so we get a known provider_id / provider_model.
    class _PinnedRouter(ModelRouter):
        def resolve_messages_request(
            self, request: MessagesRequest
        ) -> RoutedMessagesRequest:
            resolved = ResolvedModel(
                original_model=request.model,
                provider_id="deepseek",
                provider_model="deepseek-v4-flash",
                provider_model_ref="deepseek/deepseek-v4-flash",
                thinking_enabled=False,
            )
            routed = request.model_copy(deep=True)
            routed.model = resolved.provider_model
            return RoutedMessagesRequest(request=routed, resolved=resolved)

    # The local import in _run_job does ``from api.model_router import ModelRouter``,
    # so we patch the source module, not the jobs module.
    monkeypatch.setattr(
        "api.model_router.ModelRouter",
        lambda _settings: _PinnedRouter(_settings),
    )

    job = start_job(
        session_id=sid,
        project_id=None,
        model="deepseek/deepseek-v4-flash",
        messages=[{"role": "user", "content": "hello"}],
        system=None,
        max_tokens=256,
        provider_getter=lambda: fake_provider,
        metadata={},
    )
    job_id = job["id"]

    # Wait for the background task to finish.
    deadline = asyncio.get_event_loop().time() + 10.0
    while asyncio.get_event_loop().time() < deadline:
        row = datastore.get_agent_job(job_id)
        if row and row["status"] in ("done", "error", "cancelled"):
            break
        await asyncio.sleep(0.05)

    assert row is not None
    assert row["status"] == "done", f"Job ended with status {row['status']!r}"

    agg = datastore.session_usage(sid)
    assert agg["input_tokens"] == 150, f"Expected 150 input_tokens, got {agg}"
    assert agg["output_tokens"] == 60, f"Expected 60 output_tokens, got {agg}"
    assert agg["events"] == 1
