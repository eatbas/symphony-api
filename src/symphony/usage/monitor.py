"""UsageMonitor -- caches per-provider usage snapshots and refreshes them.

Mirrors :class:`symphony.updater.CLIUpdater` in shape so callers learn
one mental model: probe-on-cold-cache, periodic refresh, per-provider
on-demand refresh.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..models.enums import InstrumentName
from . import lifecycle
from .models import UsageSnapshot
from .runner import probe_provider

if TYPE_CHECKING:
    from ..orchestra import Orchestra

logger = logging.getLogger("symphony.usage")

# 15-minute refresh by default. Quota is more volatile than CLI versions
# (which use a 4-hour cadence) but still slow enough that re-probing
# every minute would burn musicians for no benefit.
DEFAULT_REFRESH_INTERVAL_SECONDS = 900


class UsageMonitor:
    """Periodically refreshes a per-provider usage cache."""

    def __init__(
        self,
        manager: "Orchestra",
        *,
        interval_seconds: int = DEFAULT_REFRESH_INTERVAL_SECONDS,
        enabled: bool = True,
    ) -> None:
        self.manager = manager
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._last_results: dict[InstrumentName, list[UsageSnapshot]] = {}
        self._task: asyncio.Task[None] | None = None
        # Serialises probe_all so a manual refresh racing the periodic
        # loop cannot double-probe the same provider concurrently.
        self._lock = asyncio.Lock()

    @property
    def last_results(self) -> list[UsageSnapshot]:
        """Defensive copy of every cached snapshot, flattened."""
        out: list[UsageSnapshot] = []
        for snapshots in self._last_results.values():
            out.extend(snapshots)
        return out

    def last_for(self, provider: InstrumentName) -> list[UsageSnapshot]:
        """Cached snapshots for a single provider; empty list when cold."""
        return list(self._last_results.get(provider, []))

    def _available_providers(self) -> list[InstrumentName]:
        return [
            p
            for p in self.manager.config.providers
            if self.manager.config.providers[p].enabled
            and self.manager.available_providers.get(p, False)
        ]

    async def probe_all(self) -> list[UsageSnapshot]:
        """Re-probe every available provider in parallel; updates the cache."""
        async with self._lock:
            providers = self._available_providers()
            if not providers:
                self._last_results = {}
                return []
            results = await asyncio.gather(
                *(self._probe_one(p) for p in providers)
            )
            new_cache: dict[InstrumentName, list[UsageSnapshot]] = {}
            for provider, snapshots in zip(providers, results):
                new_cache[provider] = snapshots
            self._last_results = new_cache
            return self.last_results

    async def probe_single(self, provider: InstrumentName) -> list[UsageSnapshot]:
        """Re-probe a single provider; writes back into the cache.

        Holds the same lock as :meth:`probe_all` so a manual per-provider
        refresh cannot race the periodic full-probe (the wholesale
        ``_last_results`` replacement would otherwise silently drop the
        single-provider write).
        """
        async with self._lock:
            snapshots = await self._probe_one(provider)
            self._last_results[provider] = snapshots
            return snapshots

    def cache_single(
        self, provider: InstrumentName, snapshots: list[UsageSnapshot]
    ) -> None:
        """Replace the cached snapshots for one provider.

        Mirrors :meth:`symphony.updater.CLIUpdater._cache_single` so test
        helpers can seed the cache without poking at private dict state.
        """
        self._last_results[provider] = list(snapshots)

    async def _probe_one(self, provider: InstrumentName) -> list[UsageSnapshot]:
        adapter = self.manager.registry.get(provider)
        if adapter is None:  # pragma: no cover - registry always has the provider
            return []
        return await probe_provider(
            adapter=adapter, provider=provider, manager=self.manager
        )

    def start(self) -> None:
        """Spawn the periodic refresh task when enabled."""
        lifecycle.start(self)

    async def stop(self) -> None:
        """Cancel and await the periodic refresh task."""
        await lifecycle.stop(self)

    async def _periodic_loop(self) -> None:
        """Long-running task: probe, then sleep ``interval_seconds``.

        Errors are caught so a transient network blip or filesystem hiccup
        cannot kill the loop.
        """
        while True:
            try:
                await self.probe_all()
            except Exception:  # pragma: no cover - defensive
                logger.exception("Periodic usage probe failed")
            await asyncio.sleep(self.interval_seconds)
