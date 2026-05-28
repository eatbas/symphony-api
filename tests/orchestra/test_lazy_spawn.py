"""Coverage for the lazy musician-pool path on the OpenCode adapter."""
from __future__ import annotations

import dataclasses

import pytest

from symphony.models import InstrumentName
from symphony.orchestra import Orchestra
from tests.helpers.orchestra import started_orchestra


def _make_opencode_lazy(loaded_config):
    """Return a copy of ``loaded_config`` with OpenCode flagged lazy."""
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.OPENCODE]
    new_providers[InstrumentName.OPENCODE] = dataclasses.replace(base, lazy=True)
    return dataclasses.replace(loaded_config, providers=new_providers)


@pytest.mark.asyncio
async def test_lazy_provider_skips_eager_musician_spawn(loaded_config) -> None:
    """``start()`` must pre-register empty pools for lazy providers."""
    config = _make_opencode_lazy(loaded_config)
    async with started_orchestra(config) as manager:
        for model in config.providers[InstrumentName.OPENCODE].models:
            key = (InstrumentName.OPENCODE, model)
            assert key in manager.musicians
            assert manager.musicians[key] == []


@pytest.mark.asyncio
async def test_lazy_provider_spawns_on_first_acquire(loaded_config) -> None:
    """The first ``acquire_musician`` call must materialise a musician."""
    config = _make_opencode_lazy(loaded_config)
    async with started_orchestra(config) as manager:
        model = config.providers[InstrumentName.OPENCODE].models[0]
        first = await manager.acquire_musician(InstrumentName.OPENCODE, model)
        assert first is not None
        # Subsequent acquires reuse the warm musician.
        again = await manager.acquire_musician(InstrumentName.OPENCODE, model)
        assert again is first


@pytest.mark.asyncio
async def test_lazy_acquire_returns_none_when_provider_unavailable(
    loaded_config, monkeypatch
) -> None:
    """If the CLI is not available, the lazy spawn must return None."""
    config = _make_opencode_lazy(loaded_config)
    async with started_orchestra(config) as manager:
        # Flip availability after start() so the pre-registered pool is
        # still empty but ``_build_musician`` sees the provider as down.
        manager.available_providers[InstrumentName.OPENCODE] = False
        model = config.providers[InstrumentName.OPENCODE].models[0]
        result = await manager.acquire_musician(InstrumentName.OPENCODE, model)
        assert result is None


def test_build_musician_returns_none_for_disabled_provider(loaded_config) -> None:
    """Disabling a provider entirely makes ``_build_musician`` return ``None``."""
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.OPENCODE]
    new_providers[InstrumentName.OPENCODE] = dataclasses.replace(base, enabled=False)
    config = dataclasses.replace(loaded_config, providers=new_providers)
    manager = Orchestra(config)
    assert manager._build_musician(InstrumentName.OPENCODE, "any-model") is None


def test_build_musician_returns_none_for_unavailable_provider(loaded_config) -> None:
    """When ``available_providers`` says False, the helper returns ``None``."""
    manager = Orchestra(loaded_config)
    # available_providers is empty until ``start()`` runs, so the lookup
    # falls back to the False default.
    assert manager._build_musician(InstrumentName.OPENCODE, "any-model") is None


@pytest.mark.asyncio
async def test_musician_info_surfaces_lazy_pending_models(loaded_config) -> None:
    """``GET /v1/musicians`` must include lazy-pending pool entries so
    the UI can render the model dropdown before any musician is warm."""
    config = _make_opencode_lazy(loaded_config)
    async with started_orchestra(config) as manager:
        info = manager.musician_info()
        opencode_entries = [
            m for m in info if m.provider == InstrumentName.OPENCODE
        ]
        configured_models = set(
            config.providers[InstrumentName.OPENCODE].models
        )
        assert {m.model for m in opencode_entries} == configured_models
        # Every lazy-pending entry must report not-ready/idle.
        for entry in opencode_entries:
            assert entry.ready is False
            assert entry.busy is False


@pytest.mark.asyncio
async def test_acquire_falls_back_when_scaling_build_returns_none(
    loaded_config, monkeypatch
) -> None:
    """If scaling tries to build but the helper now returns ``None``,
    ``acquire_musician`` must hand back the least-loaded existing
    musician instead of returning ``None``."""
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.CLAUDE]
    new_providers[InstrumentName.CLAUDE] = dataclasses.replace(base, concurrency=2)
    config = dataclasses.replace(loaded_config, providers=new_providers)

    async with started_orchestra(config) as manager:
        existing = await manager.acquire_musician(InstrumentName.CLAUDE, "opus")
        assert existing is not None

        # Mark the lone musician busy so the scaling branch is taken.
        existing.busy = True

        # Force the build helper to fail so the fallback returns the
        # existing musician.
        monkeypatch.setattr(manager, "_build_musician", lambda *_a, **_k: None)

        fallback = await manager.acquire_musician(InstrumentName.CLAUDE, "opus")
        assert fallback is existing
