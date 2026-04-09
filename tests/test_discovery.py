from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from symphony.discovery.discoverer import discover_provider, parse_models_from_toml
from symphony.discovery.providers import _discover_gemini
from symphony.models import InstrumentName


SAMPLE_CONFIG = """\
[server]
host = "127.0.0.1"
port = 8000

[providers.claude]
enabled = true
models = ["opus", "haiku"]

[providers.gemini]
enabled = true
models = ["gemini-3-flash-preview"]
"""

BRACKETED_MODEL_CONFIG = """\
[server]
host = "127.0.0.1"
port = 8000

[providers.claude]
enabled = true
models = [
  "haiku",
  "opus",
  "opus[1m]",
  "sonnet",
]

[providers.gemini]
enabled = true
models = ["gemini-3-flash-preview"]
"""


class TestDiscoverProvider:
    def test_gemini_discovery_prefers_cli_valid_model_set(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "chunk.js").write_text(
            """
            var PREVIEW_GEMINI_MODEL = "gemini-3-pro-preview";
            var PREVIEW_GEMINI_3_1_MODEL = "gemini-3.1-pro-preview";
            var DEFAULT_GEMINI_MODEL = "gemini-2.5-pro";
            var DEFAULT_GEMINI_FLASH_MODEL = "gemini-2.5-flash";
            var VALID_GEMINI_MODELS = /* @__PURE__ */ new Set([
              PREVIEW_GEMINI_MODEL,
              PREVIEW_GEMINI_3_1_MODEL,
              DEFAULT_GEMINI_MODEL,
              DEFAULT_GEMINI_FLASH_MODEL
            ]);
            """,
            encoding="utf-8",
        )

        with patch("symphony.discovery.providers._npm_package_dir", return_value=tmp_path):
            assert _discover_gemini() == [
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-3-pro-preview",
                "gemini-3.1-pro-preview",
            ]

    def test_parses_bracketed_model_names(self) -> None:
        assert parse_models_from_toml(BRACKETED_MODEL_CONFIG, "claude") == [
            "haiku",
            "opus",
            "opus[1m]",
            "sonnet",
        ]

    def test_updates_config_when_models_change(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text(SAMPLE_CONFIG, encoding="utf-8")

        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: lambda: ["haiku", "opus", "sonnet"]},
        ):
            changed = discover_provider(InstrumentName.CLAUDE, config)

        assert changed is True
        text = config.read_text(encoding="utf-8")
        assert '"sonnet"' in text
        # Gemini section must be untouched.
        assert '"gemini-3-flash-preview"' in text

    def test_updates_bracketed_model_arrays_without_corrupting_toml(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text(BRACKETED_MODEL_CONFIG, encoding="utf-8")

        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: lambda: ["haiku", "opus", "opus[1m]", "sonnet", "sonnet-extended"]},
        ):
            changed = discover_provider(InstrumentName.CLAUDE, config)

        assert changed is True
        parsed = tomllib.loads(config.read_text(encoding="utf-8"))
        assert parsed["providers"]["claude"]["models"] == [
            "haiku",
            "opus",
            "opus[1m]",
            "sonnet",
            "sonnet-extended",
        ]
        assert parsed["providers"]["gemini"]["models"] == ["gemini-3-flash-preview"]

    def test_returns_false_when_models_unchanged(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text(SAMPLE_CONFIG, encoding="utf-8")

        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: lambda: ["opus", "haiku"]},
        ):
            changed = discover_provider(InstrumentName.CLAUDE, config)

        assert changed is False

    def test_returns_false_when_discovery_returns_none(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text(SAMPLE_CONFIG, encoding="utf-8")

        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: lambda: None},
        ):
            changed = discover_provider(InstrumentName.CLAUDE, config)

        assert changed is False
        assert config.read_text(encoding="utf-8") == SAMPLE_CONFIG

    def test_returns_false_for_unknown_provider(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text(SAMPLE_CONFIG, encoding="utf-8")

        with patch("symphony.discovery.discoverer.DISCOVERERS", {}):
            changed = discover_provider(InstrumentName.CLAUDE, config)

        assert changed is False

    def test_returns_false_when_discovery_raises(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text(SAMPLE_CONFIG, encoding="utf-8")

        def _explode() -> list[str]:
            raise RuntimeError("boom")

        with patch(
            "symphony.discovery.discoverer.DISCOVERERS",
            {InstrumentName.CLAUDE: _explode},
        ):
            changed = discover_provider(InstrumentName.CLAUDE, config)

        assert changed is False
        assert config.read_text(encoding="utf-8") == SAMPLE_CONFIG
