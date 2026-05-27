"""Tests for discovery/filters.py."""
from __future__ import annotations

from symphony.discovery.filters import filter_codex, filter_opencode


def test_filter_codex_keeps_gpt_5_2_and_above() -> None:
    assert filter_codex(["gpt-5.2", "gpt-5.3", "gpt-6.0"]) == ["gpt-5.2", "gpt-5.3", "gpt-6.0"]


def test_filter_codex_drops_older_gpt_versions() -> None:
    assert filter_codex(["gpt-4", "gpt-5.0", "gpt-5.1", "gpt-5.2"]) == ["gpt-5.2"]


def test_filter_codex_includes_non_gpt_models_unchanged() -> None:
    # Non-GPT names fall through the else branch and are kept verbatim.
    assert filter_codex(["claude-opus", "gpt-5.3"]) == ["claude-opus", "gpt-5.3"]


def test_filter_codex_sorts_alphabetically() -> None:
    assert filter_codex(["gpt-5.4-mini", "gpt-5.4", "gpt-5.3"]) == [
        "gpt-5.3",
        "gpt-5.4",
        "gpt-5.4-mini",
    ]


def test_filter_codex_handles_implicit_minor_zero() -> None:
    # ``gpt-5`` parses as major=5 minor=0 -- below the (5, 2) threshold.
    assert filter_codex(["gpt-5"]) == []


def test_filter_codex_returns_empty_for_empty_input() -> None:
    assert filter_codex([]) == []


def test_filter_opencode_keeps_latest_major_only() -> None:
    """The filter must keep only the highest GLM major version present."""
    assert filter_opencode(["glm-4.5", "glm-5", "glm-5.1", "glm-5-turbo"]) == [
        "glm-5",
        "glm-5-turbo",
        "glm-5.1",
    ]


def test_filter_opencode_returns_input_unchanged_when_no_glm_prefix() -> None:
    """If none of the entries match the ``glm-N`` pattern, return the list
    verbatim -- covers the ``max_major == 0`` early-return branch."""
    arbitrary = ["zai-coding-plan/special", "another"]
    assert filter_opencode(arbitrary) == arbitrary


def test_filter_opencode_returns_empty_for_empty_input() -> None:
    assert filter_opencode([]) == []
