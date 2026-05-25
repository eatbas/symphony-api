"""Misc branch coverage across orchestra/refresh/capabilities/score modules."""
from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from symphony.config import InstrumentConfig
from symphony.models import InstrumentName
from symphony.models.enums import ScoreStatus
from symphony.orchestra import Orchestra
from symphony.orchestra.capabilities import build_model_details
from symphony.orchestra.refresh import refresh_provider_models
from symphony.orchestra.score import ScoreHandle, _safe_error_message, now_rfc3339, stopped_event
from tests.helpers.orchestra import started_orchestra


# ---------------------------------------------------------------------------
# score._safe_error_message
# ---------------------------------------------------------------------------


class _SilentError(Exception):
    def __str__(self) -> str:  # noqa: D401 - test helper
        return ""


def test_safe_error_message_returns_repr_when_str_empty() -> None:
    exc = _SilentError()
    msg = _safe_error_message(exc)
    assert msg
    # When str(exc) is empty, we fall through to repr / class name.
    assert "Error" in msg or msg != ""


def test_safe_error_message_returns_str_when_available() -> None:
    assert _safe_error_message(RuntimeError("boom")) == "boom"


# ---------------------------------------------------------------------------
# ScoreHandle broadcast handles full subscribers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_broadcast_evicts_full_subscriber_queues() -> None:
    handle = ScoreHandle()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    # Manually attach a queue that is already full.
    handle._subscribers.add(queue)
    queue.put_nowait({"type": "filler"})
    # Broadcasting must drop the queue from the subscriber set.
    handle.broadcast({"type": "output_delta", "text": "hi"})
    assert queue not in handle._subscribers


def test_stopped_event_handles_missing_provider() -> None:
    handle = ScoreHandle()
    event = stopped_event(handle)
    assert event["type"] == "stopped"
    assert event["provider"] is None


# ---------------------------------------------------------------------------
# capabilities.build_model_details dedupes scaled pools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_model_details_dedupes_repeated_musicians(loaded_config) -> None:
    # Bump concurrency so we can have two musicians for the same model.
    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.CLAUDE]
    new_providers[InstrumentName.CLAUDE] = dataclasses.replace(base, concurrency=2)
    config = dataclasses.replace(loaded_config, providers=new_providers)

    async with started_orchestra(config) as manager:
        first = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert first is not None
        first.busy = True
        # Trigger pool scale-up.
        second = await manager.acquire_musician(InstrumentName.CLAUDE, "opus")
        assert second is not None and second is not first
        first.busy = False

        details = build_model_details(
            musicians=manager._all_musicians(), registry=manager.registry
        )
        opus_entries = [d for d in details if d.provider == InstrumentName.CLAUDE and d.model == "opus"]
        assert len(opus_entries) == 1


# ---------------------------------------------------------------------------
# orchestra.stop_score swallows interrupt failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_score_logs_when_interrupt_raises(
    loaded_config, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("WARNING", logger="symphony.orchestra")
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None

        handle = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
        handle.status = ScoreStatus.RUNNING
        manager.register_score(handle)
        musician._current_handle = handle
        musician.shell.interrupt = AsyncMock(side_effect=RuntimeError("interrupt boom"))  # type: ignore[method-assign]

        result = await manager.stop_score(handle.score_id)
        assert result is handle
        assert handle.status == ScoreStatus.STOPPED
        assert "interrupt" in caplog.text.lower()


@pytest.mark.asyncio
async def test_find_musician_for_score_returns_none_when_unmatched(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        handle = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
        # No musician's _current_handle is this handle.
        assert manager._find_musician_for_score(handle) is None


@pytest.mark.asyncio
async def test_evict_old_scores_removes_excess_terminal_scores(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        # Drop the limit so we can hit the evict branch with very few scores.
        import symphony.orchestra.orchestra as orch_mod

        original = orch_mod._MAX_COMPLETED_SCORES
        orch_mod._MAX_COMPLETED_SCORES = 2
        try:
            handles = []
            for _ in range(4):
                h = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
                h.status = ScoreStatus.COMPLETED
                manager._scores[h.score_id] = h
                handles.append(h)
            manager._evict_old_scores()
            assert len(manager._scores) == 2
        finally:
            orch_mod._MAX_COMPLETED_SCORES = original


@pytest.mark.asyncio
async def test_get_bash_version_skips_musicians_that_raise(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        # Patch the first idle musician's run_quick_command to raise; the
        # method should swallow the exception and try the next one (or return
        # None if no other idle musician succeeds).
        all_musicians = manager._all_musicians()
        for m in all_musicians:
            m.run_quick_command = AsyncMock(side_effect=RuntimeError("nope"))  # type: ignore[method-assign]
        version = await manager.get_bash_version()
        # All musicians raised → result is None.
        assert version is None


# ---------------------------------------------------------------------------
# refresh.refresh_provider_models edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_returns_false_when_provider_missing_from_fresh_config(
    loaded_config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with started_orchestra(loaded_config) as manager:
        # Patch load_config to return a config without the requested provider.
        from symphony.config import AppConfig

        def fake_load(_path):
            return dataclasses.replace(loaded_config, providers={})

        monkeypatch.setattr("symphony.orchestra.refresh.load_config", fake_load)
        assert await refresh_provider_models(manager, InstrumentName.CLAUDE) is False


@pytest.mark.asyncio
async def test_refresh_returns_false_when_current_provider_missing(
    loaded_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with started_orchestra(loaded_config) as manager:
        # Drop claude from current config.
        new_providers = {k: v for k, v in manager.config.providers.items() if k != InstrumentName.CLAUDE}
        manager.config = dataclasses.replace(manager.config, providers=new_providers)
        assert await refresh_provider_models(manager, InstrumentName.CLAUDE) is False


@pytest.mark.asyncio
async def test_refresh_skips_existing_model_keys_during_add(
    loaded_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a model is in the new set but already has a pool entry, skip recreate."""
    async with started_orchestra(loaded_config) as manager:
        # Stub load_config to return the same config (no actual model changes).
        def fake_load(_path):
            new_providers = dict(manager.config.providers)
            base = new_providers[InstrumentName.CLAUDE]
            new_providers[InstrumentName.CLAUDE] = dataclasses.replace(
                base, models=["opus", "haiku", "sonnet"]  # 'sonnet' is new
            )
            return dataclasses.replace(manager.config, providers=new_providers)

        monkeypatch.setattr("symphony.orchestra.refresh.load_config", fake_load)
        # Pre-populate the new model's key so the "key in musicians" branch fires.
        from symphony.orchestra.musician import Musician

        existing = Musician(
            provider=InstrumentName.CLAUDE,
            model="sonnet",
            adapter=manager.registry[InstrumentName.CLAUDE],
            executable="claude",
            shell_path=manager.shell_path,
            default_options={},
            session_models=manager.session_models,
        )
        manager.musicians[(InstrumentName.CLAUDE, "sonnet")] = [existing]

        changed = await refresh_provider_models(manager, InstrumentName.CLAUDE)
        assert changed is True
        # The existing musician must still be the same instance (no recreate).
        assert manager.musicians[(InstrumentName.CLAUDE, "sonnet")][0] is existing


# ---------------------------------------------------------------------------
# musician.run_quick_command without explicit timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_quick_command_without_timeout(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_idle_musician(InstrumentName.CLAUDE)
        assert musician is not None
        exit_code, output = await musician.run_quick_command(
            "echo no-timeout-path\n__symphony_exit=0"
        )
        assert exit_code == 0
        assert "no-timeout-path" in output
