from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from symphony.discovery.discoverer import discover_provider, parse_models_from_toml
from symphony.discovery.providers import _discover_antigravity
from symphony.models import InstrumentName


SAMPLE_CONFIG = """\
[server]
host = "127.0.0.1"
port = 8000

[providers.claude]
enabled = true
models = ["opus", "haiku"]

[providers.antigravity]
enabled = true
models = ["gemini-3.5-flash"]
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

[providers.antigravity]
enabled = true
models = ["gemini-3.5-flash"]
"""


class TestDiscoverProvider:
    def test_antigravity_discovery_is_a_static_passthrough(self) -> None:
        """Antigravity has no programmatic model discovery; the function
        must return None so the caller keeps the static config models.
        """
        assert _discover_antigravity() is None

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
        # Antigravity section must be untouched.
        assert '"gemini-3.5-flash"' in text

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
        assert parsed["providers"]["antigravity"]["models"] == ["gemini-3.5-flash"]

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
