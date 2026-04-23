import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from symphony.models import ChatMode, ChatRequest, InstrumentName
from symphony.orchestra import Orchestra


def _new_request(provider, model, prompt="hello", workspace=None):
    resolved_workspace = workspace or str(Path.cwd().resolve())
    return ChatRequest(
        provider=provider,
        model=model,
        workspace_path=resolved_workspace,
        mode=ChatMode.NEW,
        prompt=prompt,
        stream=False,
    )


@pytest.mark.asyncio()
async def test_orchestra_boots_all_musicians(loaded_config):
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musicians = manager.musician_info()
        assert len(musicians) == 11
        assert all(musician.ready for musician in musicians)
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_musician_ready_busy_idle_lifecycle(loaded_config):
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None
        assert musician.ready
        assert not musician.busy

        handle = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus"))
        result = await handle.result_future
        assert result.exit_code == 0
        assert not musician.busy
        assert musician.ready
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_resume_rejects_model_change_in_same_runtime(loaded_config):
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None
        handle = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus"))
        result = await handle.result_future
        assert result.provider_session_ref is not None

        alt_musician = manager.get_musician(InstrumentName.CLAUDE, "haiku")
        assert alt_musician is not None
        resume_request = ChatRequest(
            provider=InstrumentName.CLAUDE,
            model="haiku",
            workspace_path=str(Path.cwd().resolve()),
            mode=ChatMode.RESUME,
            prompt="again",
            provider_session_ref=result.provider_session_ref,
            stream=False,
        )
        handle = await alt_musician.submit(resume_request)
        with pytest.raises(Exception):
            await handle.result_future
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_concurrent_requests_serialize_on_same_musician(loaded_config):
    """Two requests to the same musician should run one after the other."""
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None

        h1 = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus", prompt="first"))
        h2 = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus", prompt="second"))

        r1, r2 = await asyncio.gather(h1.result_future, h2.result_future)
        assert "first" in r1.final_text
        assert "second" in r2.final_text
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_get_musician_returns_none_for_unknown(loaded_config):
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        assert manager.get_musician(InstrumentName.CLAUDE, "nonexistent") is None
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_failed_prompt_sets_musician_error(loaded_config):
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None
        handle = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus", prompt="fail"))
        with pytest.raises(Exception):
            await handle.result_future
        assert musician.last_error is not None
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_musician_recovers_after_failure(loaded_config):
    """After a failure, the next request should still work."""
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CODEX, "gpt-5.4")
        assert musician is not None

        # First request fails
        h1 = await musician.submit(_new_request(InstrumentName.CODEX, "gpt-5.4", prompt="fail"))
        with pytest.raises(Exception):
            await h1.result_future

        # Second request should succeed (musician recovers)
        h2 = await musician.submit(_new_request(InstrumentName.CODEX, "gpt-5.4", prompt="recover"))
        r2 = await h2.result_future
        assert "recover" in r2.final_text
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_health_details_reports_musician_errors(loaded_config):
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        handle = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus", prompt="fail"))
        with pytest.raises(Exception):
            await handle.result_future

        details = manager.health_details()
        assert len(details) > 0
        assert "claude" in details[0].lower()
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_unavailable_provider_skips_musician_creation(loaded_config):
    """When a CLI is not found, no musicians should be created for that provider."""
    import shutil
    from pathlib import Path as _RealPath

    original_which = shutil.which
    _original_is_file = _RealPath.is_file

    def _fake_which(cmd: str, **kwargs) -> str | None:  # type: ignore[override]
        if "claude" in str(cmd):
            return None
        return original_which(cmd, **kwargs)

    def _fake_is_file(self: _RealPath) -> bool:
        if "claude" in str(self):
            return False
        return _original_is_file(self)

    with (
        patch("symphony.providers.base.shutil.which", side_effect=_fake_which),
        patch("symphony.providers.base.Path.is_file", _fake_is_file),
    ):
        manager = Orchestra(loaded_config)
        await manager.start()
        try:
            assert manager.get_musician(InstrumentName.CLAUDE, "opus") is None
            assert manager.get_musician(InstrumentName.CLAUDE, "opus") is None
            assert manager.available_providers[InstrumentName.CLAUDE] is False

            # Other providers should still have musicians
            assert manager.get_musician(InstrumentName.GEMINI, "gemini-3-flash-preview") is not None
            assert manager.available_providers[InstrumentName.GEMINI] is True

            # capabilities() should report available=False for claude
            caps = {c.provider: c for c in manager.capabilities()}
            assert caps[InstrumentName.CLAUDE].available is False
            assert caps[InstrumentName.GEMINI].available is True
        finally:
            await manager.stop()


@pytest.mark.asyncio()
async def test_capabilities_include_available_field(loaded_config):
    """All providers should have the available field in capabilities."""
    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        caps = manager.capabilities()
        for cap in caps:
            assert hasattr(cap, "available")
            # All test providers use absolute paths so should be available
            assert cap.available is True
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_cancel_queued_score(loaded_config):
    """Cancel a queued score before it runs."""
    from symphony.shells import ScoreCancelledError

    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None

        # Submit two scores -- second one will be queued while first runs
        h1 = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus", prompt="first"))
        h2 = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus", prompt="second"))
        manager.register_score(h1)
        manager.register_score(h2)

        # Cancel the second (queued) score
        result = await manager.stop_score(h2.score_id)
        assert result is not None
        assert result.status.value == "stopped"

        # First should complete normally
        r1 = await h1.result_future
        assert "first" in r1.final_text

        # Second should raise ScoreCancelledError
        with pytest.raises(ScoreCancelledError):
            await h2.result_future
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_cancel_running_score_terminates_cli_promptly_and_recovers(loaded_config):
    from symphony.models.enums import ScoreStatus
    from symphony.shells import ScoreCancelledError

    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CODEX, "gpt-5.4")
        assert musician is not None

        handle = await musician.submit(_new_request(InstrumentName.CODEX, "gpt-5.4", prompt="slow"))
        manager.register_score(handle)

        for _ in range(20):
            if handle.status == ScoreStatus.RUNNING:
                break
            await asyncio.sleep(0.05)

        assert handle.status == ScoreStatus.RUNNING

        stopped = await manager.stop_score(handle.score_id)
        assert stopped is not None
        assert stopped.status.value == "stopped"

        with pytest.raises(ScoreCancelledError):
            await asyncio.wait_for(handle.result_future, timeout=2.0)

        follow_up = await musician.submit(_new_request(InstrumentName.CODEX, "gpt-5.4", prompt="recover"))
        response = await asyncio.wait_for(follow_up.result_future, timeout=2.0)
        assert "recover" in response.final_text
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_score_status_transitions(loaded_config):
    """Verify score status goes QUEUED -> RUNNING -> COMPLETED."""
    from symphony.models.enums import ScoreStatus

    manager = Orchestra(loaded_config)
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CLAUDE, "opus")
        assert musician is not None

        handle = await musician.submit(_new_request(InstrumentName.CLAUDE, "opus"))
        manager.register_score(handle)
        assert handle.status == ScoreStatus.QUEUED

        result = await handle.result_future
        assert result.exit_code == 0
        assert handle.status == ScoreStatus.COMPLETED
        assert handle.score_id
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_concurrent_acquire_respects_pool_concurrency(loaded_config):
    manager = Orchestra(loaded_config)
    loaded_config.providers[InstrumentName.CODEX].concurrency = 2
    await manager.start()
    try:
        musician = manager.get_musician(InstrumentName.CODEX, "gpt-5.4")
        assert musician is not None
        musician.busy = True

        acquired = await asyncio.gather(
            *(
                manager.acquire_musician(InstrumentName.CODEX, "gpt-5.4")
                for _ in range(5)
            )
        )

        assert all(item is not None for item in acquired)
        assert len(manager.musicians[(InstrumentName.CODEX, "gpt-5.4")]) == 2
    finally:
        await manager.stop()
