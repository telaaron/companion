"""Unit tests for the tool registry."""

from __future__ import annotations

from core.tools.registry import anthropic_tool_definitions, get


def test_registry_exposes_phase_b_pr1_tools() -> None:
    defs = anthropic_tool_definitions()
    names = {d["name"] for d in defs}
    assert {"Read", "Write", "LS"}.issubset(names)


def test_registry_exposes_phase_b_pr2_tools() -> None:
    names = {d["name"] for d in anthropic_tool_definitions()}
    assert {"Edit", "Glob", "Grep", "Bash"}.issubset(names)


def test_each_definition_has_required_fields() -> None:
    for defn in anthropic_tool_definitions():
        assert defn["name"]
        assert defn["description"]
        assert defn["input_schema"]["type"] == "object"
        assert "required" in defn["input_schema"]


def test_get_returns_spec_with_executor() -> None:
    spec = get("Read")
    assert spec is not None
    assert spec.name == "Read"
    assert callable(spec.executor)


def test_get_unknown_returns_none() -> None:
    assert get("DoesNotExist") is None
