"""Coverage for the OpenRouter periodic refresh task."""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest

from symphony.models import InstrumentName
from symphony.updater import discovery_refresh as refresh_mod
from symphony.updater.discovery_refresh import refresh_openrouter_models
from tests.helpers.orchestra import started_orchestra


def _lazy_opencode(loaded_config):
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.OPENCODE]
    new_providers[InstrumentName.OPENCODE] = dataclasses.replace(base, lazy=True)
    return dataclasses.replace(loaded_config, providers=new_providers)


def _patch_discover(monkeypatch, result):
    async def fake_discover() -> list[str] | None:
        return result

    monkeypatch.setattr(refresh_mod, "discover_openrouter_free_models", fake_discover)


def _patch_invalidate(monkeypatch, counter: dict[str, int]):
    def fake_invalidate() -> None:
        counter["count"] += 1

    monkeypatch.setattr(refresh_mod, "invalidate_cache", fake_invalidate)


@pytest.mark.asyncio
async def test_refresh_returns_none_when_provider_disabled(loaded_config, monkeypatch) -> None:
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.OPENCODE]
    new_providers[InstrumentName.OPENCODE] = dataclasses.replace(base, enabled=False, lazy=True)
    config = dataclasses.replace(loaded_config, providers=new_providers)
    async with started_orchestra(config) as manager:
        assert await refresh_openrouter_models(manager) is None


@pytest.mark.asyncio
async def test_refresh_returns_none_for_non_lazy_provider(loaded_config) -> None:
    """Non-lazy OpenCode configs are out of scope for the immediate-teardown refresh."""
    async with started_orchestra(loaded_config) as manager:
        # The fixture's OpenCode is eager by default.
        assert await refresh_openrouter_models(manager) is None


@pytest.mark.asyncio
async def test_refresh_returns_none_when_discovery_returns_none(
    loaded_config, monkeypatch
) -> None:
    counter = {"count": 0}
    _patch_invalidate(monkeypatch, counter)
    _patch_discover(monkeypatch, None)
    async with started_orchestra(_lazy_opencode(loaded_config)) as manager:
        assert await refresh_openrouter_models(manager) is None
    assert counter["count"] == 1


@pytest.mark.asyncio
async def test_refresh_reports_no_changes_when_lists_match(loaded_config, monkeypatch) -> None:
    counter = {"count": 0}
    _patch_invalidate(monkeypatch, counter)
    config = _lazy_opencode(loaded_config)
    _patch_discover(
        monkeypatch,
        list(config.providers[InstrumentName.OPENCODE].models),
    )
    async with started_orchestra(config) as manager:
        result = await refresh_openrouter_models(manager)
    assert result == ([], [])


@pytest.mark.asyncio
async def test_refresh_adds_and_removes_models(loaded_config, monkeypatch) -> None:
    config = _lazy_opencode(loaded_config)
    current_models = config.providers[InstrumentName.OPENCODE].models
    new_set = [
        current_models[0],  # keep one
        "openrouter/added/m1:free",
        "openrouter/added/m2:free",
    ]
    _patch_invalidate(monkeypatch, {"count": 0})
    _patch_discover(monkeypatch, new_set)

    async with started_orchestra(config) as manager:
        result = await refresh_openrouter_models(manager)
        assert result is not None
        added, removed = result
        assert "openrouter/added/m1:free" in added
        assert "openrouter/added/m2:free" in added
        # Any model from the original list that isn't in new_set must be removed.
        original_set = set(current_models)
        expected_removed = sorted(original_set - {current_models[0]})
        assert removed == expected_removed

        # New keys exist as empty pools (lazy).
        assert manager.musicians[(InstrumentName.OPENCODE, "openrouter/added/m1:free")] == []
        # Removed keys are gone from the pool dict.
        for stale in expected_removed:
            assert (InstrumentName.OPENCODE, stale) not in manager.musicians

        # The in-memory config reflects the new list.
        assert set(manager.config.providers[InstrumentName.OPENCODE].models) == set(new_set)


@pytest.mark.asyncio
async def test_refresh_stops_warm_musicians_when_model_disappears(
    loaded_config, monkeypatch
) -> None:
    config = _lazy_opencode(loaded_config)
    current_models = list(config.providers[InstrumentName.OPENCODE].models)
    target_model = current_models[0]

    # Discovery drops the model entirely.
    _patch_invalidate(monkeypatch, {"count": 0})
    _patch_discover(monkeypatch, [m for m in current_models if m != target_model])

    async with started_orchestra(config) as manager:
        # Warm the lazy pool so a musician exists at refresh time.
        warm = await manager.acquire_musician(InstrumentName.OPENCODE, target_model)
        assert warm is not None
        assert manager.musicians[(InstrumentName.OPENCODE, target_model)] == [warm]

        result = await refresh_openrouter_models(manager)
        assert result is not None
        added, removed = result
        assert target_model in removed
        assert (InstrumentName.OPENCODE, target_model) not in manager.musicians


@pytest.mark.asyncio
async def test_refresh_serialises_with_acquire_musician(
    loaded_config, monkeypatch
) -> None:
    """A concurrent ``acquire_musician`` must not race the refresh.

    Both code paths take ``orchestra._pool_lock(key)``.  The refresh
    holds the lock while it stops the warm musician and pops the pool
    entry; an acquire that fires during that window must wait for the
    lock and then see the empty pool (returning ``None`` because the
    model is no longer configured).
    """
    config = _lazy_opencode(loaded_config)
    current_models = list(config.providers[InstrumentName.OPENCODE].models)
    target_model = current_models[0]

    _patch_invalidate(monkeypatch, {"count": 0})
    _patch_discover(monkeypatch, [m for m in current_models if m != target_model])

    async with started_orchestra(config) as manager:
        warm = await manager.acquire_musician(InstrumentName.OPENCODE, target_model)
        assert warm is not None
        observed_state: dict[str, Any] = {}

        async def slow_stop() -> None:
            """Hold the lock long enough for the racing acquire to queue."""
            await asyncio.sleep(0.05)

        monkeypatch.setattr(warm, "stop", slow_stop)

        async def racing_acquire() -> None:
            observed_state["acquired"] = await manager.acquire_musician(
                InstrumentName.OPENCODE, target_model,
            )
            observed_state["pool_after"] = manager.musicians.get(
                (InstrumentName.OPENCODE, target_model),
            )

        # Start the refresh, then immediately fire the acquire so the
        # acquire must wait on the same lock the refresh is holding.
        refresh_task = asyncio.create_task(refresh_openrouter_models(manager))
        await asyncio.sleep(0)  # let refresh acquire the lock first
        acquire_task = asyncio.create_task(racing_acquire())

        await asyncio.gather(refresh_task, acquire_task)

        # The refresh removed the model; the racing acquire must have
        # observed the post-refresh state and returned None.
        assert observed_state["acquired"] is None
        assert observed_state["pool_after"] is None
