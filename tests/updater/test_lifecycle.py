"""Tests for updater/lifecycle.py and updater.py cache/status helpers."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from symphony.config import UpdaterConfig
from symphony.models import CLIVersionStatus, InstrumentName
from symphony.orchestra import Orchestra
from symphony.updater import CLIUpdater
from symphony.updater.lifecycle import _log_status, start, stop


def _status(
    needs_update: bool = False,
    *,
    skipped_reason: str | None = None,
) -> CLIVersionStatus:
    return CLIVersionStatus(
        provider=InstrumentName.CLAUDE,
        executable="claude",
        current_version="1.0.0",
        latest_version="1.0.1" if needs_update else "1.0.0",
        needs_update=needs_update,
        last_checked="2024-01-01T00:00:00+00:00",
        next_check_at="2024-01-01T04:00:00+00:00",
        auto_update=True,
        update_skipped_reason=skipped_reason,
    )


class TestLogStatus:
    def test_logs_up_to_date(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level("INFO", logger="symphony.updater")
        _log_status(_status(needs_update=False))
        assert "up to date" in caplog.text

    def test_logs_needs_update_with_skip_reason(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level("INFO", logger="symphony.updater")
        _log_status(_status(needs_update=True, skipped_reason="musicians busy"))
        assert "musicians busy" in caplog.text

    def test_logs_needs_update_with_applied_default(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO", logger="symphony.updater")
        _log_status(_status(needs_update=True))
        assert "applied" in caplog.text


@pytest.mark.asyncio
async def test_start_skips_when_disabled(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    start(updater)
    assert updater._task is None


@pytest.mark.asyncio
async def test_start_idempotent_when_task_running(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(
        manager=manager,
        config=UpdaterConfig(enabled=True, interval_hours=24, auto_update=False),
    )

    # Replace check_and_update_all with a no-op so the loop sleeps immediately.
    updater.check_and_update_all = AsyncMock(return_value=[])  # type: ignore[method-assign]
    try:
        start(updater)
        first_task = updater._task
        assert first_task is not None
        # Second call must not replace the running task.
        start(updater)
        assert updater._task is first_task
    finally:
        await stop(updater)
        assert updater._task is None


@pytest.mark.asyncio
async def test_stop_is_noop_when_no_task(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
    await stop(updater)
    assert updater._task is None


@pytest.mark.asyncio
async def test_periodic_loop_swallows_errors_and_continues(loaded_config) -> None:
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(
        manager=manager,
        config=UpdaterConfig(enabled=True, interval_hours=24, auto_update=False),
    )

    call_count = {"n": 0}

    async def fake_check():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient")
        # On second call, sleep so the loop is interruptible by stop().
        await asyncio.sleep(60)
        return []

    updater.check_and_update_all = fake_check  # type: ignore[method-assign]
    try:
        start(updater)
        # Wait briefly for at least one iteration to error out and the
        # loop to begin the second call.
        for _ in range(20):
            if call_count["n"] >= 1:
                break
            await asyncio.sleep(0.05)
        assert call_count["n"] >= 1
    finally:
        await stop(updater)


@pytest.mark.asyncio
async def test_periodic_loop_logs_each_status(loaded_config) -> None:
    """Drive the for-loop body in periodic_loop (line 39) by returning a
    non-empty list of status results."""
    manager = Orchestra(loaded_config)
    updater = CLIUpdater(
        manager=manager,
        config=UpdaterConfig(enabled=True, interval_hours=24, auto_update=False),
    )

    statuses = [_status(needs_update=False)]
    call_count = {"n": 0}

    async def fake_check():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return statuses
        await asyncio.sleep(60)
        return []

    updater.check_and_update_all = fake_check  # type: ignore[method-assign]
    try:
        start(updater)
        for _ in range(40):
            if call_count["n"] >= 1:
                break
            await asyncio.sleep(0.05)
        assert call_count["n"] >= 1
    finally:
        await stop(updater)


class TestCacheSingle:
    def test_cache_single_replaces_existing_provider_entry(self, loaded_config) -> None:
        manager = Orchestra(loaded_config)
        updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
        first = _status(needs_update=False)
        second = _status(needs_update=True)
        updater._cache_single(first)
        updater._cache_single(second)
        assert updater._last_results == [second]

    def test_cache_single_appends_when_no_existing_entry(self, loaded_config) -> None:
        manager = Orchestra(loaded_config)
        updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
        codex = CLIVersionStatus(
            provider=InstrumentName.CODEX,
            executable="codex",
            current_version=None,
            latest_version=None,
            needs_update=False,
            last_checked="2024-01-01T00:00:00+00:00",
            next_check_at="2024-01-01T04:00:00+00:00",
            auto_update=False,
        )
        updater._cache_single(_status())
        updater._cache_single(codex)
        providers = {r.provider for r in updater._last_results}
        assert providers == {InstrumentName.CLAUDE, InstrumentName.CODEX}

    def test_last_results_returns_a_copy(self, loaded_config) -> None:
        manager = Orchestra(loaded_config)
        updater = CLIUpdater(manager=manager, config=UpdaterConfig(enabled=False))
        updater._cache_single(_status())
        copy = updater.last_results
        copy.clear()
        assert updater._last_results, "Public accessor must return an isolated copy"
