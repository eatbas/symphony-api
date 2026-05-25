"""Branch coverage for orchestra/orchestra.py and provider_runtime.py."""
from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from symphony.config import InstrumentConfig
from symphony.models import ChatMode, ChatRequest, InstrumentName
from symphony.models.enums import ScoreStatus
from symphony.orchestra import Orchestra
from symphony.orchestra.provider_runtime import activate_provider
from symphony.orchestra.score import ScoreHandle
from tests.helpers.orchestra import started_orchestra


def _request(provider: InstrumentName, model: str, prompt: str = "hello") -> ChatRequest:
    return ChatRequest(
        provider=provider,
        model=model,
        workspace_path=str(Path.cwd().resolve()),
        mode=ChatMode.NEW,
        prompt=prompt,
    )


def _disable(loaded_config, provider: InstrumentName):
    new_providers = dict(loaded_config.providers)
    base = new_providers[provider]
    new_providers[provider] = dataclasses.replace(base, enabled=False)
    return dataclasses.replace(loaded_config, providers=new_providers)


# ---------------------------------------------------------------------------
# Pool management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_musician_returns_none_for_unknown_pool(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        # Unknown model → no pool entry.
        result = await manager.acquire_musician(InstrumentName.CLAUDE, "missing-model")
        assert result is None


@pytest.mark.asyncio
async def test_acquire_musician_scales_pool_up_to_concurrency(loaded_config) -> None:
    # Bump concurrency so the scaling branch fires.
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.CLAUDE]
    new_providers[InstrumentName.CLAUDE] = dataclasses.replace(base, concurrency=2)
    config = dataclasses.replace(loaded_config, providers=new_providers)

    async with started_orchestra(config) as manager:
        first = await manager.acquire_musician(InstrumentName.CLAUDE, "opus")
        assert first is not None
        # Mark busy → next acquire should scale up.
        first.busy = True
        second = await manager.acquire_musician(InstrumentName.CLAUDE, "opus")
        assert second is not None
        assert second is not first
        assert len(manager.musicians[(InstrumentName.CLAUDE, "opus")]) == 2


@pytest.mark.asyncio
async def test_acquire_musician_returns_least_loaded_when_pool_full(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        # Default concurrency = 4 from InstrumentConfig; mark every musician busy
        # and verify the least-loaded one is returned without scaling.
        musicians = manager.musicians[(InstrumentName.CLAUDE, "opus")]
        for m in musicians:
            m.busy = True
        # Force concurrency to current pool size so no scaling occurs.
        new_providers = dict(manager.config.providers)
        base = new_providers[InstrumentName.CLAUDE]
        new_providers[InstrumentName.CLAUDE] = dataclasses.replace(
            base, concurrency=len(musicians)
        )
        manager.config = dataclasses.replace(manager.config, providers=new_providers)
        result = await manager.acquire_musician(InstrumentName.CLAUDE, "opus")
        assert result is not None
        # Reset for clean teardown.
        for m in musicians:
            m.busy = False


# ---------------------------------------------------------------------------
# stop_score variants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_score_idempotent_when_terminal(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        handle = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
        handle.status = ScoreStatus.COMPLETED
        manager.register_score(handle)
        result = await manager.stop_score(handle.score_id)
        assert result is handle
        # Status unchanged.
        assert handle.status == ScoreStatus.COMPLETED


@pytest.mark.asyncio
async def test_stop_score_queued_publishes_stopped(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        handle = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
        handle.status = ScoreStatus.QUEUED
        manager.register_score(handle)
        result = await manager.stop_score(handle.score_id)
        assert result is handle
        assert handle.status == ScoreStatus.STOPPED


@pytest.mark.asyncio
async def test_stop_score_returns_none_for_unknown_id(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        assert await manager.stop_score("nothing-here") is None


# ---------------------------------------------------------------------------
# Disabled provider branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_provider_is_marked_unavailable(loaded_config) -> None:
    config = _disable(loaded_config, InstrumentName.KIMI)
    async with started_orchestra(config) as manager:
        assert manager.available_providers[InstrumentName.KIMI] is False


# ---------------------------------------------------------------------------
# restore_scores recovery branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_scores_marks_running_as_failed(loaded_config) -> None:
    from tests.helpers.score_snapshots import make_snapshot

    manager = Orchestra(loaded_config)
    manager.score_store.save(
        make_snapshot(score_id="restored-running", status=ScoreStatus.RUNNING)
    )
    manager.restore_scores()
    handle = manager.get_score("restored-running")
    assert handle is not None
    assert handle.status == ScoreStatus.FAILED
    assert handle.error is not None
    assert "restart" in handle.error.lower() or "interrupt" in handle.error.lower()


# ---------------------------------------------------------------------------
# get_bash_version paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_bash_version_returns_none_when_no_idle_musician(loaded_config) -> None:
    # No started orchestra → no musicians → returns None.
    manager = Orchestra(loaded_config)
    assert await manager.get_bash_version() is None


@pytest.mark.asyncio
async def test_get_bash_version_returns_version_when_idle(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        version = await manager.get_bash_version()
        assert version is not None
        assert "bash" in version.lower() or "gnu" in version.lower()


# ---------------------------------------------------------------------------
# activate_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_provider_returns_true_when_already_available(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        assert await activate_provider(manager, InstrumentName.CLAUDE) is True


@pytest.mark.asyncio
async def test_activate_provider_returns_false_when_disabled(loaded_config) -> None:
    config = _disable(loaded_config, InstrumentName.KIMI)
    async with started_orchestra(config) as manager:
        manager.available_providers[InstrumentName.KIMI] = False
        assert await activate_provider(manager, InstrumentName.KIMI) is False


@pytest.mark.asyncio
async def test_activate_provider_creates_musicians_when_cli_now_available(
    loaded_config,
) -> None:
    async with started_orchestra(loaded_config) as manager:
        # Pretend kimi wasn't available initially.
        manager.available_providers[InstrumentName.KIMI] = False
        # Remove existing kimi musicians (we'll let activate recreate them).
        original = manager.musicians.pop(
            (InstrumentName.KIMI, "kimi-code/kimi-for-coding"), []
        )
        for m in original:
            await m.stop()

        result = await activate_provider(manager, InstrumentName.KIMI)
        assert result is True
        assert manager.available_providers[InstrumentName.KIMI] is True
        assert (InstrumentName.KIMI, "kimi-code/kimi-for-coding") in manager.musicians


@pytest.mark.asyncio
async def test_activate_provider_returns_false_when_provider_missing_from_config(
    loaded_config,
) -> None:
    async with started_orchestra(loaded_config) as manager:
        manager.available_providers[InstrumentName.CODEX] = False
        # Remove codex from config entirely.
        new_providers = {k: v for k, v in manager.config.providers.items() if k != InstrumentName.CODEX}
        manager.config = dataclasses.replace(manager.config, providers=new_providers)
        assert await activate_provider(manager, InstrumentName.CODEX) is False


# ---------------------------------------------------------------------------
# musicians_for_provider + restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_provider_stops_and_starts_each_musician(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        await manager.restart_provider(InstrumentName.CLAUDE)
        for m in manager.musicians_for_provider(InstrumentName.CLAUDE):
            assert m.ready is True
