"""Tests for api/agent/insights.py — insights computation.

Seeds audit_log + usage_events via the datastore helpers, then asserts
that compute_insights returns the expected shape and values.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path):
    """Return a fresh datastore backed by a temp SQLite file."""
    from api import datastore

    db_file = tmp_path / "test_insights.sqlite3"
    datastore.reset_for_tests(db_file)
    return datastore


# ---------------------------------------------------------------------------
# Shape tests
# ---------------------------------------------------------------------------


class TestComputeInsightsShape:
    """compute_insights always returns the three required keys."""

    def test_empty_db_returns_correct_shape(self, tmp_path):
        _make_db(tmp_path)
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result = compute_insights(since_ts=0, user_id="default")
        assert "tools" in result
        assert "providers" in result
        assert "suggestions" in result
        assert isinstance(result["tools"], list)
        assert isinstance(result["providers"], list)
        assert isinstance(result["suggestions"], list)

    def test_tools_item_shape(self, tmp_path):
        ds = _make_db(tmp_path)
        # Seed a tool-call audit event
        ds.record_audit(
            category="tool",
            event="call",
            detail="Bash",
            metadata={"duration_ms": 120, "error": ""},
            user_id="default",
        )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result = compute_insights(since_ts=0, user_id="default")
        assert len(result["tools"]) == 1
        t = result["tools"][0]
        assert t["name"] == "Bash"
        assert t["calls"] == 1
        assert isinstance(t["avg_ms"], int)
        assert isinstance(t["errors"], int)

    def test_providers_item_shape(self, tmp_path):
        ds = _make_db(tmp_path)
        ds.record_usage(
            kind="chat",
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            user_id="default",
        )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result = compute_insights(since_ts=0, user_id="default")
        assert len(result["providers"]) == 1
        p = result["providers"][0]
        assert p["provider"] == "deepseek"
        assert p["cost_usd"] >= 0.0
        assert p["tokens"] == 150

    def test_suggestions_item_shape(self, tmp_path):
        """When conditions are met, suggestion has id/title/body/action keys."""
        ds = _make_db(tmp_path)
        # Seed a tool with > 10% error rate (5 calls, 1 error)
        for _ in range(4):
            ds.record_audit(
                category="tool",
                event="call",
                detail="BadTool",
                metadata={"duration_ms": 50, "error": ""},
                user_id="default",
            )
        ds.record_audit(
            category="tool",
            event="call",
            detail="BadTool",
            metadata={"duration_ms": 50, "error": "something went wrong"},
            user_id="default",
        )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result = compute_insights(since_ts=0, user_id="default")
        # Should have at least one suggestion for the high error rate
        assert len(result["suggestions"]) >= 1
        s = result["suggestions"][0]
        assert "id" in s
        assert "title" in s
        assert "body" in s
        assert "action" in s


# ---------------------------------------------------------------------------
# User isolation tests
# ---------------------------------------------------------------------------


class TestUserIsolation:
    """Data scoped to user_a must not appear under user_b."""

    def test_tool_stats_scoped_by_user(self, tmp_path):
        ds = _make_db(tmp_path)
        ds.record_audit(
            category="tool",
            event="call",
            detail="MyTool",
            metadata={"duration_ms": 10},
            user_id="user_a",
        )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result_a = compute_insights(since_ts=0, user_id="user_a")
        result_b = compute_insights(since_ts=0, user_id="user_b")
        assert any(t["name"] == "MyTool" for t in result_a["tools"])
        assert not any(t["name"] == "MyTool" for t in result_b["tools"])

    def test_provider_stats_scoped_by_user(self, tmp_path):
        ds = _make_db(tmp_path)
        ds.record_usage(
            kind="chat",
            provider="openrouter",
            model="openrouter/some-model",
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.01,
            user_id="user_a",
        )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result_a = compute_insights(since_ts=0, user_id="user_a")
        result_b = compute_insights(since_ts=0, user_id="user_b")
        assert any(p["provider"] == "openrouter" for p in result_a["providers"])
        assert not any(p["provider"] == "openrouter" for p in result_b["providers"])


# ---------------------------------------------------------------------------
# Suggestion heuristics
# ---------------------------------------------------------------------------


class TestSuggestionHeuristics:
    def test_provider_cost_suggestion_triggered(self, tmp_path):
        """Two providers configured; most expensive exceeds $5 → suggestion."""
        ds = _make_db(tmp_path)
        ds.record_usage(
            kind="chat",
            provider="expensive",
            model="expensive/v1",
            input_tokens=10000,
            output_tokens=5000,
            cost_usd=6.0,
            user_id="default",
        )
        ds.record_usage(
            kind="chat",
            provider="cheap",
            model="cheap/v1",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.1,
            user_id="default",
        )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result = compute_insights(since_ts=0, user_id="default")
        ids = [s["id"] for s in result["suggestions"]]
        assert any("provider-cost" in sid for sid in ids)

    def test_provider_cost_suggestion_not_triggered_below_threshold(self, tmp_path):
        ds = _make_db(tmp_path)
        ds.record_usage(
            kind="chat",
            provider="main",
            model="main/v1",
            input_tokens=100,
            output_tokens=50,
            cost_usd=1.0,
            user_id="default",
        )
        ds.record_usage(
            kind="chat",
            provider="alt",
            model="alt/v1",
            input_tokens=50,
            output_tokens=25,
            cost_usd=0.5,
            user_id="default",
        )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result = compute_insights(since_ts=0, user_id="default")
        ids = [s["id"] for s in result["suggestions"]]
        assert not any("provider-cost" in sid for sid in ids)

    def test_tool_error_suggestion_triggered(self, tmp_path):
        """Tool with > 10% error rate (at least 5 calls) → suggestion."""
        ds = _make_db(tmp_path)
        for _ in range(8):
            ds.record_audit(
                category="tool",
                event="call",
                detail="FlakeyTool",
                metadata={"duration_ms": 50, "error": ""},
                user_id="default",
            )
        for _ in range(2):
            ds.record_audit(
                category="tool",
                event="call",
                detail="FlakeyTool",
                metadata={"duration_ms": 50, "error": "boom"},
                user_id="default",
            )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result = compute_insights(since_ts=0, user_id="default")
        ids = [s["id"] for s in result["suggestions"]]
        assert any("tool-error-FlakeyTool" in sid for sid in ids)

    def test_tool_error_suggestion_not_triggered_below_min_calls(self, tmp_path):
        """Fewer than 5 calls → no suggestion even with 100% error rate."""
        ds = _make_db(tmp_path)
        for _ in range(3):
            ds.record_audit(
                category="tool",
                event="call",
                detail="RareTool",
                metadata={"duration_ms": 50, "error": "boom"},
                user_id="default",
            )
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        result = compute_insights(since_ts=0, user_id="default")
        ids = [s["id"] for s in result["suggestions"]]
        assert not any("tool-error-RareTool" in sid for sid in ids)


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


class TestCaching:
    def test_same_args_returns_cached_result(self, tmp_path):
        ds = _make_db(tmp_path)
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        r1 = compute_insights(since_ts=0, user_id="default")
        # Seed new data — should NOT appear in the cached result
        ds.record_audit(
            category="tool",
            event="call",
            detail="NewTool",
            metadata={"duration_ms": 10},
            user_id="default",
        )
        r2 = compute_insights(since_ts=0, user_id="default")
        # Results are the same object from cache
        assert r1 is r2

    def test_bust_cache_forces_recompute(self, tmp_path):
        ds = _make_db(tmp_path)
        from api.agent.insights import bust_cache, compute_insights

        bust_cache()
        r1 = compute_insights(since_ts=0, user_id="default")
        ds.record_audit(
            category="tool",
            event="call",
            detail="FreshTool",
            metadata={"duration_ms": 10},
            user_id="default",
        )
        bust_cache()
        r2 = compute_insights(since_ts=0, user_id="default")
        assert r1 is not r2
        assert any(t["name"] == "FreshTool" for t in r2["tools"])
