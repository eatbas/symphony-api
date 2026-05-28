"""Periodic OpenRouter free-model refresh for the OpenCode pool.

Runs on the same ``interval_hours`` cadence as the CLI version check.
Each tick:

1. Invalidates the in-memory OpenRouter cache.
2. Re-fetches the live free-tier top-10 selection.
3. Diffs the result against the current OpenCode pool keys.
4. Creates empty pool entries for newly-listed models (lazy spawn keeps
   the bash fleet off the boot path).
5. Stops musicians for de-listed models *immediately* and removes their
   pool entries; in-flight requests on a just-removed musician will
   fail, which is acceptable for free-tier rotation per the plan.

The refresh is wired into :func:`symphony.updater.lifecycle.periodic_loop`
so it ticks alongside CLI version checks without a separate task.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING

from ..discovery.openrouter import (
    discover_openrouter_free_models,
    invalidate_cache,
)
from ..models import InstrumentName

if TYPE_CHECKING:
    from ..orchestra import Orchestra

logger = logging.getLogger("symphony.updater.discovery_refresh")


async def refresh_openrouter_models(
    orchestra: "Orchestra",
) -> tuple[list[str], list[str]] | None:
    """Rebuild the OpenCode pool from a fresh OpenRouter catalogue.

    Returns ``(added, removed)`` lists of model identifiers on success,
    or ``None`` when discovery could not run (network unreachable,
    OpenCode disabled, etc.).
    """
    instrument_config = orchestra.config.providers.get(InstrumentName.OPENCODE)
    if instrument_config is None or not instrument_config.enabled:
        return None
    if not instrument_config.lazy:
        # The immediate-teardown contract only applies to the lazy
        # OpenRouter-backed flow; refuse to touch other configurations.
        return None

    invalidate_cache()
    discovered = await discover_openrouter_free_models()
    if discovered is None:
        logger.warning(
            "OpenRouter discovery returned None; keeping current OpenCode pool intact",
        )
        return None

    current = set(instrument_config.models)
    new = set(discovered)
    if current == new:
        logger.info("OpenRouter free-model refresh: no changes")
        return [], []

    added = sorted(new - current)
    removed = sorted(current - new)

    # Update the in-memory config so a future ``acquire_musician`` call
    # routes through the new model set.
    orchestra.config.providers[InstrumentName.OPENCODE] = dataclasses.replace(
        instrument_config, models=list(discovered),
    )

    # Serialise per-key mutations against ``Orchestra.acquire_musician``
    # by acquiring the same lock it uses.  Without this, a concurrent
    # chat request could read a pool that this loop is about to tear
    # down and then operate on a half-stopped musician.
    for model in added:
        key = (InstrumentName.OPENCODE, model)
        async with orchestra._pool_lock(key):
            orchestra.musicians.setdefault(key, [])
        logger.info("OpenRouter refresh added: %s (lazy-pending)", model)

    for model in removed:
        key = (InstrumentName.OPENCODE, model)
        async with orchestra._pool_lock(key):
            pool = orchestra.musicians.pop(key, [])
            if pool:
                await asyncio.gather(
                    *(m.stop() for m in pool), return_exceptions=True,
                )
        logger.info(
            "OpenRouter refresh removed: %s (torn down %d musician(s))",
            model, len(pool),
        )

    return added, removed
