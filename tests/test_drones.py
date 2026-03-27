import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from hive_api.models import ChatMode, ChatRequest, ProviderName
from hive_api.colony import Colony


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
async def test_colony_boots_all_drones(loaded_config):
    manager = Colony(loaded_config)
    await manager.start()
    try:
        drones = manager.drone_info()
        assert len(drones) == 9
        assert all(drone.ready for drone in drones)
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_drone_ready_busy_idle_lifecycle(loaded_config):
    manager = Colony(loaded_config)
    await manager.start()
    try:
        drone = manager.get_drone(ProviderName.CLAUDE, "sonnet")
        assert drone is not None
        assert drone.ready
        assert not drone.busy

        handle = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet"))
        result = await handle.result_future
        assert result.exit_code == 0
        assert not drone.busy
        assert drone.ready
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_resume_rejects_model_change_in_same_runtime(loaded_config):
    manager = Colony(loaded_config)
    await manager.start()
    try:
        drone = manager.get_drone(ProviderName.CLAUDE, "sonnet")
        assert drone is not None
        handle = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet"))
        result = await handle.result_future
        assert result.provider_session_ref is not None

        alt_drone = manager.get_drone(ProviderName.CLAUDE, "opus")
        assert alt_drone is not None
        resume_request = ChatRequest(
            provider=ProviderName.CLAUDE,
            model="opus",
            workspace_path=str(Path.cwd().resolve()),
            mode=ChatMode.RESUME,
            prompt="again",
            provider_session_ref=result.provider_session_ref,
            stream=False,
        )
        handle = await alt_drone.submit(resume_request)
        with pytest.raises(Exception):
            await handle.result_future
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_concurrent_requests_serialize_on_same_drone(loaded_config):
    """Two requests to the same drone should run one after the other."""
    manager = Colony(loaded_config)
    await manager.start()
    try:
        drone = manager.get_drone(ProviderName.CLAUDE, "sonnet")
        assert drone is not None

        h1 = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet", prompt="first"))
        h2 = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet", prompt="second"))

        r1, r2 = await asyncio.gather(h1.result_future, h2.result_future)
        assert "first" in r1.final_text
        assert "second" in r2.final_text
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_get_drone_returns_none_for_unknown(loaded_config):
    manager = Colony(loaded_config)
    await manager.start()
    try:
        assert manager.get_drone(ProviderName.CLAUDE, "nonexistent") is None
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_failed_prompt_sets_drone_error(loaded_config):
    manager = Colony(loaded_config)
    await manager.start()
    try:
        drone = manager.get_drone(ProviderName.CLAUDE, "sonnet")
        assert drone is not None
        handle = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet", prompt="fail"))
        with pytest.raises(Exception):
            await handle.result_future
        assert drone.last_error is not None
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_drone_recovers_after_failure(loaded_config):
    """After a failure, the next request should still work."""
    manager = Colony(loaded_config)
    await manager.start()
    try:
        drone = manager.get_drone(ProviderName.CODEX, "codex-5.3")
        assert drone is not None

        # First request fails
        h1 = await drone.submit(_new_request(ProviderName.CODEX, "codex-5.3", prompt="fail"))
        with pytest.raises(Exception):
            await h1.result_future

        # Second request should succeed (drone recovers)
        h2 = await drone.submit(_new_request(ProviderName.CODEX, "codex-5.3", prompt="recover"))
        r2 = await h2.result_future
        assert "recover" in r2.final_text
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_health_details_reports_drone_errors(loaded_config):
    manager = Colony(loaded_config)
    await manager.start()
    try:
        drone = manager.get_drone(ProviderName.CLAUDE, "sonnet")
        handle = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet", prompt="fail"))
        with pytest.raises(Exception):
            await handle.result_future

        details = manager.health_details()
        assert len(details) > 0
        assert "claude" in details[0].lower()
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_unavailable_provider_skips_drone_creation(loaded_config):
    """When a CLI is not found, no drones should be created for that provider."""
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
        patch("hive_api.providers.base.shutil.which", side_effect=_fake_which),
        patch("hive_api.providers.base.Path.is_file", _fake_is_file),
    ):
        manager = Colony(loaded_config)
        await manager.start()
        try:
            assert manager.get_drone(ProviderName.CLAUDE, "sonnet") is None
            assert manager.get_drone(ProviderName.CLAUDE, "opus") is None
            assert manager.available_providers[ProviderName.CLAUDE] is False

            # Other providers should still have drones
            assert manager.get_drone(ProviderName.GEMINI, "gemini-3-flash-preview") is not None
            assert manager.available_providers[ProviderName.GEMINI] is True

            # capabilities() should report available=False for claude
            caps = {c.provider: c for c in manager.capabilities()}
            assert caps[ProviderName.CLAUDE].available is False
            assert caps[ProviderName.GEMINI].available is True
        finally:
            await manager.stop()


@pytest.mark.asyncio()
async def test_capabilities_include_available_field(loaded_config):
    """All providers should have the available field in capabilities."""
    manager = Colony(loaded_config)
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
async def test_cancel_queued_job(loaded_config):
    """Cancel a queued job before it runs."""
    from hive_api.shells import JobCancelledError

    manager = Colony(loaded_config)
    await manager.start()
    try:
        drone = manager.get_drone(ProviderName.CLAUDE, "sonnet")
        assert drone is not None

        # Submit two jobs — second one will be queued while first runs
        h1 = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet", prompt="first"))
        h2 = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet", prompt="second"))
        manager.register_job(h1)
        manager.register_job(h2)

        # Cancel the second (queued) job
        result = manager.stop_job(h2.job_id)
        assert result is not None
        assert result.status.value == "stopped"

        # First should complete normally
        r1 = await h1.result_future
        assert "first" in r1.final_text

        # Second should raise JobCancelledError
        with pytest.raises(JobCancelledError):
            await h2.result_future
    finally:
        await manager.stop()


@pytest.mark.asyncio()
async def test_job_status_transitions(loaded_config):
    """Verify job status goes QUEUED -> RUNNING -> COMPLETED."""
    from hive_api.models.enums import JobStatus

    manager = Colony(loaded_config)
    await manager.start()
    try:
        drone = manager.get_drone(ProviderName.CLAUDE, "sonnet")
        assert drone is not None

        handle = await drone.submit(_new_request(ProviderName.CLAUDE, "sonnet"))
        manager.register_job(handle)
        assert handle.status == JobStatus.QUEUED

        result = await handle.result_future
        assert result.exit_code == 0
        assert handle.status == JobStatus.COMPLETED
        assert handle.job_id
    finally:
        await manager.stop()
