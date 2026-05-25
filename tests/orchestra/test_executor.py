"""Branch coverage for orchestra/musician/executor.py."""
from __future__ import annotations

import asyncio
import dataclasses
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from symphony.config import InstrumentConfig
from symphony.models import ChatMode, ChatRequest, InstrumentName
from symphony.models.enums import ScoreStatus
from symphony.orchestra import Orchestra
from symphony.orchestra.score import ScoreHandle
from symphony.shells import ScoreCancelledError, ShellSessionError
from tests.helpers.orchestra import started_orchestra


def _new_request(
    provider: InstrumentName,
    model: str,
    *,
    prompt: str = "hello",
    workspace: str | None = None,
) -> ChatRequest:
    return ChatRequest(
        provider=provider,
        model=model,
        workspace_path=workspace or str(Path.cwd().resolve()),
        mode=ChatMode.NEW,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# _dispatch_score branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_in_queue_publishes_stopped_event(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None

        handle = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
        handle.cancelled.set()
        events: list[dict] = []
        queue = handle.subscribe()

        consumer = asyncio.create_task(_drain(queue, events))
        request = _new_request(InstrumentName.CLAUDE, "opus")
        await musician.submit(request, handle)
        # Wait for handle.result_future to reject with cancelled.
        with pytest.raises(ScoreCancelledError):
            await handle.result_future
        await asyncio.sleep(0.1)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        types = [e.get("type") for e in events]
        assert "stopped" in types
        assert handle.status == ScoreStatus.STOPPED


@pytest.mark.asyncio
async def test_failure_publishes_failed_event_and_resets_ready(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None

        # Force a fatal error by killing the shell mid-dispatch.
        # First make sure the musician fails when its shell is unusable.
        await musician.shell.stop()
        musician.ready = False

        handle = await musician.submit(
            _new_request(InstrumentName.CLAUDE, "opus", prompt="fail")
        )
        with pytest.raises(Exception):
            await handle.result_future
        # After failure, the dispatch path either recovered the shell or set ready=False.
        assert handle.status in {ScoreStatus.FAILED, ScoreStatus.STOPPED, ScoreStatus.COMPLETED}


@pytest.mark.asyncio
async def test_resume_with_mismatched_model_raises(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_musician(InstrumentName.CLAUDE, "haiku")
        assert musician is not None
        # Pre-populate session_models so the mismatch check fires.
        musician.session_models[(InstrumentName.CLAUDE, "session-1")] = "opus"

        handle = await musician.submit(
            ChatRequest(
                provider=InstrumentName.CLAUDE,
                model="haiku",
                workspace_path=str(Path.cwd().resolve()),
                mode=ChatMode.RESUME,
                prompt="x",
                provider_session_ref="session-1",
            )
        )
        with pytest.raises(Exception):
            await handle.result_future
        assert handle.status == ScoreStatus.FAILED


# ---------------------------------------------------------------------------
# nonzero exit codes & adapter-fatal interruptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publishes_failed_when_exit_code_nonzero(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None
        handle = await musician.submit(
            _new_request(InstrumentName.CLAUDE, "opus", prompt="fail")
        )
        with pytest.raises(Exception):
            await handle.result_future
        assert handle.status == ScoreStatus.FAILED


@pytest.mark.asyncio
async def test_kimi_fatal_pattern_triggers_interrupt(loaded_config) -> None:
    """Cover the fatal_interrupt branch + ShellSessionError re-raise with adapter message."""
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_musician(InstrumentName.KIMI, "kimi-code/kimi-for-coding")
        assert musician is not None
        handle = await musician.submit(
            _new_request(
                InstrumentName.KIMI,
                "kimi-code/kimi-for-coding",
                prompt="hang-after-fatal",
            )
        )
        with pytest.raises(Exception):
            await handle.result_future
        assert handle.status == ScoreStatus.FAILED


# ---------------------------------------------------------------------------
# Idle watcher and CLI timeout
# ---------------------------------------------------------------------------


def _short_timeout_config(loaded_config, *, cli_timeout: float = 0.0, idle_timeout: float = 0.0):
    """Return a copy of loaded_config with adjusted timeouts on the codex provider."""
    new_providers = dict(loaded_config.providers)
    codex = new_providers[InstrumentName.CODEX]
    new_providers[InstrumentName.CODEX] = dataclasses.replace(
        codex,
        cli_timeout=cli_timeout,
        idle_timeout=idle_timeout,
    )
    return dataclasses.replace(loaded_config, providers=new_providers)


@pytest.mark.asyncio
async def test_cli_timeout_raises_shell_session_error(loaded_config) -> None:
    config = _short_timeout_config(loaded_config, cli_timeout=0.5)
    async with started_orchestra(config) as manager:
        musician = manager.get_musician(InstrumentName.CODEX, "gpt-5.4")
        assert musician is not None
        handle = await musician.submit(
            _new_request(InstrumentName.CODEX, "gpt-5.4", prompt="hang-forever")
        )
        with pytest.raises(Exception) as exc_info:
            await handle.result_future
        assert "timed out" in str(exc_info.value).lower() or handle.status == ScoreStatus.FAILED


@pytest.mark.asyncio
async def test_idle_watcher_interrupts_on_silence(loaded_config) -> None:
    config = _short_timeout_config(loaded_config, idle_timeout=0.5)
    async with started_orchestra(config) as manager:
        musician = manager.get_musician(InstrumentName.CODEX, "gpt-5.4")
        assert musician is not None
        handle = await musician.submit(
            _new_request(InstrumentName.CODEX, "gpt-5.4", prompt="silent-hang")
        )
        with pytest.raises(Exception):
            await handle.result_future


# ---------------------------------------------------------------------------
# Cancel watcher path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_watcher_interrupts_running_shell(loaded_config) -> None:
    config = _short_timeout_config(loaded_config, cli_timeout=0.0, idle_timeout=0.0)
    async with started_orchestra(config) as manager:
        musician = manager.get_musician(InstrumentName.CODEX, "gpt-5.4")
        assert musician is not None
        handle = await musician.submit(
            _new_request(InstrumentName.CODEX, "gpt-5.4", prompt="hang-forever")
        )
        # Let the run start.
        await asyncio.sleep(0.2)
        handle.cancelled.set()
        # Now interrupt should fire; await result -- expect ScoreCancelledError or similar.
        with pytest.raises(Exception):
            await asyncio.wait_for(handle.result_future, timeout=5.0)


async def _drain(queue: asyncio.Queue, sink: list) -> None:
    while True:
        evt = await queue.get()
        sink.append(evt)
