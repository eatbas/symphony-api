from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from symphony.config import load_config
from symphony.models import InstrumentName


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run Playwright UI tests last to avoid interfering with pytest-asyncio loops."""
    ui_items: list[pytest.Item] = []
    other_items: list[pytest.Item] = []
    for item in items:
        if item.nodeid.startswith("tests/ui_e2e/"):
            ui_items.append(item)
        else:
            other_items.append(item)
    items[:] = [*other_items, *ui_items]


def make_wrapper(tmp_path: Path, provider: str) -> str:
    wrapper = tmp_path / f"{provider}.sh"
    fake_cli = Path(__file__).parent / "fakes" / "fake_cli.py"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f'"{sys.executable}" "{fake_cli.as_posix()}" {provider} "$@"\n',
        encoding="utf-8",
    )
    os.chmod(wrapper, 0o755)
    return str(wrapper)


def make_config(tmp_path: Path) -> Path:
    providers = {provider.value: make_wrapper(tmp_path, provider.value) for provider in InstrumentName}
    score_dir = tmp_path.parent / f"{tmp_path.name}-scores"
    escaped_score_dir = str(score_dir.resolve()).replace("\\", "\\\\")
    escaped_providers = {
        key: value.replace("\\", "\\\\")
        for key, value in providers.items()
    }
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[server]
host = "127.0.0.1"
port = 8000

[shell]
path = ""

[storage]
score_dir = "{escaped_score_dir}"

[providers.gemini]
enabled = true
executable = "{escaped_providers['gemini']}"
models = ["gemini-3-flash-preview", "gemini-3.1-pro-preview"]
default_options = {{ extra_args = [] }}

[providers.codex]
enabled = true
executable = "{escaped_providers['codex']}"
models = ["gpt-5.4", "gpt-5.2"]
default_options = {{ extra_args = [] }}

[providers.claude]
enabled = true
executable = "{escaped_providers['claude']}"
models = ["opus", "haiku"]
default_options = {{ extra_args = [] }}

[providers.kimi]
enabled = true
executable = "{escaped_providers['kimi']}"
models = ["kimi-code/kimi-for-coding"]
default_options = {{ extra_args = [] }}

[providers.copilot]
enabled = true
executable = "{escaped_providers['copilot']}"
models = ["claude-sonnet-4.6", "grok-code-fast-1"]
default_options = {{ extra_args = [] }}

[providers.opencode]
enabled = true
executable = "{escaped_providers['opencode']}"
models = ["glm-4.5", "glm-5.1"]
default_options = {{ extra_args = [] }}

[updater]
enabled = false
""".strip(),
        encoding="utf-8",
    )
    return config_path


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = make_config(tmp_path)
    monkeypatch.setenv("SYMPHONY_CONFIG", str(path))
    monkeypatch.setenv("SYMPHONY_SKIP_DISCOVERY", "1")
    return path


@pytest.fixture()
def loaded_config(config_path: Path):
    return load_config(config_path)
