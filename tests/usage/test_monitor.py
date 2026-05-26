"""Unit tests for ``UsageMonitor`` and ``probe_provider``."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from symphony.models import InstrumentName, UsageSnapshot
from symphony.usage.monitor import UsageMonitor
from symphony.usage.runner import probe_provider


class _FakeProviderConfig:
    def __init__(
        self,
        *,
        enabled: bool = True,
        executable: str = "/usr/bin/fake",
        models: list[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.executable = executable
        self.models = models or []


class _FakeConfig:
    def __init__(self, providers: dict[InstrumentName, _FakeProviderConfig]) -> None:
        self.providers = providers


def _make_manager(
    *,
    providers: dict[InstrumentName, _FakeProviderConfig],
    available: dict[InstrumentName, bool],
    adapters: dict[InstrumentName, object],
):
    manager = MagicMock()
    manager.config = _FakeConfig(providers=providers)
    manager.available_providers = available
    manager.registry = adapters
    manager.get_idle_musician = MagicMock(return_value=None)
    return manager


async def test_probe_all_caches_snapshots_per_provider() -> None:
    snapshot = UsageSnapshot(
        provider=InstrumentName.CLAUDE,
        supported=False,
        source="not_supported",
        note="x",
    )
    adapter = MagicMock()
    adapter.resolve_executable = MagicMock(return_value="claude")
    adapter.get_usage = AsyncMock(return_value=[snapshot])

    manager = _make_manager(
        providers={InstrumentName.CLAUDE: _FakeProviderConfig()},
        available={InstrumentName.CLAUDE: True},
        adapters={InstrumentName.CLAUDE: adapter},
    )
    monitor = UsageMonitor(manager)

    results = await monitor.probe_all()

    assert results == [snapshot]
    assert monitor.last_for(InstrumentName.CLAUDE) == [snapshot]


async def test_probe_all_returns_empty_with_no_available_providers() -> None:
    manager = _make_manager(providers={}, available={}, adapters={})
    monitor = UsageMonitor(manager)

    assert await monitor.probe_all() == []


async def test_probe_all_skips_disabled_providers() -> None:
    adapter = MagicMock()
    adapter.get_usage = AsyncMock(return_value=[])
    manager = _make_manager(
        providers={InstrumentName.CLAUDE: _FakeProviderConfig(enabled=False)},
        available={InstrumentName.CLAUDE: True},
        adapters={InstrumentName.CLAUDE: adapter},
    )
    monitor = UsageMonitor(manager)

    assert await monitor.probe_all() == []


async def test_probe_single_serialises_with_probe_all() -> None:
    """A manual single-provider refresh must wait for an in-flight
    ``probe_all`` -- otherwise the wholesale cache replacement at the end
    of ``probe_all`` would silently overwrite the single-provider write.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_get_usage(**_kwargs):
        started.set()
        await release.wait()
        return [
            UsageSnapshot(
                provider=InstrumentName.CLAUDE,
                supported=True,
                source="session_log",
            )
        ]

    adapter = MagicMock()
    adapter.resolve_executable = MagicMock(return_value="claude")
    adapter.get_usage = slow_get_usage

    manager = _make_manager(
        providers={InstrumentName.CLAUDE: _FakeProviderConfig()},
        available={InstrumentName.CLAUDE: True},
        adapters={InstrumentName.CLAUDE: adapter},
    )
    monitor = UsageMonitor(manager)

    probe_all_task = asyncio.create_task(monitor.probe_all())
    await started.wait()

    # probe_all is holding the lock. probe_single must wait for it.
    probe_single_task = asyncio.create_task(
        monitor.probe_single(InstrumentName.CLAUDE)
    )
    # Yield a few ticks; probe_single should still be waiting on the lock.
    for _ in range(3):
        await asyncio.sleep(0)
    assert not probe_single_task.done(), (
        "probe_single completed without waiting for probe_all's lock"
    )

    release.set()
    await asyncio.gather(probe_all_task, probe_single_task)
    assert monitor.last_for(InstrumentName.CLAUDE)


async def test_cache_single_seeds_cache_without_probe() -> None:
    snapshot = UsageSnapshot(
        provider=InstrumentName.CODEX,
        supported=True,
        source="session_log",
    )
    manager = _make_manager(providers={}, available={}, adapters={})
    monitor = UsageMonitor(manager)

    monitor.cache_single(InstrumentName.CODEX, [snapshot])

    assert monitor.last_for(InstrumentName.CODEX) == [snapshot]


async def test_probe_single_writes_back_to_cache() -> None:
    snapshot = UsageSnapshot(
        provider=InstrumentName.KIMI,
        supported=False,
        source="not_supported",
    )
    adapter = MagicMock()
    adapter.resolve_executable = MagicMock(return_value="kimi")
    adapter.get_usage = AsyncMock(return_value=[snapshot])

    manager = _make_manager(
        providers={InstrumentName.KIMI: _FakeProviderConfig()},
        available={InstrumentName.KIMI: True},
        adapters={InstrumentName.KIMI: adapter},
    )
    monitor = UsageMonitor(manager)

    snapshots = await monitor.probe_single(InstrumentName.KIMI)

    assert snapshots == [snapshot]
    assert monitor.last_for(InstrumentName.KIMI) == [snapshot]


async def test_start_then_stop_does_not_leak_task() -> None:
    manager = _make_manager(providers={}, available={}, adapters={})
    # Interval well past test wall clock so the loop never actually fires.
    monitor = UsageMonitor(manager, interval_seconds=3600)

    monitor.start()
    assert monitor._task is not None
    await monitor.stop()
    assert monitor._task is None


async def test_start_is_idempotent_when_already_running() -> None:
    manager = _make_manager(providers={}, available={}, adapters={})
    monitor = UsageMonitor(manager, interval_seconds=3600)

    monitor.start()
    first_task = monitor._task
    monitor.start()
    assert monitor._task is first_task
    await monitor.stop()


async def test_start_is_skipped_when_disabled() -> None:
    manager = _make_manager(providers={}, available={}, adapters={})
    monitor = UsageMonitor(manager, enabled=False)

    monitor.start()

    assert monitor._task is None


async def test_stop_is_noop_when_never_started() -> None:
    manager = _make_manager(providers={}, available={}, adapters={})
    monitor = UsageMonitor(manager)

    await monitor.stop()

    assert monitor._task is None


async def test_probe_provider_returns_empty_for_disabled_provider() -> None:
    adapter = MagicMock()
    manager = _make_manager(
        providers={InstrumentName.CLAUDE: _FakeProviderConfig(enabled=False)},
        available={InstrumentName.CLAUDE: True},
        adapters={InstrumentName.CLAUDE: adapter},
    )

    results = await probe_provider(
        adapter=adapter, provider=InstrumentName.CLAUDE, manager=manager
    )

    assert results == []


async def test_probe_provider_wraps_adapter_exception() -> None:
    adapter = MagicMock()
    adapter.resolve_executable = MagicMock(return_value="x")
    adapter.get_usage = AsyncMock(side_effect=RuntimeError("boom"))
    manager = _make_manager(
        providers={InstrumentName.CLAUDE: _FakeProviderConfig()},
        available={InstrumentName.CLAUDE: True},
        adapters={InstrumentName.CLAUDE: adapter},
    )

    results = await probe_provider(
        adapter=adapter, provider=InstrumentName.CLAUDE, manager=manager
    )

    assert len(results) == 1
    assert results[0].supported is False
    assert results[0].source == "not_supported"
    assert "boom" in (results[0].note or "")


async def test_probe_provider_timeout_returns_not_supported(monkeypatch) -> None:
    import symphony.usage.runner as runner_mod

    monkeypatch.setattr(runner_mod, "PROBE_TIMEOUT_SECONDS", 0.05)

    async def slow_probe(**_kwargs):
        await asyncio.sleep(1.0)
        return []

    adapter = MagicMock()
    adapter.resolve_executable = MagicMock(return_value="x")
    adapter.get_usage = slow_probe
    manager = _make_manager(
        providers={InstrumentName.CLAUDE: _FakeProviderConfig()},
        available={InstrumentName.CLAUDE: True},
        adapters={InstrumentName.CLAUDE: adapter},
    )

    results = await probe_provider(
        adapter=adapter, provider=InstrumentName.CLAUDE, manager=manager
    )

    assert len(results) == 1
    assert results[0].supported is False
    assert "timed out" in (results[0].note or "")


async def test_default_adapter_get_usage_returns_not_supported() -> None:
    """The base ProviderAdapter.get_usage stub returns a single not_supported
    snapshot. Cover it directly so coverage doesn't depend on integration."""
    from datetime import datetime, timezone

    from symphony.providers.base import ProviderAdapter

    adapter = ProviderAdapter()
    adapter.name = InstrumentName.CLAUDE  # type: ignore[assignment]

    results = await adapter.get_usage(
        executable="claude",
        models=["sonnet"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=datetime.now(timezone.utc),
    )

    assert len(results) == 1
    assert results[0].supported is False
    assert results[0].source == "not_supported"
