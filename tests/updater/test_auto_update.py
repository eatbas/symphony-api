from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from symphony.config import UpdaterConfig
from symphony.models import InstrumentName
from symphony.updater import CLIPackageInfo, CLIUpdater
from symphony.orchestra import Orchestra


@pytest.fixture()
def updater(loaded_config):
    manager = Orchestra(loaded_config)
    config = UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True)
    return CLIUpdater(manager=manager, config=config)


class TestUpdateCli:
    @pytest.mark.asyncio()
    async def test_npm_update_success(self, updater):
        pkg = CLIPackageInfo(InstrumentName.CLAUDE, "npm", "@anthropic-ai/claude-code")
        with patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (0, "added 1 package")
            result = await updater.update_cli(pkg)
            assert result is True
            mock_cmd.assert_called_once_with("npm", "install", "-g", "@anthropic-ai/claude-code@latest", timeout=120)

    @pytest.mark.asyncio()
    async def test_uv_update_success(self, updater):
        pkg = CLIPackageInfo(InstrumentName.KIMI, "uv", "kimi-cli")
        with patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (0, "Updated kimi-cli")
            result = await updater.update_cli(pkg)
            assert result is True
            mock_cmd.assert_called_once_with("uv", "tool", "upgrade", "kimi-cli", "--no-cache", timeout=120)

    @pytest.mark.asyncio()
    async def test_update_failure(self, updater):
        pkg = CLIPackageInfo(InstrumentName.CLAUDE, "npm", "@anthropic-ai/claude-code")
        with patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (1, "npm ERR!")
            result = await updater.update_cli(pkg)
            assert result is False

    @pytest.mark.asyncio()
    async def test_update_cli_unknown_manager(self, updater):
        pkg = CLIPackageInfo(InstrumentName.CLAUDE, "pip", "some-package")
        result = await updater.update_cli(pkg)
        assert result is False


class TestCheckAndUpdateAll:
    @pytest.mark.asyncio()
    async def test_up_to_date_no_update(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True))
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.0.0"
                results = await checker.check_and_update_all()
                assert len(results) == 5
                assert all(not status.needs_update for status in results)
                mock_update.assert_not_called()
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_outdated_and_idle_triggers_update(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True))
            call_counts: dict[str, int] = {}

            async def version_side_effect(executable, provider=None):
                key = str(provider or executable)
                call_counts[key] = call_counts.get(key, 0) + 1
                return "1.0.0" if call_counts[key] == 1 else "1.1.0"

            with patch.object(checker, "get_current_version", side_effect=version_side_effect), patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update, patch.object(
                manager, "restart_provider", new_callable=AsyncMock
            ):
                mock_latest.return_value = "1.1.0"
                mock_update.return_value = True
                results = await checker.check_and_update_all()
                assert len(results) == 5
                assert mock_update.call_count == 5
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_outdated_but_busy_skips(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True))
            manager.musicians_for_provider(InstrumentName.CLAUDE)[0].busy = True
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock):
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.1.0"
                results = await checker.check_and_update_all()
                claude_result = next(r for r in results if r.provider == InstrumentName.CLAUDE)
                assert claude_result.needs_update is True
                assert claude_result.update_skipped_reason == "musicians busy"
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_auto_update_disabled(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=False))
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.1.0"
                results = await checker.check_and_update_all()
                assert all(status.update_skipped_reason == "auto_update disabled" for status in results)
                mock_update.assert_not_called()
        finally:
            await manager.stop()


class TestUpdateSingleProvider:
    @pytest.mark.asyncio()
    async def test_force_update_single_provider(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=False))
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update, patch.object(
                manager, "restart_provider", new_callable=AsyncMock
            ):
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.1.0"
                mock_update.return_value = True
                result = await checker.update_single_provider(InstrumentName.CLAUDE)
                assert result.provider == InstrumentName.CLAUDE
                assert mock_update.call_count == 1
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_force_update_already_up_to_date(self, loaded_config):
        """Manual update returns early when already on latest version."""
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=False))
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update:
                mock_curr.return_value = "1.1.0"
                mock_latest.return_value = "1.1.0"
                result = await checker.update_single_provider(InstrumentName.CLAUDE)
                assert result.provider == InstrumentName.CLAUDE
                assert not result.needs_update
                mock_update.assert_not_called()
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_force_update_busy_musicians(self, loaded_config):
        """Manual update proceeds even when musicians are busy (subprocess fallback)."""
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True))
            manager.musicians_for_provider(InstrumentName.CLAUDE)[0].busy = True
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update, patch.object(
                manager, "restart_provider", new_callable=AsyncMock
            ):
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.1.0"
                mock_update.return_value = True
                result = await checker.update_single_provider(InstrumentName.CLAUDE)
                assert result.update_skipped_reason is None
                assert mock_update.call_count == 1
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_force_update_failure(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True))
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.1.0"
                mock_update.return_value = False
                result = await checker.update_single_provider(InstrumentName.CLAUDE)
                assert result.update_skipped_reason == "update command failed"
        finally:
            await manager.stop()


class TestRediscoveryIntegration:
    @pytest.mark.asyncio()
    async def test_rediscovery_called_after_successful_update(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True))
            call_counts: dict[str, int] = {}

            async def version_side_effect(executable, provider=None):
                key = str(provider or executable)
                call_counts[key] = call_counts.get(key, 0) + 1
                return "1.0.0" if call_counts[key] == 1 else "1.1.0"

            with patch.object(checker, "get_current_version", side_effect=version_side_effect), patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update, patch.object(
                manager, "restart_provider", new_callable=AsyncMock
            ), patch.object(checker, "_rediscover_models", new_callable=AsyncMock) as mock_rediscover:
                mock_latest.return_value = "1.1.0"
                mock_update.return_value = True
                await checker.check_and_update_all()
                assert mock_rediscover.call_count == 5

        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_rediscovery_not_called_after_failed_update(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True))
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update, patch.object(
                checker, "_rediscover_models", new_callable=AsyncMock
            ) as mock_rediscover:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.1.0"
                mock_update.return_value = False
                await checker.check_and_update_all()
                mock_rediscover.assert_not_called()
        finally:
            await manager.stop()


class TestLifecycleAndCache:
    @pytest.mark.asyncio()
    async def test_start_stop(self, updater):
        updater.start()
        assert updater._task is not None
        await updater.stop()
        assert updater._task is None

    @pytest.mark.asyncio()
    async def test_disabled_does_not_start(self, loaded_config):
        manager = Orchestra(loaded_config)
        checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False, interval_hours=4.0, auto_update=True))
        checker.start()
        assert checker._task is None

    @pytest.mark.asyncio()
    async def test_last_results_populated(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=False))
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.0.0"
                await checker.check_and_update_all()
                assert len(checker.last_results) == 5
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_version_check_with_none_versions(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True))
            with patch.object(checker, "get_current_version", new_callable=AsyncMock) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(checker, "update_cli", new_callable=AsyncMock) as mock_update:
                mock_curr.return_value = None
                mock_latest.return_value = None
                results = await checker.check_and_update_all()
                assert all(not status.needs_update for status in results)
                mock_update.assert_not_called()
        finally:
            await manager.stop()
