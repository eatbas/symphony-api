"""Usage / quota REST endpoints -- per-instrument consumption snapshots.

The surface intentionally mirrors :mod:`symphony.routes.updates`:

* ``GET  /v1/usage`` - cache-first; lazy probe on cold cache.
* ``POST /v1/usage/refresh`` - forces a full re-probe.
* ``GET  /v1/usage/{provider}`` - per-provider snapshots (lazy on cold).
* ``POST /v1/usage/{provider}/refresh`` - forces a per-provider re-probe.

Providers without a quota surface still appear in the response with
``supported=false`` and ``source="not_supported"`` so consumers see a
uniform shape across instruments.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import ErrorDetail, InstrumentName, UsageSnapshot
from ._deps import get_orchestra, get_usage_monitor

router = APIRouter(tags=["Usage"])


def _require_available(request: Request, provider: InstrumentName) -> None:
    """Raise 400 if the instrument's CLI is not installed.

    Mirrors :func:`symphony.routes.updates._require_available` exactly --
    only used for "CLI not installed", not for "no usage data published".
    Unsupported-but-installed providers still return one ``not_supported``
    snapshot rather than an HTTP error.
    """
    orchestra = get_orchestra(request)
    if not orchestra.available_providers.get(provider, False):
        raise HTTPException(
            status_code=400,
            detail=f"Instrument '{provider.value}' is not available. CLI not installed.",
        )


@router.get(
    "/v1/usage",
    summary="List usage snapshots across instruments",
    description=(
        "Returns one or more `UsageSnapshot` entries per available instrument. "
        "Providers without a quota surface still appear with `supported=false` "
        "and `source=\"not_supported\"` so the response is uniform across "
        "instruments. Served from a 15-minute background cache; the first "
        "request after start-up runs a lazy probe."
    ),
    response_model=list[UsageSnapshot],
)
async def usage(request: Request) -> list[UsageSnapshot]:
    monitor = get_usage_monitor(request)
    if not monitor.last_results:
        return await monitor.probe_all()
    return monitor.last_results


@router.post(
    "/v1/usage/refresh",
    summary="Force a refresh of every instrument's usage snapshot",
    response_model=list[UsageSnapshot],
)
async def usage_refresh(request: Request) -> list[UsageSnapshot]:
    return await get_usage_monitor(request).probe_all()


@router.get(
    "/v1/usage/{provider}",
    summary="Usage snapshots for a single instrument",
    description=(
        "Returns the cached snapshots for one instrument, or runs a lazy "
        "probe when the cache is cold. Returns a `not_supported` snapshot "
        "rather than HTTP 501 when the instrument has no quota surface."
    ),
    response_model=list[UsageSnapshot],
    responses={
        400: {"description": "Instrument CLI not installed.", "model": ErrorDetail},
    },
)
async def usage_single(
    request: Request, provider: InstrumentName
) -> list[UsageSnapshot]:
    _require_available(request, provider)
    monitor = get_usage_monitor(request)
    cached = monitor.last_for(provider)
    if cached:
        return cached
    return await monitor.probe_single(provider)


@router.post(
    "/v1/usage/{provider}/refresh",
    summary="Force a refresh of a single instrument's usage snapshots",
    response_model=list[UsageSnapshot],
    responses={
        400: {"description": "Instrument CLI not installed.", "model": ErrorDetail},
    },
)
async def usage_single_refresh(
    request: Request, provider: InstrumentName
) -> list[UsageSnapshot]:
    _require_available(request, provider)
    return await get_usage_monitor(request).probe_single(provider)
