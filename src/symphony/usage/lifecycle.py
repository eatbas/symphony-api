"""Lifecycle helpers for :class:`UsageMonitor` -- mirrors updater/lifecycle.py."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .monitor import UsageMonitor

logger = logging.getLogger("symphony.usage")


def start(monitor: "UsageMonitor") -> None:
    """Spawn the periodic refresh task if enabled and not already running."""
    if not monitor.enabled:
        logger.info("Usage monitor is disabled")
        return
    if monitor._task is not None and not monitor._task.done():
        return
    logger.info(
        "Starting usage monitor (interval=%ds)", monitor.interval_seconds
    )
    monitor._task = asyncio.create_task(monitor._periodic_loop())


async def stop(monitor: "UsageMonitor") -> None:
    """Cancel the periodic task and wait for it to finish."""
    task = monitor._task
    if task is None:
        return
    monitor._task = None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
