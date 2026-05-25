"""Tests for providers/options.py branch coverage."""
from __future__ import annotations

import pytest

from symphony.providers.options import (
    apply_thinking_prompt_hint,
    boolean_thinking_schema,
    get_ralph_iterations,
    get_thinking_level,
    ralph_iterations_schema,
    thinking_enabled,
    thinking_level_schema,
)


class TestGetThinkingLevel:
    def test_returns_none_when_unset(self) -> None:
        assert get_thinking_level({}) is None

    def test_returns_thinking_level_value(self) -> None:
        assert get_thinking_level({"thinking_level": "high"}) == "high"

    def test_reads_reasoning_effort_alias(self) -> None:
        assert get_thinking_level({"reasoning_effort": "low"}) == "low"

    def test_thinking_level_takes_precedence_over_reasoning_effort(self) -> None:
        assert (
            get_thinking_level({"thinking_level": "high", "reasoning_effort": "low"})
            == "high"
        )

    def test_raises_for_invalid_value(self) -> None:
        with pytest.raises(ValueError, match="thinking_level"):
            get_thinking_level({"thinking_level": "ultra"})

    def test_raises_for_non_string_value(self) -> None:
        with pytest.raises(ValueError, match="thinking_level"):
            get_thinking_level({"thinking_level": 5})

    def test_respects_custom_allowed_tuple(self) -> None:
        assert get_thinking_level({"thinking_level": "low"}, allowed=("low",)) == "low"
        with pytest.raises(ValueError):
            get_thinking_level({"thinking_level": "high"}, allowed=("low",))


class TestThinkingEnabled:
    def test_returns_default_when_unset(self) -> None:
        assert thinking_enabled({}) is True
        assert thinking_enabled({}, default=False) is False

    def test_enabled(self) -> None:
        assert thinking_enabled({"thinking_mode": "enabled"}) is True

    def test_disabled(self) -> None:
        assert thinking_enabled({"thinking_mode": "disabled"}) is False

    def test_raises_for_invalid_value(self) -> None:
        with pytest.raises(ValueError, match="thinking_mode"):
            thinking_enabled({"thinking_mode": "maybe"})


class TestRalphIterations:
    def test_returns_none_when_unset(self) -> None:
        assert get_ralph_iterations({}) is None

    def test_accepts_int(self) -> None:
        assert get_ralph_iterations({"max_ralph_iterations": 3}) == 3

    def test_accepts_int_string(self) -> None:
        assert get_ralph_iterations({"max_ralph_iterations": "5"}) == 5

    def test_accepts_negative_one(self) -> None:
        assert get_ralph_iterations({"max_ralph_iterations": "-1"}) == -1

    def test_raises_for_garbage_string(self) -> None:
        with pytest.raises(ValueError, match="integer"):
            get_ralph_iterations({"max_ralph_iterations": "many"})

    def test_raises_for_invalid_type(self) -> None:
        with pytest.raises(ValueError, match="integer"):
            get_ralph_iterations({"max_ralph_iterations": [1, 2]})


class TestApplyThinkingPromptHint:
    def test_returns_prompt_unchanged_when_no_level(self) -> None:
        assert apply_thinking_prompt_hint("Hello", {}) == "Hello"

    def test_prepends_hint_when_level_set(self) -> None:
        result = apply_thinking_prompt_hint("Do task", {"thinking_level": "high"})
        assert result.endswith("\n\nDo task")
        assert "deeper reasoning" in result.lower()


class TestSchemaShapes:
    def test_thinking_level_schema_default_choices(self) -> None:
        schema = thinking_level_schema()
        assert schema[0]["key"] == "thinking_level"
        assert schema[0]["default"] == "medium"
        assert {c["value"] for c in schema[0]["choices"]} == {
            "low",
            "medium",
            "high",
            "xhigh",
        }

    def test_thinking_level_schema_custom_levels(self) -> None:
        schema = thinking_level_schema(levels=("low", "high"), default="high")
        assert schema[0]["default"] == "high"
        assert [c["value"] for c in schema[0]["choices"]] == ["low", "high"]

    def test_boolean_thinking_schema(self) -> None:
        schema = boolean_thinking_schema()
        assert schema[0]["key"] == "thinking_mode"
        assert schema[0]["default"] == "enabled"

    def test_ralph_iterations_schema(self) -> None:
        schema = ralph_iterations_schema(default="3")
        assert schema[0]["key"] == "max_ralph_iterations"
        assert schema[0]["default"] == "3"
        assert {c["value"] for c in schema[0]["choices"]} == {"1", "3", "5", "-1"}
