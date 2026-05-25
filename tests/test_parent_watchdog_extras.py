"""Additional coverage for parent_watchdog edge cases."""
from __future__ import annotations

import asyncio
import os

import pytest

import symphony.parent_watchdog as parent_watchdog


class _FakeSys:
    def __init__(self, platform: str) -> None:
        self.platform = platform


@pytest.fixture(autouse=True)
async def _reset() -> None:
    await parent_watchdog.stop_parent_watchdog()
    yield
    await parent_watchdog.stop_parent_watchdog()


@pytest.mark.asyncio
async def test_start_is_idempotent_when_task_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parent_watchdog, "sys", _FakeSys("linux"), raising=False)
    monkeypatch.setenv("MAESTRO_PARENT_PID", str(os.getpid()))

    parent_watchdog.start_parent_watchdog()
    first_task = parent_watchdog._task
    assert first_task is not None

    # Second call must early-return; the task reference must not change.
    parent_watchdog.start_parent_watchdog()
    assert parent_watchdog._task is first_task


@pytest.mark.asyncio
async def test_watch_returns_when_sleep_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the watch task during ``asyncio.sleep`` must exit cleanly."""
    monkeypatch.setattr(parent_watchdog, "_POLL_INTERVAL_SECONDS", 10.0)

    task = asyncio.create_task(parent_watchdog._watch(os.getpid()))
    # Let the loop enter asyncio.sleep.
    await asyncio.sleep(0.05)
    task.cancel()
    # The cancellation should propagate inside the except branch and return.
    await task
    assert task.done() and task.exception() is None
