from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..models import ChatAcceptedResponse, ChatRequest, ErrorDetail, ScoreSnapshot, StopResponse
from ..models.enums import TERMINAL_STATUSES
from ..orchestra import ScoreHandle
from ._deps import get_orchestra, get_ready_orchestra

router = APIRouter()


@router.post(
    "/v1/chat",
    tags=["Chat"],
    summary="Submit a prompt to an AI instrument",
    response_model=ChatAcceptedResponse,
    status_code=202,
    responses={
        404: {"description": "No musician configured for instrument/model.", "model": ErrorDetail},
        500: {"description": "Instrument CLI crashed or returned an unrecoverable error.", "model": ErrorDetail},
    },
)
async def chat(request: Request, body: ChatRequest) -> ChatAcceptedResponse:
    orchestra = await get_ready_orchestra(request)

    if not orchestra.available_providers.get(body.provider, False):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Instrument '{body.provider.value}' is not available. "
                f"The CLI is not installed or was not found on PATH."
            ),
        )

    musician = await orchestra.acquire_musician(body.provider, body.model)
    if musician is None:
        raise HTTPException(
            status_code=404,
            detail=f"No musician configured for instrument={body.provider.value} model={body.model}",
        )

    handle = ScoreHandle(provider=body.provider, model=body.model)
    orchestra.register_score(handle)
    await musician.submit(body, handle)
    return ChatAcceptedResponse(
        score_id=handle.score_id,
        status=handle.status,
        provider=body.provider,
        model=body.model,
        created_at=handle.created_at,
        started_at=handle.started_at,
    )


@router.get(
    "/v1/chat/{score_id}",
    tags=["Chat"],
    summary="Read the current authoritative score snapshot",
    response_model=ScoreSnapshot,
    responses={404: {"description": "Score ID not found.", "model": ErrorDetail}},
)
async def get_score(request: Request, score_id: str) -> ScoreSnapshot:
    orchestra = get_orchestra(request)
    snapshot = orchestra.get_score_snapshot(score_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Score '{score_id}' not found")
    return snapshot


@router.websocket("/v1/chat/{score_id}/ws")
async def score_websocket(websocket: WebSocket, score_id: str) -> None:
    await websocket.accept()
    orchestra = websocket.app.state.orchestra
    handle = orchestra.get_score(score_id)
    if handle is None:
        snapshot = orchestra.get_score_snapshot(score_id)
        if snapshot is None:
            await websocket.send_json({"type": "error", "detail": f"Score '{score_id}' not found"})
            await websocket.close(code=1008, reason="Unknown score")
            return
        await websocket.send_json({"type": "score_snapshot", "score": snapshot.model_dump(mode="json")})
        await websocket.close()
        return

    queue = handle.subscribe()
    try:
        snapshot = handle.snapshot()
        await websocket.send_json({"type": "score_snapshot", "score": snapshot.model_dump(mode="json")})
        if snapshot.status in TERMINAL_STATUSES:
            await websocket.close()
            return
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    finally:
        handle.unsubscribe(queue)


@router.post(
    "/v1/chat/{score_id}/stop",
    tags=["Chat"],
    summary="Stop a running or queued score",
    response_model=StopResponse,
    responses={
        404: {"description": "Score ID not found.", "model": ErrorDetail},
    },
)
async def stop_score(request: Request, score_id: str) -> StopResponse:
    orchestra = get_orchestra(request)
    handle = await orchestra.stop_score(score_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"Score '{score_id}' not found")
    return StopResponse(
        score_id=score_id,
        status=handle.status,
        provider=handle.provider,
        model=handle.model,
    )
