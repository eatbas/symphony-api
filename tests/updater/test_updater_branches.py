"""Branch coverage for symphony.updater.updater.CLIUpdater."""
from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, patch

import pytest

from symphony.config import UpdaterConfig
from symphony.models import InstrumentName
from symphony.orchestra import Orchestra
from symphony.updater import CLIUpdater


@pytest.mark.asyncio
async def test_run_cmd_wraps_module_run_cmd(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    with patch(
        "symphony.updater.updater.run_cmd", AsyncMock(return_value=(0, "ok"))
    ) as mock_run:
        code, output = await updater._run_cmd("echo", "hi")
    assert code == 0
    assert output == "ok"
    mock_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_rediscover_models_logs_when_models_refreshed(
    loaded_config, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="symphony.updater")
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))

    with (
        patch(
            "symphony.updater.updater.discover_provider",
            return_value=True,
        ),
        patch(
            "symphony.updater.updater.refresh_provider_models",
            AsyncMock(return_value=True),
        ),
    ):
        await updater._rediscover_models(InstrumentName.CLAUDE)
    assert "refreshed" in caplog.text.lower()


@pytest.mark.asyncio
async def test_rediscover_models_skips_refresh_when_no_changes(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))

    with (
        patch(
            "symphony.updater.updater.discover_provider",
            return_value=False,
        ),
        patch(
            "symphony.updater.updater.refresh_provider_models",
            AsyncMock(return_value=False),
        ) as mock_refresh,
    ):
        await updater._rediscover_models(InstrumentName.CLAUDE)
    mock_refresh.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_provider_context branches
# ---------------------------------------------------------------------------


def test_resolve_context_returns_none_for_disabled_provider(loaded_config) -> None:
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.KIMI]
    new_providers[InstrumentName.KIMI] = dataclasses.replace(base, enabled=False)
    config = dataclasses.replace(loaded_config, providers=new_providers)
    manager = Orchestra(config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    assert updater._resolve_provider_context(InstrumentName.KIMI) is None


def test_resolve_context_returns_none_when_adapter_missing(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    manager.registry.pop(InstrumentName.CLAUDE, None)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    assert updater._resolve_provider_context(InstrumentName.CLAUDE) is None


def test_resolve_context_returns_none_when_pkg_info_missing(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    with patch.dict(
        "symphony.updater.updater.PACKAGE_REGISTRY", {}, clear=True
    ):
        assert updater._resolve_provider_context(InstrumentName.CLAUDE) is None


def test_resolve_context_returns_tuple_on_happy_path(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    ctx = updater._resolve_provider_context(InstrumentName.CLAUDE)
    assert ctx is not None
    adapter, executable, pkg_info = ctx
    assert adapter is not None
    assert pkg_info.provider == InstrumentName.CLAUDE


# ---------------------------------------------------------------------------
# _probe_single_provider returns None when context resolution fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_single_provider_returns_none_when_context_missing(
    loaded_config,
) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    with patch.dict(
        "symphony.updater.updater.PACKAGE_REGISTRY", {}, clear=True
    ):
        result = await updater._probe_single_provider(
            InstrumentName.CLAUDE, "now", "next"
        )
    assert result is None


# ---------------------------------------------------------------------------
# check_single_provider caches results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_single_provider_caches_result(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    updater.get_current_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    updater.get_latest_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    result = await updater.check_single_provider(InstrumentName.CLAUDE)
    assert result is not None
    assert result.provider == InstrumentName.CLAUDE
    assert updater._last_results and updater._last_results[0].provider == InstrumentName.CLAUDE


@pytest.mark.asyncio
async def test_check_single_provider_returns_none_when_context_missing(
    loaded_config,
) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    with patch.dict(
        "symphony.updater.updater.PACKAGE_REGISTRY", {}, clear=True
    ):
        result = await updater.check_single_provider(InstrumentName.CLAUDE)
    assert result is None


# ---------------------------------------------------------------------------
# Auto-update path inside _check_single_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_single_provider_skips_update_when_busy(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(
        manager=manager,
        config=UpdaterConfig(enabled=False, auto_update=True),
    )
    updater.get_current_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    updater.get_latest_version = AsyncMock(return_value="1.0.1")  # type: ignore[method-assign]
    # Force provider to look "busy".
    updater.is_provider_idle = lambda _p: False  # type: ignore[method-assign]
    result = await updater.check_single_provider(InstrumentName.CLAUDE)
    assert result is not None
    assert result.update_skipped_reason == "musicians busy"


@pytest.mark.asyncio
async def test_check_single_provider_runs_update_and_restart(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(
        manager=manager,
        config=UpdaterConfig(enabled=False, auto_update=True),
    )
    updater.get_current_version = AsyncMock(side_effect=["1.0.0", "1.0.1"])  # type: ignore[method-assign]
    updater.get_latest_version = AsyncMock(return_value="1.0.1")  # type: ignore[method-assign]
    updater.is_provider_idle = lambda _p: True  # type: ignore[method-assign]
    updater.update_cli = AsyncMock(return_value=True)  # type: ignore[method-assign]
    manager.restart_provider = AsyncMock()  # type: ignore[method-assign]
    manager.activate_provider = AsyncMock(return_value=True)  # type: ignore[method-assign]
    updater._rediscover_models = AsyncMock()  # type: ignore[method-assign]

    result = await updater.check_single_provider(InstrumentName.CLAUDE)
    assert result is not None
    assert result.last_updated is not None
    assert result.current_version == "1.0.1"


@pytest.mark.asyncio
async def test_check_single_provider_handles_update_failure(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(
        manager=manager,
        config=UpdaterConfig(enabled=False, auto_update=True),
    )
    updater.get_current_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    updater.get_latest_version = AsyncMock(return_value="1.0.1")  # type: ignore[method-assign]
    updater.is_provider_idle = lambda _p: True  # type: ignore[method-assign]
    updater.update_cli = AsyncMock(return_value=False)  # type: ignore[method-assign]

    result = await updater.check_single_provider(InstrumentName.CLAUDE)
    assert result is not None
    assert result.update_skipped_reason == "update command failed"


@pytest.mark.asyncio
async def test_probe_versions_only_returns_per_provider_results(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    manager.available_providers[InstrumentName.CLAUDE] = True
    manager.available_providers[InstrumentName.CODEX] = False
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    updater.get_current_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    updater.get_latest_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    results = await updater.probe_versions_only()
    providers = {r.provider for r in results}
    # Only available providers are probed.
    assert InstrumentName.CLAUDE in providers
    assert InstrumentName.CODEX not in providers
