from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .models import HealthResponse
from .routes import (
    _parse_generate_response,
    chat_router,
    console_router,
    providers_router,
    testlab_router,
    updates_router,
)
from .shells import GitBashNotFoundError
from .updater import CLIUpdater
from .colony import Colony

logger = logging.getLogger("hive_api.service")

UI_STATIC_DIR = Path(__file__).with_name("ui") / "static"

API_DESCRIPTION = """\
Hive API — a coordinated collective of AI coding CLIs (Gemini, Codex, Claude, Kimi, Copilot, OpenCode).

The API maintains persistent drone pools for configured provider/model pairs,
enabling low-latency prompt execution without cold-start overhead.
Pools scale lazily up to the per-provider `concurrency` limit.

Running jobs can be stopped via `POST /v1/chat/{job_id}/stop`.

**Provider options** — per-request overrides via `provider_options`:

| Key | Providers | Description |
|-----|-----------|-------------|
| `extra_args` | All | Raw CLI flags appended to the command. |
| `effort` | Claude | Reasoning effort (`low`, `medium`, `high`). Omit for CLI default. |
| `max_turns` | Claude | Maximum autonomous tool-use turns. Omit for CLI default. |
"""

OPENAPI_TAGS = [
    {"name": "Health", "description": "System health and readiness checks."},
    {"name": "Providers", "description": "Query registered AI CLI providers and capabilities."},
    {"name": "Models", "description": "Discover configured models across providers."},
    {"name": "Drones", "description": "Inspect runtime state of drone processes."},
    {"name": "Chat", "description": "Submit prompts to AI providers with JSON or SSE responses."},
    {"name": "Updates", "description": "CLI version checking and auto-update management."},
    {"name": "Test Lab", "description": "Multi-model harness for NEW/RESUME verification workflows."},
    {"name": "Console", "description": "Built-in browser UI for interactive testing."},
]


def create_app() -> FastAPI:
    config = load_config()

    try:
        colony = Colony(config)
    except GitBashNotFoundError:
        logger.critical(
            "Git Bash is required on Windows but was not found. "
            "Please install Git for Windows: https://git-scm.com/download/win"
        )
        raise

    updater = CLIUpdater(manager=colony, config=config.updater)

    async def _boot_colony() -> None:
        """Boot drones and run the first version check in the background.

        This keeps the lifespan yield instant so uvicorn starts accepting
        connections immediately — the sidecar health check passes in <1s
        instead of waiting 8-12s for all bash sessions to spawn.
        """
        await colony.start()

        available = [p.value for p, ok in colony.available_providers.items() if ok]
        unavailable = [p.value for p, ok in colony.available_providers.items() if not ok]
        logger.info(
            "CLI availability: available=%s, unavailable=%s, drones=%d",
            available or "none",
            unavailable or "none",
            len(colony._all_drones()),
        )

        try:
            await asyncio.wait_for(updater.check_and_update_all(), timeout=30)
        except Exception:
            logger.warning("Initial CLI version check did not finish in time")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        boot_task = asyncio.create_task(_boot_colony())
        updater.start()
        try:
            yield
        finally:
            if not boot_task.done():
                boot_task.cancel()
                try:
                    await boot_task
                except asyncio.CancelledError:
                    pass
            await updater.stop()
            await colony.stop()

    app = FastAPI(
        title="Hive",
        version="0.1.0",
        summary="Hive — coordinated AI CLI collective",
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
    app.state.colony = colony
    app.state.updater = updater

    app.mount("/static", StaticFiles(directory=UI_STATIC_DIR), name="static")

    @app.get(
        "/health",
        tags=["Health"],
        summary="System health check",
        response_model=HealthResponse,
    )
    async def health() -> HealthResponse:
        details = colony.health_details()
        bash_version = await colony.get_bash_version()
        return HealthResponse(
            status="ok" if not details else "degraded",
            config_path=str(config.config_path),
            shell_path=colony.shell_path,
            bash_version=bash_version,
            drones_booted=all(drone.ready for drone in colony._all_drones()) if colony.drones else False,
            drone_count=len(colony._all_drones()),
            details=details,
        )

    app.include_router(console_router)
    app.include_router(providers_router)
    app.include_router(chat_router)
    app.include_router(updates_router)
    app.include_router(testlab_router)

    return app


__all__ = ["create_app", "_parse_generate_response"]
