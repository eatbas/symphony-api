"""Targeted coverage for ``symphony.usage.runner.probe_provider``."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from symphony.models import InstrumentName, UsageSnapshot
from symphony.usage.runner import probe_provider


class _FakeProviderConfig:
    def __init__(self) -> None:
        self.enabled = True
        self.executable = "/usr/bin/fake"
        self.models = ["sonnet"]


class _FakeConfig:
    def __init__(self, providers: dict[InstrumentName, _FakeProviderConfig]) -> None:
        self.providers = providers


async def test_probe_provider_passes_musician_lookup_to_adapter() -> None:
    """An adapter that invokes ``musician_lookup`` must see the manager's
    idle-musician callable -- this exercises the inner closure that
    delegates to :meth:`Orchestra.get_idle_musician`.
    """
    sentinel = object()

    async def get_usage(
        *,
        executable,
        models,
        musician_lookup,
        run_subprocess,
        now,
    ):
        # Exercise the closure -- this is the line under test.
        looked_up = musician_lookup(InstrumentName.CLAUDE)
        return [
            UsageSnapshot(
                provider=InstrumentName.CLAUDE,
                supported=True,
                source="session_log",
                note=f"lookup={looked_up!r}",
            )
        ]

    adapter = MagicMock()
    adapter.resolve_executable = MagicMock(return_value="claude")
    adapter.get_usage = get_usage

    manager = MagicMock()
    manager.config = _FakeConfig({InstrumentName.CLAUDE: _FakeProviderConfig()})
    manager.available_providers = {InstrumentName.CLAUDE: True}
    manager.registry = {InstrumentName.CLAUDE: adapter}
    manager.get_idle_musician = MagicMock(return_value=sentinel)

    snapshots = await probe_provider(
        adapter=adapter, provider=InstrumentName.CLAUDE, manager=manager
    )

    assert len(snapshots) == 1
    # The adapter routed the lookup through the manager, returning our sentinel.
    assert repr(sentinel) in (snapshots[0].note or "")
    manager.get_idle_musician.assert_called_once_with(InstrumentName.CLAUDE)
