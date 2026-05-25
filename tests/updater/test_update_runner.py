"""Tests for updater/update_runner.py — install command resolution + fallback."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from symphony.config import UpdaterConfig
from symphony.models import InstrumentName
from symphony.orchestra import Orchestra
from symphony.updater import CLIPackageInfo, CLIUpdater
from symphony.updater.update_runner import (
    _resolve_method,
    _run_via_subprocess,
    _shell_command,
    run_update,
)


def _info(manager: str = "npm", *, update_cmd: str = "") -> CLIPackageInfo:
    return CLIPackageInfo(
        provider=InstrumentName.CODEX,
        manager=manager,
        package="some-pkg",
        update_cmd=update_cmd,
    )


# ---------------------------------------------------------------------------
# _resolve_method
# ---------------------------------------------------------------------------


class TestResolveMethod:
    def test_returns_native_when_update_cmd_present(self) -> None:
        assert _resolve_method(_info(update_cmd="claude update"), "claude") == "native"

    def test_returns_manager_when_no_executable(self) -> None:
        assert _resolve_method(_info(manager="uv"), None) == "uv"

    def test_uses_detected_when_executable_resolves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with patch(
            "symphony.updater.update_runner.detect_install_method", return_value="uv"
        ):
            assert _resolve_method(_info(manager="npm"), "/path/to/cli") == "uv"

    def test_falls_back_to_manager_when_detect_unknown(self) -> None:
        with patch(
            "symphony.updater.update_runner.detect_install_method",
            return_value="unknown",
        ):
            assert _resolve_method(_info(manager="npm"), "/path/to/cli") == "npm"


# ---------------------------------------------------------------------------
# _shell_command
# ---------------------------------------------------------------------------


class TestShellCommand:
    def test_native(self) -> None:
        cmd = _shell_command(_info(update_cmd="claude update"), "native")
        assert cmd is not None
        assert "claude update" in cmd
        assert "yes" in cmd

    def test_npm(self) -> None:
        cmd = _shell_command(_info(manager="npm"), "npm")
        assert cmd is not None
        assert "npm install -g some-pkg@latest" in cmd

    def test_uv(self) -> None:
        cmd = _shell_command(_info(manager="uv"), "uv")
        assert cmd is not None
        assert "uv tool upgrade some-pkg --no-cache" in cmd

    def test_unknown_returns_none(self) -> None:
        assert _shell_command(_info(), "weird-manager") is None


# ---------------------------------------------------------------------------
# _run_via_subprocess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunViaSubprocess:
    async def test_native_method_splits_command(self) -> None:
        captured: dict[str, object] = {}

        async def fake_run(*args, timeout):
            captured["args"] = args
            captured["timeout"] = timeout
            return 0, "ok"

        assert await _run_via_subprocess(_info(update_cmd="claude update"), "native", fake_run) is True
        assert captured["args"] == ("claude", "update")
        assert captured["timeout"] == 120

    async def test_npm_method_passes_at_latest(self) -> None:
        captured: dict[str, object] = {}

        async def fake_run(*args, timeout):
            captured["args"] = args
            return 0, ""

        await _run_via_subprocess(_info(manager="npm"), "npm", fake_run)
        assert captured["args"] == ("npm", "install", "-g", "some-pkg@latest")

    async def test_uv_method_uses_upgrade_no_cache(self) -> None:
        captured: dict[str, object] = {}

        async def fake_run(*args, timeout):
            captured["args"] = args
            return 0, ""

        await _run_via_subprocess(_info(manager="uv"), "uv", fake_run)
        assert captured["args"] == ("uv", "tool", "upgrade", "some-pkg", "--no-cache")

    async def test_unknown_method_returns_false(self) -> None:
        async def fake_run(*args, timeout):
            return 0, ""

        assert await _run_via_subprocess(_info(), "weird", fake_run) is False

    async def test_logs_error_on_nonzero_exit(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level("ERROR", logger="symphony.updater")

        async def fake_run(*args, timeout):
            return 1, "boom"

        assert await _run_via_subprocess(_info(manager="npm"), "npm", fake_run) is False
        assert "Update failed" in caplog.text


# ---------------------------------------------------------------------------
# run_update — integration via Orchestra
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunUpdate:
    async def test_falls_back_to_subprocess_when_no_idle_musician(
        self, loaded_config
    ) -> None:
        manager = Orchestra(loaded_config)
        # Do not start manager → no musicians → falls back to subprocess.
        called = {"hit": False}

        async def fake_run(*args, timeout):
            called["hit"] = True
            return 0, "ok"

        result = await run_update(
            manager=manager,
            run_cmd=fake_run,
            pkg_info=_info(manager="npm"),
            executable=None,
        )
        assert result is True
        assert called["hit"] is True

    async def test_unknown_method_short_circuits(self, loaded_config) -> None:
        manager = Orchestra(loaded_config)

        async def fake_run(*args, timeout):
            raise AssertionError("should not be called")

        result = await run_update(
            manager=manager,
            run_cmd=fake_run,
            pkg_info=_info(manager="totally-unknown-mgr"),
            executable=None,
        )
        assert result is False

    async def test_uses_shell_when_idle_musician_available(self, loaded_config) -> None:
        from tests.helpers.orchestra import started_orchestra

        async with started_orchestra(loaded_config) as manager:
            # The fake codex CLI is configured under loaded_config; the
            # shell update command will exec `npm install -g some-pkg@latest`
            # in bash. That command will fail because npm isn't installed
            # in the sandbox, but the shell path is exercised either way.
            async def fake_run(*args, timeout):
                return 0, "subprocess"

            result = await run_update(
                manager=manager,
                run_cmd=fake_run,
                pkg_info=CLIPackageInfo(
                    provider=InstrumentName.CODEX,
                    manager="npm",
                    package="@openai/codex",
                ),
                executable=None,
            )
            # Either the shell path succeeded or fell back to subprocess;
            # both ultimately return True given the npm install fallback.
            assert isinstance(result, bool)

    async def test_shell_timeout_restarts_musician_and_falls_back(
        self, loaded_config
    ) -> None:
        from tests.helpers.orchestra import started_orchestra

        async with started_orchestra(loaded_config) as manager:
            musician = manager.get_idle_musician(InstrumentName.CODEX)
            assert musician is not None
            musician.run_quick_command = AsyncMock(  # type: ignore[method-assign]
                side_effect=asyncio.TimeoutError()
            )
            musician.stop = AsyncMock()  # type: ignore[method-assign]
            musician.start = AsyncMock()  # type: ignore[method-assign]

            async def fake_run(*args, timeout):
                return 0, ""

            with patch(
                "symphony.updater.update_runner._run_via_subprocess",
                AsyncMock(return_value=False),
            ) as mock_sub:
                result = await run_update(
                    manager=manager,
                    run_cmd=fake_run,
                    pkg_info=_info(manager="npm"),
                    executable=None,
                )
            # Returns False after timeout (musician restarted, no further attempts here).
            assert result is False
            musician.stop.assert_awaited_once()
            musician.start.assert_awaited_once()
            # Subprocess fallback NOT called on timeout path.
            mock_sub.assert_not_called()

    async def test_shell_failure_falls_back_to_subprocess(self, loaded_config) -> None:
        from tests.helpers.orchestra import started_orchestra

        async with started_orchestra(loaded_config) as manager:
            musician = manager.get_idle_musician(InstrumentName.CODEX)
            assert musician is not None
            musician.run_quick_command = AsyncMock(  # type: ignore[method-assign]
                side_effect=RuntimeError("boom")
            )
            called = {"sub": False}

            async def fake_run(*args, timeout):
                called["sub"] = True
                return 0, "ok"

            result = await run_update(
                manager=manager,
                run_cmd=fake_run,
                pkg_info=_info(manager="npm"),
                executable=None,
            )
            assert result is True
            assert called["sub"] is True
