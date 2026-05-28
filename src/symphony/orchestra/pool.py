"""Musician pool lifecycle: lazy spawning, acquisition, and scaling.

Extracted from :mod:`orchestra` to keep that file under the project's
300-line cap.  Each public function takes the orchestra instance as the
first argument and reads/writes its ``musicians`` dict, ``_pool_locks``,
and ``available_providers`` map.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..config import InstrumentConfig
from ..models import InstrumentName
from .musician import Musician

if TYPE_CHECKING:
    from .orchestra import Orchestra

logger = logging.getLogger("symphony.orchestra.pool")


def get_pool_lock(orchestra: "Orchestra", key: tuple[InstrumentName, str]) -> asyncio.Lock:
    """Return the lock guarding lazy scaling for a provider/model pool."""
    lock = orchestra._pool_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        orchestra._pool_locks[key] = lock
    return lock


def build_musician(
    orchestra: "Orchestra", provider: InstrumentName, model: str,
) -> Musician | None:
    """Construct a Musician from config without starting it.

    Returns ``None`` if the provider is not enabled or its CLI is not
    available.  Shared between eager boot in :func:`start_pools` and
    lazy pool growth in :func:`acquire_musician`.
    """
    instrument_config = orchestra.config.providers.get(provider)
    if instrument_config is None or not instrument_config.enabled:
        return None
    if not orchestra.available_providers.get(provider, False):
        return None
    adapter = orchestra.registry[provider]
    executable = adapter.resolve_executable(instrument_config.executable)
    return Musician(
        provider=provider,
        model=model,
        adapter=adapter,
        executable=executable,
        shell_path=orchestra.shell_path,
        default_options=instrument_config.default_options,
        session_models=orchestra.session_models,
        cli_timeout=instrument_config.cli_timeout,
        idle_timeout=instrument_config.idle_timeout,
    )


def _register_lazy_pool_keys(
    orchestra: "Orchestra",
    instrument: InstrumentName,
    instrument_config: InstrumentConfig,
    executable: str,
) -> None:
    """Pre-register empty pool entries for a lazy provider so that
    :func:`acquire_musician` can grow them on first request."""
    logger.info(
        "Instrument %s: CLI '%s' found -- lazy mode, %d model(s) pending first request",
        instrument.value, executable, len(instrument_config.models),
    )
    for model in instrument_config.models:
        orchestra.musicians.setdefault((instrument, model), [])


def _collect_eager_pending(
    orchestra: "Orchestra",
    instrument: InstrumentName,
    instrument_config: InstrumentConfig,
    executable: str,
) -> list[tuple[tuple[InstrumentName, str], Musician]]:
    """Build (but do not start) one Musician per configured model for an
    eager provider; return the (key, musician) pairs awaiting start."""
    logger.info(
        "Instrument %s: CLI '%s' found -- starting %d musician(s)",
        instrument.value, executable, len(instrument_config.models),
    )
    pending: list[tuple[tuple[InstrumentName, str], Musician]] = []
    for model in instrument_config.models:
        musician = build_musician(orchestra, instrument, model)
        if musician is not None:
            pending.append(((instrument, model), musician))
    return pending


async def start_pools(orchestra: "Orchestra") -> None:
    """Boot every configured provider's musician pool in parallel.

    Lazy providers (``InstrumentConfig.lazy``) get empty pool entries
    pre-registered without spawning any musician; eager providers get
    one musician per configured model started concurrently.
    """
    eager_pending: list[tuple[tuple[InstrumentName, str], Musician]] = []

    for instrument, instrument_config in orchestra.config.providers.items():
        if not instrument_config.enabled:
            orchestra.available_providers[instrument] = False
            logger.info("Instrument %s: disabled by configuration", instrument.value)
            continue

        adapter = orchestra.registry[instrument]
        executable = adapter.resolve_executable(instrument_config.executable)

        if not adapter.is_available(instrument_config.executable):
            orchestra.available_providers[instrument] = False
            logger.warning(
                "Instrument %s: CLI '%s' not found -- skipping musician creation",
                instrument.value, executable,
            )
            continue

        orchestra.available_providers[instrument] = True

        if instrument_config.lazy:
            _register_lazy_pool_keys(orchestra, instrument, instrument_config, executable)
            continue

        eager_pending.extend(
            _collect_eager_pending(orchestra, instrument, instrument_config, executable)
        )

    await asyncio.gather(*(m.start() for _, m in eager_pending))
    for key, m in eager_pending:
        orchestra.musicians.setdefault(key, []).append(m)

    orchestra._ready.set()


async def _lazy_first_spawn(
    orchestra: "Orchestra",
    pool: list[Musician],
    provider: InstrumentName,
    model: str,
) -> Musician | None:
    """Materialise the first musician for an empty lazy-pending pool.

    Routes through ``orchestra._build_musician`` (rather than calling
    :func:`build_musician` here) so tests that monkeypatch the instance
    method continue to control the spawn outcome.
    """
    new_musician = orchestra._build_musician(provider, model)
    if new_musician is None:
        return None
    await new_musician.start()
    pool.append(new_musician)
    logger.info("Lazy-spawned first musician for %s/%s", provider.value, model)
    return new_musician


async def _scale_up(
    orchestra: "Orchestra",
    pool: list[Musician],
    provider: InstrumentName,
    model: str,
    max_pool: int,
) -> Musician:
    """Add one more musician to a busy pool, or fall back to the
    least-loaded existing musician if construction now fails (e.g. the
    provider became unavailable mid-flight)."""
    new_musician = orchestra._build_musician(provider, model)
    if new_musician is None:
        return min(pool, key=lambda m: m.queue.qsize())
    await new_musician.start()
    pool.append(new_musician)
    logger.info(
        "Scaled musician pool %s/%s to %d (max %d)",
        provider.value, model, len(pool), max_pool,
    )
    return new_musician


async def acquire_musician(
    orchestra: "Orchestra", provider: InstrumentName, model: str,
) -> Musician | None:
    """Acquire the least-busy musician, scaling the pool lazily if needed.

    1. If the pool exists but is empty (lazy-mode providers like
       OpenCode), spawn the first musician from config and return it.
    2. Otherwise return an idle musician from the pool if one exists.
    3. If all musicians are busy and the pool is below *concurrency*,
       spawn a new musician and return it.
    4. Otherwise return the musician with the smallest queue.
    """
    key = (provider, model)
    async with get_pool_lock(orchestra, key):
        pool = orchestra.musicians.get(key)
        if pool is None:
            return None

        instrument_config = orchestra.config.providers.get(provider)
        max_pool = max(1, instrument_config.concurrency if instrument_config else 1)

        if not pool:
            return await _lazy_first_spawn(orchestra, pool, provider, model)

        for musician in pool:
            if musician.is_idle:
                return musician

        if len(pool) < max_pool:
            return await _scale_up(orchestra, pool, provider, model, max_pool)

        return min(pool, key=lambda m: m.queue.qsize())
