from symphony.config import _instrument_config, load_config
from symphony.models import InstrumentName


def test_load_config_expands_provider_models(config_path):
    config = load_config(config_path)
    assert config.providers[InstrumentName.ANTIGRAVITY].models == ["gemini-3.5-flash", "gemini-3.1-pro"]
    assert config.providers[InstrumentName.CODEX].enabled is True
    assert config.providers[InstrumentName.CODEX].models == ["gpt-5.4", "gpt-5.4-mini"]
    assert config.storage.score_dir is not None
    assert config.storage.score_dir.name.endswith("-scores")


def test_instrument_config_defaults_models_when_key_absent() -> None:
    """When the TOML block omits ``models`` entirely, the default
    placeholder ``["default"]`` must be installed so downstream code
    has at least one model identifier to attach to a musician pool."""
    cfg = _instrument_config({"enabled": True})
    assert cfg.models == ["default"]


def test_instrument_config_lazy_flag_defaults_to_false() -> None:
    """The ``lazy`` flag must be opt-in so existing providers keep
    eager-boot semantics."""
    cfg = _instrument_config({})
    assert cfg.lazy is False


def test_instrument_config_lazy_flag_parses_truthy_value() -> None:
    cfg = _instrument_config({"lazy": True})
    assert cfg.lazy is True
