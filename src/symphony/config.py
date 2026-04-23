from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from typing import Any

from .models import InstrumentName


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass(slots=True)
class ShellConfig:
    path: str | None = None


@dataclass(slots=True)
class StorageConfig:
    score_dir: Path | None = None


@dataclass(slots=True)
class InstrumentConfig:
    enabled: bool = True
    executable: str | None = None
    models: list[str] = field(default_factory=lambda: ["default"])
    default_options: dict[str, Any] = field(default_factory=dict)
    cli_timeout: float = 300.0  # seconds; 0 = no timeout
    idle_timeout: float = 0.0  # seconds without output before assuming CLI is stuck; 0 = no idle timeout
    concurrency: int = 4  # max concurrent musicians per model


@dataclass(slots=True)
class UpdaterConfig:
    enabled: bool = True
    interval_hours: float = 4.0
    auto_update: bool = True


@dataclass(slots=True)
class AppConfig:
    server: ServerConfig
    shell: ShellConfig
    storage: StorageConfig
    providers: dict[InstrumentName, InstrumentConfig]
    updater: UpdaterConfig
    config_path: Path


def _instrument_config(raw: dict[str, Any] | None) -> InstrumentConfig:
    raw = raw or {}
    models = [str(item) for item in raw.get("models", ["default"]) if str(item).strip()]
    return InstrumentConfig(
        enabled=bool(raw.get("enabled", True)),
        executable=(str(raw["executable"]).strip() or None) if raw.get("executable") is not None else None,
        models=models or ["default"],
        default_options=dict(raw.get("default_options", {})),
        cli_timeout=float(raw.get("cli_timeout", 300.0)),
        idle_timeout=float(raw.get("idle_timeout", 0.0)),
        concurrency=int(raw.get("concurrency", 4)),
    )


def _default_instrument_map(raw: dict[str, Any]) -> dict[InstrumentName, InstrumentConfig]:
    provider_section = raw.get("providers", {})
    return {
        instrument: _instrument_config(provider_section.get(instrument.value))
        for instrument in InstrumentName
    }


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    config_path = Path(
        path
        or os.environ.get("SYMPHONY_CONFIG")
        or os.environ.get("HIVE_API_CONFIG")
        or Path.cwd() / "config.toml"
    )
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    server = raw.get("server", {})
    shell = raw.get("shell", {})
    storage = raw.get("storage", {})
    updater = raw.get("updater", {})
    score_dir = os.environ.get("SYMPHONY_SCORE_DIR") or storage.get("score_dir")

    return AppConfig(
        server=ServerConfig(
            host=str(server.get("host", "127.0.0.1")),
            port=int(server.get("port", 8000)),
        ),
        shell=ShellConfig(
            path=(str(shell["path"]).strip() or None) if shell.get("path") is not None else None,
        ),
        storage=StorageConfig(
            score_dir=Path(str(score_dir)).expanduser() if score_dir else None,
        ),
        providers=_default_instrument_map(raw),
        updater=UpdaterConfig(
            enabled=bool(updater.get("enabled", True)),
            interval_hours=float(updater.get("interval_hours", 4.0)),
            auto_update=bool(updater.get("auto_update", True)),
        ),
        config_path=config_path,
    )
