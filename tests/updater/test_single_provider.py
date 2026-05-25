"""Tests for updater/single_provider.py — manual provider update flow."""
from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, patch

import pytest

from symphony.config import UpdaterConfig
from symphony.models import InstrumentName
from symphony.orchestra import Orchestra
from symphony.updater import CLIUpdater
from symphony.updater.single_provider import update_single_provider_impl


@pytest.mark.asyncio
async def test_returns_disabled_status_for_disabled_provider(loaded_config) -> None:
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.KIMI]
    new_providers[InstrumentName.KIMI] = dataclasses.replace(base, enabled=False)
    config = dataclasses.replace(loaded_config, providers=new_providers)
    manager = Orchestra(config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    result = await update_single_provider_impl(updater, InstrumentName.KIMI)
    assert result.update_skipped_reason == "provider not enabled"


@pytest.mark.asyncio
async def test_returns_no_adapter_status_when_adapter_missing(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    # Remove the adapter to drive the "no adapter" branch.
    manager.registry.pop(InstrumentName.CLAUDE, None)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    result = await update_single_provider_impl(updater, InstrumentName.CLAUDE)
    assert result.update_skipped_reason == "no adapter"


@pytest.mark.asyncio
async def test_returns_unknown_package_status_when_pkg_info_missing(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    # Patch PACKAGE_REGISTRY to drop the claude entry.
    with patch.dict(
        "symphony.updater.single_provider.PACKAGE_REGISTRY", {}, clear=True
    ):
        updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
        result = await update_single_provider_impl(updater, InstrumentName.CLAUDE)
    assert result.update_skipped_reason == "unknown package"


@pytest.mark.asyncio
async def test_returns_up_to_date_when_versions_match(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    updater.get_current_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    updater.get_latest_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    result = await update_single_provider_impl(updater, InstrumentName.CLAUDE)
    assert result.needs_update is False
    # Cached.
    assert updater._last_results and updater._last_results[0].provider == InstrumentName.CLAUDE


@pytest.mark.asyncio
async def test_update_failure_reports_skip_reason(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    updater.get_current_version = AsyncMock(return_value="1.0.0")  # type: ignore[method-assign]
    updater.get_latest_version = AsyncMock(return_value="1.0.1")  # type: ignore[method-assign]
    updater.update_cli = AsyncMock(return_value=False)  # type: ignore[method-assign]
    result = await update_single_provider_impl(updater, InstrumentName.CLAUDE)
    assert result.update_skipped_reason == "update command failed"


@pytest.mark.asyncio
async def test_update_success_runs_post_update_hooks(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    updater.get_current_version = AsyncMock(side_effect=["1.0.0", "1.0.1"])  # type: ignore[method-assign]
    updater.get_latest_version = AsyncMock(return_value="1.0.1")  # type: ignore[method-assign]
    updater.update_cli = AsyncMock(return_value=True)  # type: ignore[method-assign]
    manager.restart_provider = AsyncMock()  # type: ignore[method-assign]
    manager.activate_provider = AsyncMock(return_value=True)  # type: ignore[method-assign]
    updater._rediscover_models = AsyncMock()  # type: ignore[method-assign]

    result = await update_single_provider_impl(updater, InstrumentName.CLAUDE)
    assert result.last_updated is not None
    assert result.current_version == "1.0.1"
    manager.restart_provider.assert_awaited_once()
    manager.activate_provider.assert_awaited_once()
    updater._rediscover_models.assert_awaited_once()
