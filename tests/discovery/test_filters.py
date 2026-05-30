"""Tests for discovery/filters.py."""
from __future__ import annotations

from symphony.discovery.filters import filter_codex


def test_filter_codex_keeps_gpt_5_4_and_above() -> None:
    assert filter_codex(["gpt-5.4", "gpt-5.5", "gpt-6.0"]) == ["gpt-5.4", "gpt-5.5", "gpt-6.0"]


def test_filter_codex_drops_older_gpt_versions() -> None:
    assert filter_codex(["gpt-4", "gpt-5.2", "gpt-5.3", "gpt-5.4"]) == ["gpt-5.4"]


def test_filter_codex_includes_non_gpt_models_unchanged() -> None:
    # Non-GPT names fall through the else branch and are kept verbatim.
    assert filter_codex(["claude-opus", "gpt-5.4"]) == ["claude-opus", "gpt-5.4"]


def test_filter_codex_sorts_alphabetically() -> None:
    assert filter_codex(["gpt-5.4-mini", "gpt-5.4", "gpt-5.5"]) == [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.5",
    ]


def test_filter_codex_handles_implicit_minor_zero() -> None:
    # ``gpt-5`` parses as major=5 minor=0 -- below the (5, 4) threshold.
    assert filter_codex(["gpt-5"]) == []


def test_filter_codex_returns_empty_for_empty_input() -> None:
    assert filter_codex([]) == []
