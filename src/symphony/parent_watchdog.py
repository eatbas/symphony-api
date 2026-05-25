"""Terminate Symphony when the Maestro parent process dies.

Covers the case where Maestro crashes, is force-killed, or otherwise exits
without running its graceful ``stop_sidecar`` path. On macOS/Linux the
Python sidecar (and its bash + CLI descendants) are otherwise reparented
to init/launchd and survive indefinitely.

On Windows the same guarantee is provided by the Job Object created by
Maestro, so this watchdog is skipped there.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 2.0

_task: asyncio.Task[None] | None = None


def start_parent_watchdog() -> None:
    """Spawn the watchdog task if ``MAESTRO_PARENT_PID`` is set.

    Must be called from inside a running asyncio event loop. Safe to call
    more than once — only the first invocation creates a task.
    """
    global _task

    if sys.platform == "win32":  # pragma: no cover - Windows-only short-circuit
        # Maestro's Windows Job Object guarantees descendants die with it.
        return

    if _task is not None and not _task.done():
        return

    parent_pid_raw = os.environ.get("MAESTRO_PARENT_PID")
    if not parent_pid_raw:
        return

    try:
        parent_pid = int(parent_pid_raw)
    except ValueError:
        logger.warning(
            "Invalid MAESTRO_PARENT_PID=%r, watchdog disabled", parent_pid_raw
        )
        return

    if parent_pid <= 1:
        # PID 1 is init/launchd; watching it would never fire and the value
        # is almost certainly a configuration mistake.
        logger.warning(
            "MAESTRO_PARENT_PID=%d refers to init, watchdog disabled", parent_pid
        )
        return

    loop = asyncio.get_running_loop()
    _task = loop.create_task(_watch(parent_pid), name="maestro-parent-watchdog")
    logger.info("Parent watchdog started (pid=%d)", parent_pid)


async def stop_parent_watchdog() -> None:
    """Cancel the watchdog — call this during lifespan shutdown."""
    global _task
    if _task is None or _task.done():
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    finally:
        _task = None


async def _watch(parent_pid: int) -> None:
    while True:
        try:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return
        if not _pid_alive(parent_pid):
            logger.warning(
                "Maestro parent pid=%d no longer alive — triggering shutdown",
                parent_pid,
            )
            _trigger_shutdown()
            return


def _pid_alive(pid: int) -> bool:
    """Return ``True`` if *pid* is still alive. Uses signal-0 probing."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The PID exists but belongs to another user — still alive.
        return True


def _trigger_shutdown() -> None:
    """Send ``SIGTERM`` to ourselves so uvicorn runs lifespan shutdown.

    A bare ``sys.exit`` would skip ``orchestra.stop()`` and leave the
    bash + CLI descendants orphaned — exactly the situation we are trying
    to avoid. SIGTERM drives the same clean path the Rust parent uses.
    """
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError:  # pragma: no cover - self-SIGTERM does not realistically fail
        logger.exception("Failed to raise SIGTERM for self-shutdown")
