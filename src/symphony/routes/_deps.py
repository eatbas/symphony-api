from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from ..orchestra import Orchestra
    from ..updater import CLIUpdater


def get_orchestra(request: Request) -> Orchestra:
    """Retrieve the Orchestra from application state."""
    return request.app.state.orchestra


async def get_ready_orchestra(request: Request) -> Orchestra:
    """Retrieve the Orchestra, waiting for it to finish booting first.

    Use this instead of :func:`get_orchestra` in route handlers that need
    musicians or instrument availability data.  The ``/health`` endpoint should
    keep using :func:`get_orchestra` so the sidecar health check passes
    instantly.
    """
    orchestra = request.app.state.orchestra
    await orchestra._ready.wait()
    return orchestra


def get_updater(request: Request) -> CLIUpdater:
    """Retrieve the CLIUpdater from application state."""
    return request.app.state.updater
