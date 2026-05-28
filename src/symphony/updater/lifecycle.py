from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..models import CLIVersionStatus

if TYPE_CHECKING:
    from .updater import CLIUpdater

logger = logging.getLogger("symphony.updater")


def _log_status(status: CLIVersionStatus) -> None:
    if status.needs_update:
        logger.info(
            "%s: %s -> %s (update %s)",
            status.provider.value,
            status.current_version,
            status.latest_version,
            status.update_skipped_reason or "applied",
        )
    else:
        logger.info(
            "%s: %s (up to date)",
            status.provider.value,
            status.current_version,
        )


async def periodic_loop(updater: "CLIUpdater") -> None:
    """Long-running task: re-runs ``check_and_update_all`` every
    ``interval_hours``. Errors are caught so a transient network blip
    can't kill the loop."""
    from .discovery_refresh import refresh_openrouter_models

    while True:
        try:
            for status in await updater.check_and_update_all():
                _log_status(status)
        except Exception:
            logger.exception("Error during periodic CLI version check")

        try:
            await refresh_openrouter_models(updater.manager)
        except Exception:
            logger.exception("Error during periodic OpenRouter refresh")

        await asyncio.sleep(updater.config.interval_hours * 3600)


def start(updater: "CLIUpdater") -> None:
    if not updater.config.enabled:
        logger.info("CLI updater is disabled")
        return
    if updater._task is not None:
        return
    logger.info(
        "Starting CLI updater (interval=%.1fh, auto_update=%s)",
        updater.config.interval_hours,
        updater.config.auto_update,
    )
    updater._task = asyncio.create_task(periodic_loop(updater))


async def stop(updater: "CLIUpdater") -> None:
    if updater._task is None:
        return
    updater._task.cancel()
    try:
        await updater._task
    except asyncio.CancelledError:
        pass
    updater._task = None
