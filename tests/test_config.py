from symphony.config import load_config
from symphony.models import InstrumentName


def test_load_config_expands_provider_models(config_path):
    config = load_config(config_path)
    assert config.providers[InstrumentName.GEMINI].models == ["gemini-3-flash-preview", "gemini-3.1-pro-preview"]
    assert config.providers[InstrumentName.CODEX].enabled is True
    assert config.providers[InstrumentName.CODEX].models == ["gpt-5.4", "gpt-5.2"]
    assert config.storage.score_dir is not None
    assert config.storage.score_dir.name.endswith("-scores")
