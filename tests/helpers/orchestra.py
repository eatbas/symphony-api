"""Shared async helpers for orchestra-driven integration tests."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from symphony.config import AppConfig
from symphony.orchestra import Orchestra


@asynccontextmanager
async def started_orchestra(config: AppConfig) -> AsyncIterator[Orchestra]:
    """Start an Orchestra for the duration of the with-block, then stop it.

    Replaces the repeated ``Orchestra(loaded_config)`` / ``try/finally
    stop`` boilerplate that appears across the existing test suite.
    """
    manager = Orchestra(config)
    await manager.start()
    try:
        yield manager
    finally:
        await manager.stop()
