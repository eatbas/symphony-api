"""Tests for providers/codex_options.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from symphony.providers.codex_options import codex_model_options


@pytest.fixture()
def home_with_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` so codex_options reads a temp ``~/.codex/``."""
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


def _write_cache(home: Path, payload: object) -> None:
    codex_dir = home / ".codex"
    codex_dir.mkdir(exist_ok=True)
    (codex_dir / "models_cache.json").write_text(json.dumps(payload))


class TestCodexModelOptions:
    def test_returns_empty_when_cache_missing(self, home_with_codex: Path) -> None:
        assert codex_model_options("gpt-5.4") == []

    def test_returns_empty_when_cache_is_invalid_json(self, home_with_codex: Path) -> None:
        codex_dir = home_with_codex / ".codex"
        codex_dir.mkdir()
        (codex_dir / "models_cache.json").write_text("not json")
        assert codex_model_options("gpt-5.4") == []

    def test_returns_empty_when_cache_is_not_a_dict(self, home_with_codex: Path) -> None:
        _write_cache(home_with_codex, ["not", "a", "dict"])
        assert codex_model_options("gpt-5.4") == []

    def test_returns_empty_when_models_key_missing(self, home_with_codex: Path) -> None:
        _write_cache(home_with_codex, {"unrelated": []})
        assert codex_model_options("gpt-5.4") == []

    def test_returns_empty_when_models_not_list(self, home_with_codex: Path) -> None:
        _write_cache(home_with_codex, {"models": "not a list"})
        assert codex_model_options("gpt-5.4") == []

    def test_returns_empty_when_model_not_found(self, home_with_codex: Path) -> None:
        _write_cache(
            home_with_codex,
            {"models": [{"slug": "gpt-5.3", "supported_reasoning_levels": []}]},
        )
        assert codex_model_options("gpt-5.4") == []

    def test_returns_empty_when_supported_reasoning_levels_not_list(
        self, home_with_codex: Path
    ) -> None:
        _write_cache(
            home_with_codex,
            {"models": [{"slug": "gpt-5.4", "supported_reasoning_levels": "high"}]},
        )
        assert codex_model_options("gpt-5.4") == []

    def test_filters_non_dict_entries(self, home_with_codex: Path) -> None:
        _write_cache(
            home_with_codex,
            {
                "models": [
                    {
                        "slug": "gpt-5.4",
                        "supported_reasoning_levels": [
                            "not a dict",
                            {"effort": "high"},
                        ],
                    }
                ]
            },
        )
        result = codex_model_options("gpt-5.4")
        assert len(result) == 1
        assert [c["value"] for c in result[0]["choices"]] == ["high"]

    def test_filters_entries_missing_effort(self, home_with_codex: Path) -> None:
        _write_cache(
            home_with_codex,
            {
                "models": [
                    {
                        "slug": "gpt-5.4",
                        "supported_reasoning_levels": [
                            {"description": "no effort key"},
                            {"effort": ""},  # empty effort skipped
                            {"effort": 123},  # non-string effort skipped
                            {"effort": "low"},
                        ],
                    }
                ]
            },
        )
        result = codex_model_options("gpt-5.4")
        assert [c["value"] for c in result[0]["choices"]] == ["low"]

    def test_returns_empty_when_no_valid_choices(self, home_with_codex: Path) -> None:
        _write_cache(
            home_with_codex,
            {
                "models": [
                    {
                        "slug": "gpt-5.4",
                        "supported_reasoning_levels": [{"description": "no effort"}],
                    }
                ]
            },
        )
        assert codex_model_options("gpt-5.4") == []

    def test_uses_known_label_when_effort_is_recognised(self, home_with_codex: Path) -> None:
        _write_cache(
            home_with_codex,
            {
                "models": [
                    {
                        "slug": "gpt-5.4",
                        "supported_reasoning_levels": [
                            {"effort": "low", "description": "fast"},
                            {"effort": "xhigh", "description": "deep"},
                        ],
                    }
                ]
            },
        )
        result = codex_model_options("gpt-5.4")
        labels = {c["value"]: c["label"] for c in result[0]["choices"]}
        assert labels == {"low": "Low", "xhigh": "Extra high"}

    def test_falls_back_to_raw_effort_string_for_unknown_label(
        self, home_with_codex: Path
    ) -> None:
        _write_cache(
            home_with_codex,
            {
                "models": [
                    {
                        "slug": "gpt-5.4",
                        "supported_reasoning_levels": [{"effort": "atomic"}],
                    }
                ]
            },
        )
        result = codex_model_options("gpt-5.4")
        assert result[0]["choices"][0]["label"] == "atomic"

    def test_drops_non_string_description(self, home_with_codex: Path) -> None:
        _write_cache(
            home_with_codex,
            {
                "models": [
                    {
                        "slug": "gpt-5.4",
                        "supported_reasoning_levels": [
                            {"effort": "high", "description": 42},
                        ],
                    }
                ]
            },
        )
        result = codex_model_options("gpt-5.4")
        assert result[0]["choices"][0]["description"] is None

    def test_drops_non_string_default(self, home_with_codex: Path) -> None:
        _write_cache(
            home_with_codex,
            {
                "models": [
                    {
                        "slug": "gpt-5.4",
                        "supported_reasoning_levels": [{"effort": "high"}],
                        "default_reasoning_level": 999,
                    }
                ]
            },
        )
        result = codex_model_options("gpt-5.4")
        assert result[0]["default"] is None
