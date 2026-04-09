from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .discovery import run_startup_discovery
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
from .orchestra import Orchestra

logger = logging.getLogger("symphony.service")

UI_STATIC_DIR = Path(__file__).with_name("ui") / "static"

API_DESCRIPTION = """\
Symphony API — a coordinated collective of AI coding CLIs (Gemini, Codex, Claude, Kimi, Copilot, OpenCode).

The API maintains persistent musician pools for configured instrument/model pairs,
enabling low-latency prompt execution without cold-start overhead.
Pools scale lazily up to the per-instrument `concurrency` limit.

Scores are submitted via `POST /v1/chat`, polled via `GET /v1/chat/{score_id}`,
and observed live via `GET /v1/chat/{score_id}/ws`.
Running scores can be stopped via `POST /v1/chat/{score_id}/stop`.

**Instrument options** — per-request overrides via `provider_options`:

| Key | Instruments | Description |
|-----|-----------|-------------|
| `extra_args` | All | Raw CLI flags appended to the command. |
| `effort` | Claude | Reasoning effort (`low`, `medium`, `high`). Omit for CLI default. |
| `max_turns` | Claude | Maximum autonomous tool-use turns. Omit for CLI default. |
"""

OPENAPI_TAGS = [
    {"name": "Health", "description": "System health and readiness checks."},
    {"name": "Providers", "description": "Query registered AI CLI instruments and capabilities."},
    {"name": "Models", "description": "Discover configured models across instruments."},
    {"name": "Musicians", "description": "Inspect runtime state of musician processes."},
    {"name": "Chat", "description": "Submit prompts to AI instruments and track durable score snapshots."},
    {"name": "Updates", "description": "CLI version checking and auto-update management."},
    {"name": "Test Lab", "description": "Multi-model harness for NEW/RESUME verification workflows."},
    {"name": "Console", "description": "Built-in browser UI for interactive testing."},
]


def create_app() -> FastAPI:
    config = load_config()

    # Discover models from installed CLIs and update config.toml before
    # the Orchestra reads it.  If any provider's model list changed the
    # config is reloaded so the Orchestra boots with fresh data.
    if run_startup_discovery(config.config_path):
        config = load_config(config.config_path)

    try:
        orchestra = Orchestra(config)
    except GitBashNotFoundError:
        logger.critical(
            "Git Bash is required on Windows but was not found. "
            "Please install Git for Windows: https://git-scm.com/download/win"
        )
        raise

    updater = CLIUpdater(manager=orchestra, config=config.updater)
    orchestra.restore_scores()

    async def _boot_orchestra() -> None:
        """Boot musicians in the background, then start the updater.

        This keeps the lifespan yield instant so uvicorn starts accepting
        connections immediately — the sidecar health check passes in <1s
        instead of waiting 8-12s for all bash sessions to spawn.

        The updater is started *after* the orchestra boots so its periodic
        loop never races with musician shell initialisation.
        """
        await orchestra.start()

        available = [p.value for p, ok in orchestra.available_providers.items() if ok]
        unavailable = [p.value for p, ok in orchestra.available_providers.items() if not ok]
        logger.info(
            "CLI availability: available=%s, unavailable=%s, musicians=%d",
            available or "none",
            unavailable or "none",
            len(orchestra._all_musicians()),
        )

        # Start the updater now that musicians are ready — its periodic
        # loop runs the first version check immediately.
        updater.start()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        boot_task = asyncio.create_task(_boot_orchestra())
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
            await orchestra.stop()

    app = FastAPI(
        title="Symphony",
        version="0.1.0",
        summary="Symphony — coordinated AI CLI collective",
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
    app.state.orchestra = orchestra
    app.state.updater = updater

    app.mount("/static", StaticFiles(directory=UI_STATIC_DIR), name="static")

    @app.get(
        "/health",
        tags=["Health"],
        summary="System health check",
        response_model=HealthResponse,
    )
    async def health() -> HealthResponse:
        details = orchestra.health_details()
        bash_version = await orchestra.get_bash_version()
        return HealthResponse(
            status="ok" if not details else "degraded",
            config_path=str(config.config_path),
            shell_path=orchestra.shell_path,
            bash_version=bash_version,
            musicians_booted=all(m.ready for m in orchestra._all_musicians()) if orchestra.musicians else False,
            musician_count=len(orchestra._all_musicians()),
            details=details,
        )

    app.include_router(console_router)
    app.include_router(providers_router)
    app.include_router(chat_router)
    app.include_router(updates_router)
    app.include_router(testlab_router)

    return app


__all__ = ["create_app", "_parse_generate_response"]
