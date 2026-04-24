from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import CLIVersionStatus, ErrorDetail, InstrumentName
from ._deps import get_orchestra, get_updater

router = APIRouter(tags=["Updates"])


def _require_available(request: Request, provider: InstrumentName) -> None:
    """Raise 400 if the instrument's CLI is not installed."""
    orchestra = get_orchestra(request)
    if not orchestra.available_providers.get(provider, False):
        raise HTTPException(
            status_code=400,
            detail=f"Instrument '{provider.value}' is not available. CLI not installed.",
        )


@router.get("/v1/cli-versions", summary="List CLI version statuses", response_model=list[CLIVersionStatus])
async def cli_versions(request: Request) -> list[CLIVersionStatus]:
    """Return cached CLI version statuses.

    If the periodic updater has not completed its first pass yet,
    trigger a synchronous check on demand so callers never observe the
    transient empty-cache window during Symphony start-up.
    """
    orchestra = get_orchestra(request)
    updater = get_updater(request)
    available = {p for p, ok in orchestra.available_providers.items() if ok}

    results = updater.last_results
    if not results and available:
        results = await updater.check_and_update_all()

    return [r for r in results if r.provider in available]


@router.post(
    "/v1/cli-versions/check",
    summary="Trigger an immediate version check",
    response_model=list[CLIVersionStatus],
)
async def cli_versions_check(request: Request) -> list[CLIVersionStatus]:
    orchestra = get_orchestra(request)
    available = {p for p, ok in orchestra.available_providers.items() if ok}
    results = await get_updater(request).check_and_update_all()
    return [r for r in results if r.provider in available]


@router.post(
    "/v1/cli-versions/{provider}/check",
    summary="Check a single CLI instrument for updates",
    response_model=CLIVersionStatus,
    responses={
        400: {"description": "Instrument CLI not installed.", "model": ErrorDetail},
        404: {"description": "Unknown instrument name.", "model": ErrorDetail},
    },
)
async def cli_version_check_single(request: Request, provider: InstrumentName) -> CLIVersionStatus:
    _require_available(request, provider)
    result = await get_updater(request).check_single_provider(provider)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Instrument '{provider.value}' not found or not enabled")
    return result


@router.post(
    "/v1/cli-versions/{provider}/update",
    summary="Force-update a single CLI instrument",
    response_model=CLIVersionStatus,
    responses={
        400: {"description": "Instrument CLI not installed.", "model": ErrorDetail},
        404: {"description": "Unknown instrument name.", "model": ErrorDetail},
    },
)
async def cli_version_update(request: Request, provider: InstrumentName) -> CLIVersionStatus:
    _require_available(request, provider)
    return await get_updater(request).update_single_provider(provider)
