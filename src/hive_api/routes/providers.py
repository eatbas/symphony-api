from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..models import ModelDetail, ProviderCapability, DroneInfo
from ._deps import get_ready_colony

router = APIRouter()


@router.get(
    "/v1/providers",
    tags=["Providers"],
    summary="List provider capabilities",
    description="Returns only providers whose CLI is installed and available. Pass `?all=true` to include unavailable ones.",
    response_model=list[ProviderCapability],
)
async def providers(
    request: Request,
    all: bool = Query(False, description="Include unavailable providers"),
) -> list[ProviderCapability]:
    colony = await get_ready_colony(request)
    caps = colony.capabilities()
    if all:
        return caps
    return [c for c in caps if c.available]


@router.get(
    "/v1/models",
    tags=["Models"],
    summary="List all supported models with chat examples",
    response_model=list[ModelDetail],
)
async def models(request: Request) -> list[ModelDetail]:
    colony = await get_ready_colony(request)
    return colony.model_details()


@router.get(
    "/v1/drones",
    tags=["Drones"],
    summary="List active drones",
    response_model=list[DroneInfo],
)
async def drones(request: Request) -> list[DroneInfo]:
    colony = await get_ready_colony(request)
    return colony.drone_info()
