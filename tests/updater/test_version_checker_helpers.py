"""Tests for symphony.updater.version_checker helpers."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import symphony.updater.version_checker as vc
from symphony.models import InstrumentName
from symphony.orchestra import Orchestra
from symphony.updater import CLIPackageInfo
from symphony.updater.version_checker import (
    _get_latest_pypi_version,
    _run_cmd_sync,
    get_current_version,
    get_latest_version,
    get_latest_version_subprocess,
    get_latest_version_via_shell,
    run_cmd,
    set_bash_path,
)
from tests.helpers.orchestra import started_orchestra


def _info(manager: str = "npm", *, update_cmd: str = "") -> CLIPackageInfo:
    return CLIPackageInfo(
        provider=InstrumentName.CODEX,
        manager=manager,
        package="example-pkg",
        update_cmd=update_cmd,
    )


# ---------------------------------------------------------------------------
# _run_cmd_sync / run_cmd
# ---------------------------------------------------------------------------


class TestRunCmdSync:
    def test_returns_stdout_and_exit_code(self) -> None:
        code, output = _run_cmd_sync("echo", "hi", timeout=5)
        assert code == 0
        assert "hi" in output

    def test_returns_minus_one_on_timeout(self) -> None:
        code, output = _run_cmd_sync(sys.executable, "-c", "import time; time.sleep(5)", timeout=1)
        assert code == -1
        assert output == ""

    def test_returns_minus_one_when_executable_missing(self) -> None:
        code, output = _run_cmd_sync("/nonexistent/binary-xyz-12345", timeout=5)
        assert code == -1
        assert output == ""

    def test_set_bash_path_updates_module_global(self) -> None:
        set_bash_path("/custom/bash")
        try:
            assert vc._bash_path == "/custom/bash"
        finally:
            set_bash_path(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_cmd_wraps_sync_in_thread() -> None:
    code, output = await run_cmd("echo", "async-hi", timeout=5)
    assert code == 0
    assert "async-hi" in output


# ---------------------------------------------------------------------------
# _get_latest_pypi_version
# ---------------------------------------------------------------------------


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Make ``httpx.AsyncClient(...)`` always return a transport-backed client."""
    original = httpx.AsyncClient

    def factory(*_args, **kwargs):
        kwargs.pop("transport", None)
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(vc.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
class TestGetLatestPypiVersion:
    async def test_returns_version_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "example-pkg" in str(request.url)
            return httpx.Response(200, json={"info": {"version": "1.2.3"}})

        _patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
        version = await _get_latest_pypi_version("example-pkg")
        assert version == "1.2.3"

    async def test_returns_none_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        _patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
        assert await _get_latest_pypi_version("example-pkg") is None

    async def test_returns_none_on_connection_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

        _patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
        assert await _get_latest_pypi_version("example-pkg") is None


# ---------------------------------------------------------------------------
# get_current_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetCurrentVersion:
    async def test_uses_subprocess_runner_when_no_idle_musician(
        self, loaded_config
    ) -> None:
        # No started orchestra → no idle musician → subprocess path.
        manager = Orchestra(loaded_config)
        captured: dict[str, tuple] = {}

        async def fake_runner(*args, timeout=60):
            captured["args"] = args
            return 0, "cli 1.0.5"

        version = await get_current_version(
            manager=manager,
            runner=fake_runner,
            executable="some-cli",
            provider=None,
        )
        assert version == "1.0.5"
        assert captured["args"] == ("some-cli", "--version")

    async def test_returns_none_when_runner_fails(self, loaded_config) -> None:
        manager = Orchestra(loaded_config)

        async def fake_runner(*args, timeout=60):
            return 1, ""

        assert (
            await get_current_version(
                manager=manager, runner=fake_runner, executable="x", provider=None
            )
            is None
        )

    async def test_uses_shell_when_idle_musician_available(self, loaded_config) -> None:
        async with started_orchestra(loaded_config) as manager:
            captured: dict[str, tuple] = {}

            async def fake_runner(*args, timeout=60):
                captured["args"] = args
                return 0, "fallback 9.9.9"

            # Patch the codex musician's shell command to emit a version-like string.
            musician = manager.get_idle_musician(InstrumentName.CODEX)
            assert musician is not None
            musician.run_quick_command = AsyncMock(return_value=(0, "codex 4.5.6"))  # type: ignore[method-assign]
            version = await get_current_version(
                manager=manager,
                runner=fake_runner,
                executable="codex",
                provider=InstrumentName.CODEX,
            )
            assert version == "4.5.6"

    async def test_falls_back_to_runner_when_shell_raises(self, loaded_config) -> None:
        async with started_orchestra(loaded_config) as manager:
            musician = manager.get_idle_musician(InstrumentName.CODEX)
            assert musician is not None
            musician.run_quick_command = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

            async def fake_runner(*args, timeout=60):
                return 0, "fallback 7.8.9"

            version = await get_current_version(
                manager=manager,
                runner=fake_runner,
                executable="codex",
                provider=InstrumentName.CODEX,
            )
            assert version == "7.8.9"


# ---------------------------------------------------------------------------
# get_latest_version (and the *_via_shell / *_subprocess split)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetLatestVersionSubprocess:
    async def test_npm_invokes_npm_view(self) -> None:
        captured: dict[str, tuple] = {}

        async def fake_runner(*args, timeout=60):
            captured["args"] = args
            return 0, "1.2.3"

        version = await get_latest_version_subprocess(
            runner=fake_runner, pkg_info=_info(manager="npm")
        )
        assert version == "1.2.3"
        assert captured["args"] == ("npm", "view", "example-pkg", "version")

    async def test_npm_returns_none_on_failure(self) -> None:
        async def fake_runner(*args, timeout=60):
            return 1, ""

        assert (
            await get_latest_version_subprocess(
                runner=fake_runner, pkg_info=_info(manager="npm")
            )
            is None
        )

    async def test_uv_uses_pypi_first(self) -> None:
        async def fake_runner(*args, timeout=60):
            raise AssertionError("should not run subprocess when pypi succeeds")

        with patch(
            "symphony.updater.version_checker._get_latest_pypi_version",
            AsyncMock(return_value="9.9.9"),
        ):
            version = await get_latest_version_subprocess(
                runner=fake_runner, pkg_info=_info(manager="uv")
            )
        assert version == "9.9.9"

    async def test_uv_falls_back_to_uv_tool_list(self) -> None:
        async def fake_runner(*args, timeout=60):
            return 0, "example-pkg v1.0.4\nother-pkg v0.1.0"

        with patch(
            "symphony.updater.version_checker._get_latest_pypi_version",
            AsyncMock(return_value=None),
        ):
            version = await get_latest_version_subprocess(
                runner=fake_runner, pkg_info=_info(manager="uv")
            )
        assert version == "1.0.4"

    async def test_uv_tool_list_failure_returns_none(self) -> None:
        async def fake_runner(*args, timeout=60):
            return 1, ""

        with patch(
            "symphony.updater.version_checker._get_latest_pypi_version",
            AsyncMock(return_value=None),
        ):
            version = await get_latest_version_subprocess(
                runner=fake_runner, pkg_info=_info(manager="uv")
            )
        assert version is None

    async def test_uv_package_not_found_in_list(self) -> None:
        async def fake_runner(*args, timeout=60):
            return 0, "other-pkg v0.1.0"

        with patch(
            "symphony.updater.version_checker._get_latest_pypi_version",
            AsyncMock(return_value=None),
        ):
            version = await get_latest_version_subprocess(
                runner=fake_runner, pkg_info=_info(manager="uv")
            )
        assert version is None

    async def test_unknown_manager_returns_none(self) -> None:
        async def fake_runner(*args, timeout=60):
            return 0, ""

        version = await get_latest_version_subprocess(
            runner=fake_runner, pkg_info=_info(manager="weird")
        )
        assert version is None


@pytest.mark.asyncio
class TestGetLatestVersionViaShell:
    async def test_npm_via_shell(self, loaded_config) -> None:
        async with started_orchestra(loaded_config) as manager:
            musician = manager.get_idle_musician(InstrumentName.CODEX)
            assert musician is not None
            musician.run_quick_command = AsyncMock(return_value=(0, "3.4.5"))  # type: ignore[method-assign]
            version = await get_latest_version_via_shell(
                musician=musician, pkg_info=_info(manager="npm")
            )
            assert version == "3.4.5"

    async def test_uv_pypi_path(self, loaded_config) -> None:
        async with started_orchestra(loaded_config) as manager:
            musician = manager.get_idle_musician(InstrumentName.CODEX)
            assert musician is not None
            with patch(
                "symphony.updater.version_checker._get_latest_pypi_version",
                AsyncMock(return_value="2.0.0"),
            ):
                version = await get_latest_version_via_shell(
                    musician=musician, pkg_info=_info(manager="uv")
                )
            assert version == "2.0.0"

    async def test_uv_falls_back_to_local_list(self, loaded_config) -> None:
        async with started_orchestra(loaded_config) as manager:
            musician = manager.get_idle_musician(InstrumentName.CODEX)
            assert musician is not None
            musician.run_quick_command = AsyncMock(  # type: ignore[method-assign]
                return_value=(0, "example-pkg v0.1.2\nother v9")
            )
            with patch(
                "symphony.updater.version_checker._get_latest_pypi_version",
                AsyncMock(return_value=None),
            ):
                version = await get_latest_version_via_shell(
                    musician=musician, pkg_info=_info(manager="uv")
                )
            assert version == "0.1.2"

    async def test_returns_none_for_unknown_manager(self, loaded_config) -> None:
        async with started_orchestra(loaded_config) as manager:
            musician = manager.get_idle_musician(InstrumentName.CODEX)
            assert musician is not None
            version = await get_latest_version_via_shell(
                musician=musician, pkg_info=_info(manager="totally-unknown")
            )
            assert version is None


@pytest.mark.asyncio
async def test_get_latest_version_falls_back_to_subprocess(loaded_config) -> None:
    """When the shell branch raises, the subprocess path runs."""
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_idle_musician(InstrumentName.CODEX)
        assert musician is not None
        musician.run_quick_command = AsyncMock(side_effect=RuntimeError("shell-fail"))  # type: ignore[method-assign]

        async def fake_runner(*args, timeout=60):
            return 0, "fallback 5.5.5"

        version = await get_latest_version(
            manager=manager, runner=fake_runner, pkg_info=_info(manager="npm")
        )
        assert version == "5.5.5"


@pytest.mark.asyncio
async def test_get_latest_version_runs_subprocess_when_no_musician(loaded_config) -> None:
    manager = Orchestra(loaded_config)

    async def fake_runner(*args, timeout=60):
        return 0, "1.0.0"

    version = await get_latest_version(
        manager=manager, runner=fake_runner, pkg_info=_info(manager="npm")
    )
    assert version == "1.0.0"
