from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..models import ChatRequest, ChatResponse, ErrorDetail, StopResponse
from ..colony import JobHandle
from ._deps import get_colony, get_ready_colony

router = APIRouter()

_TERMINAL_EVENTS = {"completed", "failed", "stopped"}


async def _stream_handle_events(handle: JobHandle) -> AsyncIterator[str]:
    while True:
        event = await handle.events.get()
        payload = dict(event)
        event_name = payload.pop("type")
        yield f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
        if event_name in _TERMINAL_EVENTS:
            break


@router.post(
    "/v1/chat",
    tags=["Chat"],
    summary="Send a prompt to an AI provider",
    response_model=ChatResponse,
    responses={
        404: {"description": "No drone configured for provider/model.", "model": ErrorDetail},
        500: {"description": "Provider CLI crashed or returned an unrecoverable error.", "model": ErrorDetail},
    },
)
async def chat(request: Request, body: ChatRequest) -> StreamingResponse | JSONResponse:
    colony = await get_ready_colony(request)

    if not colony.available_providers.get(body.provider, False):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Provider '{body.provider.value}' is not available. "
                f"The CLI is not installed or was not found on PATH."
            ),
        )

    drone = await colony.acquire_drone(body.provider, body.model)
    if drone is None:
        raise HTTPException(
            status_code=404,
            detail=f"No drone configured for provider={body.provider.value} model={body.model}",
        )

    handle = await drone.submit(body)
    colony.register_job(handle)

    if body.stream:
        return StreamingResponse(
            _stream_handle_events(handle),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    try:
        result = await handle.result_future
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=result.model_dump())


@router.post(
    "/v1/chat/{job_id}/stop",
    tags=["Chat"],
    summary="Stop a running or queued job",
    response_model=StopResponse,
    responses={
        404: {"description": "Job ID not found.", "model": ErrorDetail},
    },
)
async def stop_job(request: Request, job_id: str) -> StopResponse:
    colony = get_colony(request)
    handle = colony.stop_job(job_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return StopResponse(
        job_id=job_id,
        status=handle.status,
        provider=handle.provider,
        model=handle.model,
    )
