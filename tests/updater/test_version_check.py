from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from symphony.config import UpdaterConfig
from symphony.models import InstrumentName
from symphony.updater import (
    CLIPackageInfo,
    CLIUpdater,
    PACKAGE_REGISTRY,
    _parse_version,
    _version_tuple,
)
from symphony.orchestra import Orchestra


class TestParseVersion:
    def test_simple_semver(self):
        assert _parse_version("1.2.3") == "1.2.3"

    def test_prefix_text(self):
        assert _parse_version("claude v1.0.16") == "1.0.16"

    def test_at_version(self):
        assert _parse_version("@anthropic-ai/claude-code@0.3.1") == "0.3.1"

    def test_no_match(self):
        assert _parse_version("no version here") is None

    def test_empty(self):
        assert _parse_version("") is None

    def test_multiline_output(self):
        output = "Antigravity CLI\nVersion: 0.5.2\nNode.js v20.0.0"
        assert _parse_version(output) == "0.5.2"


class TestVersionTuple:
    def test_basic(self):
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_comparison(self):
        assert _version_tuple("1.2.3") < _version_tuple("1.3.0")
        assert _version_tuple("2.0.0") > _version_tuple("1.99.99")
        assert _version_tuple("1.0.0") == _version_tuple("1.0.0")


class TestPackageRegistry:
    def test_all_providers_registered(self):
        assert "claude" in PACKAGE_REGISTRY
        assert "codex" in PACKAGE_REGISTRY
        assert "agy" in PACKAGE_REGISTRY
        assert "kimi" in PACKAGE_REGISTRY
        assert "opencode" in PACKAGE_REGISTRY

    def test_opencode_native_upgrade(self):
        info = PACKAGE_REGISTRY["opencode"]
        assert info.manager == "native"
        assert info.package == "opencode-ai"
        assert info.update_cmd == "opencode upgrade"
        assert info.provider == InstrumentName.OPENCODE

    def test_claude_is_native(self):
        info = PACKAGE_REGISTRY["claude"]
        assert info.manager == "native"
        assert info.package == "@anthropic-ai/claude-code"
        assert info.update_cmd == "claude update"

    def test_kimi_is_uv(self):
        info = PACKAGE_REGISTRY["kimi"]
        assert info.manager == "uv"
        assert info.package == "kimi-cli"
        assert info.update_cmd == ""

    def test_antigravity_native_update_via_manifest(self):
        info = PACKAGE_REGISTRY["agy"]
        assert info.manager == "native"
        assert info.package == "agy"
        assert info.provider == InstrumentName.ANTIGRAVITY
        # Antigravity ships a native ``agy update`` subcommand; the curl
        # install script is not an upgrade path (it no-ops when the binary
        # exists and rejects Windows outright).
        assert info.update_cmd == "agy update"
        # ``agy`` is not on npm, so the latest version is read from the
        # auto-updater platform manifest.
        assert "{platform}" in info.manifest_url
        assert info.manifest_url.endswith(".json")
        assert "antigravity-cli-auto-updater" in info.manifest_url


@pytest.fixture()
def updater(loaded_config):
    manager = Orchestra(loaded_config)
    config = UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True)
    return CLIUpdater(manager=manager, config=config)


class TestGetCurrentVersion:
    @pytest.mark.asyncio()
    async def test_parses_version_from_stdout(self, updater):
        with patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (0, "claude v1.0.16\n")
            version = await updater.get_current_version("claude")
            assert version == "1.0.16"
            mock_cmd.assert_called_once_with("claude", "--version")

    @pytest.mark.asyncio()
    async def test_returns_none_on_failure(self, updater):
        with patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (-1, "")
            version = await updater.get_current_version("claude")
            assert version is None


class TestGetLatestVersion:
    @pytest.mark.asyncio()
    async def test_npm_package(self, updater):
        pkg = CLIPackageInfo(InstrumentName.CLAUDE, "npm", "@anthropic-ai/claude-code")
        with patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (0, "1.0.17\n")
            version = await updater.get_latest_version(pkg)
            assert version == "1.0.17"
            mock_cmd.assert_called_once_with("npm", "view", "@anthropic-ai/claude-code", "version")

    @pytest.mark.asyncio()
    async def test_uv_package_via_pypi(self, updater):
        pkg = CLIPackageInfo(InstrumentName.KIMI, "uv", "kimi-cli")
        with patch("symphony.updater.version_checker._get_latest_pypi_version", new_callable=AsyncMock) as mock_pypi:
            mock_pypi.return_value = "1.25.0"
            version = await updater.get_latest_version(pkg)
            assert version == "1.25.0"
            mock_pypi.assert_called_once_with("kimi-cli")

    @pytest.mark.asyncio()
    async def test_uv_package_pypi_fallback_to_uv_tool_list(self, updater):
        """When PyPI is unreachable, falls back to uv tool list."""
        pkg = CLIPackageInfo(InstrumentName.KIMI, "uv", "kimi-cli")
        with (
            patch("symphony.updater.version_checker._get_latest_pypi_version", new_callable=AsyncMock) as mock_pypi,
            patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd,
        ):
            mock_pypi.return_value = None
            mock_cmd.return_value = (0, "kimi-cli v1.2.0\n- kimi\nother-tool v0.1.0\n")
            version = await updater.get_latest_version(pkg)
            assert version == "1.2.0"

    @pytest.mark.asyncio()
    async def test_uv_package_not_found(self, updater):
        """When both PyPI and uv tool list fail to find the package."""
        pkg = CLIPackageInfo(InstrumentName.KIMI, "uv", "kimi-cli")
        with (
            patch("symphony.updater.version_checker._get_latest_pypi_version", new_callable=AsyncMock) as mock_pypi,
            patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd,
        ):
            mock_pypi.return_value = None
            mock_cmd.return_value = (0, "other-tool v0.1.0\n")
            version = await updater.get_latest_version(pkg)
            assert version is None

    @pytest.mark.asyncio()
    async def test_npm_failure(self, updater):
        pkg = CLIPackageInfo(InstrumentName.CLAUDE, "npm", "@anthropic-ai/claude-code")
        with patch.object(updater, "_run_cmd", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (1, "npm ERR!")
            version = await updater.get_latest_version(pkg)
            assert version is None


class TestIsProviderIdle:
    @pytest.mark.asyncio()
    async def test_idle_when_no_musicians(self, updater):
        assert updater.is_provider_idle(InstrumentName.CLAUDE) is True

    @pytest.mark.asyncio()
    async def test_idle_when_musicians_not_busy(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            config = UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True)
            checker = CLIUpdater(manager=manager, config=config)
            assert checker.is_provider_idle(InstrumentName.CLAUDE) is True
        finally:
            await manager.stop()


class TestProbeVersionsOnly:
    """The probe-only path is what the lazy GET /v1/cli-versions hits on
    a cold cache. It must run version checks in parallel and never trigger
    an install — that's what makes the first response arrive in seconds
    instead of minutes."""

    @pytest.mark.asyncio()
    async def test_returns_status_for_each_available_provider(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(
                manager=manager,
                config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True),
            )
            with patch.object(
                checker, "get_current_version", new_callable=AsyncMock
            ) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.0.0"
                results = await checker.probe_versions_only()
                assert len(results) == 5
                assert all(not status.needs_update for status in results)
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_does_not_trigger_install_when_outdated(self, loaded_config):
        """Even with auto_update=True and updates available, probe-only
        must never call ``update_cli`` — that's the whole point of the
        split."""
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(
                manager=manager,
                config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=True),
            )
            with patch.object(
                checker, "get_current_version", new_callable=AsyncMock
            ) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest, patch.object(
                checker, "update_cli", new_callable=AsyncMock
            ) as mock_update:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.1.0"
                results = await checker.probe_versions_only()
                assert len(results) == 5
                assert all(status.needs_update for status in results)
                mock_update.assert_not_called()
        finally:
            await manager.stop()

    @pytest.mark.asyncio()
    async def test_populates_last_results_cache(self, loaded_config):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            checker = CLIUpdater(
                manager=manager,
                config=UpdaterConfig(enabled=True, interval_hours=4.0, auto_update=False),
            )
            with patch.object(
                checker, "get_current_version", new_callable=AsyncMock
            ) as mock_curr, patch.object(
                checker, "get_latest_version", new_callable=AsyncMock
            ) as mock_latest:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.0.0"
                await checker.probe_versions_only()
                assert len(checker.last_results) == 5
        finally:
            await manager.stop()


class TestAPIEndpoints:
    def test_cli_versions_lazy_loads_when_cache_empty(self, config_path):
        """GET /v1/cli-versions runs a probe-only pass when the updater
        cache is empty, so callers don't see an empty window during
        Symphony start-up — and the response arrives in seconds rather
        than blocking on installs."""
        from fastapi.testclient import TestClient
        from symphony.service import create_app

        app = create_app()
        with TestClient(app) as client:
            with patch(
                "symphony.updater.updater.CLIUpdater.get_current_version",
                new_callable=AsyncMock,
            ) as mock_curr, patch(
                "symphony.updater.updater.CLIUpdater.get_latest_version",
                new_callable=AsyncMock,
            ) as mock_latest, patch(
                "symphony.updater.updater.CLIUpdater.update_cli",
                new_callable=AsyncMock,
            ) as mock_update:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.0.0"
                response = client.get("/v1/cli-versions")
                assert response.status_code == 200
                data = response.json()
                assert len(data) == 5
                assert mock_curr.await_count >= 1
                assert mock_latest.await_count >= 1
                # Lazy GET must never block on installs.
                mock_update.assert_not_called()

    def test_cli_versions_get_does_not_install_when_outdated(self, config_path):
        """GET must not run an install even when versions are stale —
        installs are the periodic loop's job, not the request's."""
        from fastapi.testclient import TestClient
        from symphony.service import create_app

        app = create_app()
        with TestClient(app) as client:
            with patch(
                "symphony.updater.updater.CLIUpdater.get_current_version",
                new_callable=AsyncMock,
            ) as mock_curr, patch(
                "symphony.updater.updater.CLIUpdater.get_latest_version",
                new_callable=AsyncMock,
            ) as mock_latest, patch(
                "symphony.updater.updater.CLIUpdater.update_cli",
                new_callable=AsyncMock,
            ) as mock_update:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.1.0"
                response = client.get("/v1/cli-versions")
                assert response.status_code == 200
                assert all(item["needs_update"] for item in response.json())
                mock_update.assert_not_called()

    def test_cli_versions_returns_cached_results(self, config_path):
        """After results are cached, subsequent GETs don't trigger a new check."""
        from fastapi.testclient import TestClient
        from symphony.service import create_app

        app = create_app()
        with TestClient(app) as client:
            with patch(
                "symphony.updater.updater.CLIUpdater.get_current_version",
                new_callable=AsyncMock,
            ) as mock_curr, patch(
                "symphony.updater.updater.CLIUpdater.get_latest_version",
                new_callable=AsyncMock,
            ) as mock_latest:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.0.0"
                # First call populates the cache.
                client.get("/v1/cli-versions")
                prior_count = mock_curr.await_count
                # Second call should hit the cache.
                response = client.get("/v1/cli-versions")
                assert response.status_code == 200
                assert len(response.json()) == 5
                assert mock_curr.await_count == prior_count

    def test_cli_versions_check_returns_results(self, config_path):
        from fastapi.testclient import TestClient
        from symphony.service import create_app

        app = create_app()
        with TestClient(app) as client:
            with patch("symphony.updater.updater.CLIUpdater.get_current_version", new_callable=AsyncMock) as mock_curr, patch(
                "symphony.updater.updater.CLIUpdater.get_latest_version", new_callable=AsyncMock
            ) as mock_latest:
                mock_curr.return_value = "1.0.0"
                mock_latest.return_value = "1.0.0"
                response = client.post("/v1/cli-versions/check")
                assert response.status_code == 200
                data = response.json()
                assert len(data) == 5
                for item in data:
                    assert "provider" in item
                    assert "current_version" in item
                    assert "latest_version" in item
                    assert "needs_update" in item
