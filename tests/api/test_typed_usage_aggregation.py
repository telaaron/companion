"""Tests for typed usage aggregation in the streaming agent loop (R5)."""

from __future__ import annotations

from api.agent.agent_loop_streaming import _accumulate_usage


def test_summable_keys_are_summed() -> None:
    agg: dict[str, int | str] = {}
    _accumulate_usage(
        agg, {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2}
    )
    _accumulate_usage(
        agg, {"input_tokens": 7, "output_tokens": 1, "cache_read_input_tokens": 4}
    )
    assert agg["input_tokens"] == 17
    assert agg["output_tokens"] == 6
    assert agg["cache_read_input_tokens"] == 6


def test_unknown_keys_use_last_write_wins() -> None:
    agg: dict[str, str | int] = {}
    _accumulate_usage(agg, {"service_tier": "standard", "input_tokens": 1})
    _accumulate_usage(agg, {"service_tier": "priority", "input_tokens": 1})
    # service_tier is non-summable; latest value wins (no double-counting).
    assert agg["service_tier"] == "priority"
    assert agg["input_tokens"] == 2


def test_non_int_summable_value_falls_back_to_last_write() -> None:
    agg: dict[str, str | int] = {}
    _accumulate_usage(agg, {"input_tokens": "abc"})
    assert agg["input_tokens"] == "abc"
