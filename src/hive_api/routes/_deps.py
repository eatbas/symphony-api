from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from ..colony import Colony
    from ..updater import CLIUpdater


def get_colony(request: Request) -> Colony:
    """Retrieve the Colony from application state."""
    return request.app.state.colony


async def get_ready_colony(request: Request) -> Colony:
    """Retrieve the Colony, waiting for it to finish booting first.

    Use this instead of :func:`get_colony` in route handlers that need
    drones or provider availability data.  The ``/health`` endpoint should
    keep using :func:`get_colony` so the sidecar health check passes
    instantly.
    """
    colony = request.app.state.colony
    await colony._ready.wait()
    return colony


def get_updater(request: Request) -> CLIUpdater:
    """Retrieve the CLIUpdater from application state."""
    return request.app.state.updater
