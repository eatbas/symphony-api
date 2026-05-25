"""Branch coverage for symphony.discovery.discoverer parsing helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from symphony.discovery.discoverer import (
    _find_matching_bracket,
    _format_models_toml,
    _locate_provider_models_array,
    parse_config_models,
    parse_models_from_toml,
    replace_models_in_toml,
    run_startup_discovery,
)
from symphony.models import InstrumentName


# ---------------------------------------------------------------------------
# Bracket matcher
# ---------------------------------------------------------------------------


class TestFindMatchingBracket:
    def test_simple_balanced(self) -> None:
        text = "[a, b, c]"
        assert _find_matching_bracket(text, 0) == 8

    def test_handles_nested_brackets(self) -> None:
        text = "[a, [b, c], d]"
        assert _find_matching_bracket(text, 0) == 13

    def test_skips_brackets_inside_double_quoted_strings(self) -> None:
        text = '["a]b", "c"]'
        assert _find_matching_bracket(text, 0) == len(text) - 1

    def test_skips_brackets_inside_single_quoted_literals(self) -> None:
        text = "['a]b', 'c']"
        assert _find_matching_bracket(text, 0) == len(text) - 1

    def test_skips_brackets_inside_line_comments(self) -> None:
        text = "[\n  # ] not a close\n  \"a\"\n]"
        result = _find_matching_bracket(text, 0)
        assert result is not None
        assert text[result] == "]"

    def test_handles_escaped_quote_inside_string(self) -> None:
        text = r'["a\"b", "c"]'
        assert _find_matching_bracket(text, 0) == len(text) - 1

    def test_returns_none_when_unbalanced(self) -> None:
        assert _find_matching_bracket("[a, b", 0) is None


# ---------------------------------------------------------------------------
# Locate provider models array
# ---------------------------------------------------------------------------


class TestLocateProviderModelsArray:
    def test_returns_none_when_section_missing(self) -> None:
        text = "[providers.codex]\nmodels = [\"x\"]\n"
        assert _locate_provider_models_array(text, "claude") is None

    def test_returns_none_when_models_key_missing(self) -> None:
        text = "[providers.claude]\nenabled = true\n"
        assert _locate_provider_models_array(text, "claude") is None

    def test_returns_none_when_value_is_not_array(self) -> None:
        text = '[providers.claude]\nmodels = "not-a-list"\n'
        assert _locate_provider_models_array(text, "claude") is None

    def test_returns_bounds_for_inline_array(self) -> None:
        text = '[providers.claude]\nmodels = ["opus"]\n'
        bounds = _locate_provider_models_array(text, "claude")
        assert bounds is not None
        start, end = bounds
        assert text[start] == "["
        assert text[end] == "]"


# ---------------------------------------------------------------------------
# parse_models_from_toml
# ---------------------------------------------------------------------------


class TestParseModelsFromToml:
    def test_returns_empty_when_section_missing(self) -> None:
        assert parse_models_from_toml("[other]\nfoo = 1\n", "claude") == []

    def test_returns_empty_when_snippet_is_invalid(self) -> None:
        # Construct a fake bounds — easiest path is replace_models_in_toml then
        # corrupt; instead reach the TOMLDecodeError branch via a manually-crafted
        # array containing invalid TOML.
        text = '[providers.claude]\nmodels = ["unterminated\n'
        # Bracket matching will not find a close → returns [].
        assert parse_models_from_toml(text, "claude") == []

    def test_filters_empty_and_stringifies_items(self) -> None:
        text = '[providers.claude]\nmodels = ["opus", "haiku", ""]\n'
        assert parse_models_from_toml(text, "claude") == ["opus", "haiku"]


class TestParseConfigModels:
    def test_returns_mapping_keyed_by_provider(self) -> None:
        text = (
            '[providers.claude]\nmodels = ["opus"]\n'
            '[providers.codex]\nmodels = ["gpt-5.4"]\n'
        )
        assert parse_config_models(text, ["claude", "codex"]) == {
            "claude": ["opus"],
            "codex": ["gpt-5.4"],
        }


# ---------------------------------------------------------------------------
# _format_models_toml
# ---------------------------------------------------------------------------


class TestFormatModelsToml:
    def test_short_list_renders_inline(self) -> None:
        assert _format_models_toml(["a", "b"]) == '["a", "b"]'

    def test_long_list_renders_multiline(self) -> None:
        rendered = _format_models_toml(["a", "b", "c", "d"])
        assert rendered.startswith("[\n")
        assert rendered.endswith("\n]")
        assert '"a"' in rendered and '"d"' in rendered


# ---------------------------------------------------------------------------
# replace_models_in_toml
# ---------------------------------------------------------------------------


class TestReplaceModelsInToml:
    def test_returns_text_unchanged_when_section_absent(self) -> None:
        text = "[other]\nfoo = 1\n"
        assert replace_models_in_toml(text, "claude", ["x"]) == text

    def test_replaces_inline_array(self) -> None:
        text = '[providers.claude]\nmodels = ["opus"]\n'
        result = replace_models_in_toml(text, "claude", ["sonnet", "haiku"])
        assert '"sonnet"' in result
        assert '"opus"' not in result


# ---------------------------------------------------------------------------
# run_startup_discovery
# ---------------------------------------------------------------------------


class TestRunStartupDiscovery:
    def test_returns_false_when_env_var_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYMPHONY_SKIP_DISCOVERY", "1")
        cfg = tmp_path / "config.toml"
        cfg.write_text("[providers.claude]\nmodels = []\n")
        assert run_startup_discovery(cfg) is False

    def test_returns_false_when_config_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SYMPHONY_SKIP_DISCOVERY", raising=False)
        assert run_startup_discovery(tmp_path / "nope.toml") is False

    def test_writes_changes_when_models_differ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SYMPHONY_SKIP_DISCOVERY", raising=False)
        cfg = tmp_path / "config.toml"
        cfg.write_text('[providers.claude]\nmodels = ["opus"]\n')
        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: lambda: ["opus", "haiku"]},
        ):
            changed = run_startup_discovery(cfg)
        assert changed is True
        text = cfg.read_text()
        assert '"haiku"' in text

    def test_returns_false_when_models_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SYMPHONY_SKIP_DISCOVERY", raising=False)
        cfg = tmp_path / "config.toml"
        cfg.write_text('[providers.claude]\nmodels = ["opus"]\n')
        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: lambda: ["opus"]},
        ):
            changed = run_startup_discovery(cfg)
        assert changed is False

    def test_skips_when_discovery_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SYMPHONY_SKIP_DISCOVERY", raising=False)
        cfg = tmp_path / "config.toml"
        original = '[providers.claude]\nmodels = ["opus"]\n'
        cfg.write_text(original)
        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: lambda: None},
        ):
            changed = run_startup_discovery(cfg)
        assert changed is False
        assert cfg.read_text() == original

    def test_swallows_discovery_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SYMPHONY_SKIP_DISCOVERY", raising=False)
        cfg = tmp_path / "config.toml"
        original = '[providers.claude]\nmodels = ["opus"]\n'
        cfg.write_text(original)

        def explode() -> list[str]:
            raise RuntimeError("boom")

        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: explode},
        ):
            changed = run_startup_discovery(cfg)
        assert changed is False
        assert cfg.read_text() == original
