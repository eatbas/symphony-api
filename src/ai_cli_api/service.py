from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import load_config
from .models import (
    ChatRequest,
    ChatResponse,
    ErrorDetail,
    HealthResponse,
    ProviderCapability,
    WorkerInfo,
)
from .worker import WorkerManager

UI_INDEX = Path(__file__).with_name("ui") / "index.html"

API_DESCRIPTION = """\
Warm-worker API wrapper for AI coding CLIs (Gemini, Codex, Claude, Kimi).

The API maintains **persistent warm worker processes** for each configured
provider/model pair, enabling low-latency prompt execution without cold-start
overhead.

## Key Concepts

- **Providers** — Supported AI CLIs: `gemini`, `codex`, `claude`, `kimi`.
- **Workers** — Long-lived bash processes, one per provider/model pair,
  ready to execute prompts immediately.
- **Sessions** — Some providers support resuming previous conversations via
  a `provider_session_ref` returned in the response.

## Response Modes

The `POST /v1/chat` endpoint supports two response modes:

| Mode | `stream` | Content-Type | Description |
|------|----------|--------------|-------------|
| **JSON** | `false` | `application/json` | Single `ChatResponse` after completion |
| **SSE** | `true` (default) | `text/event-stream` | Real-time Server-Sent Events |

## SSE Event Reference

When streaming, the following event types are emitted:

| Event | Description | Data Fields |
|-------|-------------|-------------|
| `run_started` | CLI process launched | `provider`, `model` |
| `provider_session` | Session reference assigned | `provider_session_ref` |
| `output_delta` | Incremental output chunk | `text` |
| `completed` | Finished successfully | `provider`, `model`, `provider_session_ref`, `final_text`, `exit_code`, `warnings` |
| `failed` | Exited with error | `provider`, `model`, `provider_session_ref`, `exit_code`, `warnings`, `error` |

See the **Schemas** section below for the full structure of each SSE event payload.
"""

OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": "System health and readiness checks.",
    },
    {
        "name": "Providers",
        "description": "Query registered AI CLI providers and their capabilities.",
    },
    {
        "name": "Workers",
        "description": "Inspect the runtime state of warm worker processes.",
    },
    {
        "name": "Chat",
        "description": "Submit prompts to AI providers. Supports streaming (SSE) and synchronous JSON responses.",
    },
    {
        "name": "Console",
        "description": "Built-in browser UI for testing the API interactively.",
    },
]


async def _stream_handle_events(handle) -> AsyncIterator[str]:
    while True:
        event = await handle.events.get()
        payload = dict(event)
        event_name = payload.pop("type")
        yield f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
        if event_name in {"completed", "failed"}:
            break


def create_app() -> FastAPI:
    config = load_config()
    manager = WorkerManager(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(
        title="AI CLI API",
        version="0.1.0",
        summary="Warm-worker API wrapper for AI coding CLIs",
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.config = config
    app.state.worker_manager = manager

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get(
        "/",
        response_class=HTMLResponse,
        tags=["Console"],
        summary="Web test console",
        description="Serves the built-in HTML console for interacting with workers in a browser. Returns HTML, not JSON.",
    )
    async def index() -> HTMLResponse:
        return HTMLResponse(UI_INDEX.read_text(encoding="utf-8"))

    @app.get(
        "/health",
        tags=["Health"],
        summary="System health check",
        description=(
            "Returns the overall health status of the API, including worker boot state, "
            "configuration path, and any degradation details. Status is `ok` when all "
            "workers are healthy, `degraded` when any worker reports an error."
        ),
        response_model=HealthResponse,
    )
    async def health() -> HealthResponse:
        details = manager.health_details()
        return HealthResponse(
            status="ok" if not details else "degraded",
            config_path=str(config.config_path),
            shell_path=manager.shell_path,
            workers_booted=all(worker.ready for worker in manager.workers.values()) if manager.workers else False,
            worker_count=len(manager.workers),
            details=details,
        )

    @app.get(
        "/v1/providers",
        tags=["Providers"],
        summary="List provider capabilities",
        description=(
            "Returns the capability matrix for all registered AI CLI providers, "
            "including whether each supports resume, streaming, and model override."
        ),
        response_model=list[ProviderCapability],
    )
    async def providers() -> list[ProviderCapability]:
        return manager.capabilities()

    @app.get(
        "/v1/workers",
        tags=["Workers"],
        summary="List active workers",
        description=(
            "Returns the runtime state of all warm worker processes, including "
            "readiness, busy state, queue depth, and last error."
        ),
        response_model=list[WorkerInfo],
    )
    async def workers() -> list[WorkerInfo]:
        return manager.worker_info()

    @app.post(
        "/v1/chat",
        tags=["Chat"],
        summary="Send a prompt to an AI provider",
        description="""\
Submit a prompt to a warm AI CLI worker.

### Response Modes

- **JSON** (`stream: false`): Returns a single `ChatResponse` JSON object after the CLI completes.
- **Streaming** (`stream: true`, default): Returns a `text/event-stream` (Server-Sent Events) response.

### SSE Event Types

| Event | Description | Data Fields |
|-------|-------------|-------------|
| `run_started` | CLI process launched | `provider`, `model` |
| `provider_session` | Session reference assigned | `provider_session_ref` |
| `output_delta` | Incremental output chunk | `text` |
| `completed` | CLI finished successfully | `provider`, `model`, `provider_session_ref`, `final_text`, `exit_code`, `warnings` |
| `failed` | CLI exited with error | `provider`, `model`, `provider_session_ref`, `exit_code`, `warnings`, `error` |

### Resuming Sessions

Set `mode` to `"resume"` and provide the `provider_session_ref` from a prior response.
Check `GET /v1/providers` for the `supports_resume` flag before attempting to resume.
""",
        response_model=ChatResponse,
        responses={
            200: {
                "description": "Chat completed successfully. Returns `ChatResponse` JSON when `stream: false`, or `text/event-stream` when `stream: true`.",
            },
            404: {
                "description": "No warm worker configured for the requested provider/model combination.",
                "model": ErrorDetail,
            },
            422: {
                "description": "Validation error. Common causes: relative workspace_path, missing provider_session_ref for resume mode, empty required fields.",
            },
            500: {
                "description": "The AI CLI process crashed or returned an unrecoverable error.",
                "model": ErrorDetail,
            },
        },
    )
    async def chat(request: ChatRequest):
        worker = manager.get_worker(request.provider, request.model)
        if worker is None:
            raise HTTPException(
                status_code=404,
                detail=f"No warm worker configured for provider={request.provider.value} model={request.model}",
            )

        handle = await worker.submit(request)
        if request.stream:
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

    return app
