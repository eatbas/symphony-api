"""Branch coverage for symphony.service create_app and lifespan."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from symphony.service import create_app
from symphony.shells import GitBashNotFoundError


def test_create_app_reraises_on_git_bash_not_found(config_path) -> None:
    with patch("symphony.service.Orchestra", side_effect=GitBashNotFoundError()):
        with pytest.raises(GitBashNotFoundError):
            create_app()


def test_boot_orchestra_reloads_config_after_discovery_changes(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force run_startup_discovery to return True so the reload branch fires.
    monkeypatch.setattr(
        "symphony.service.run_startup_discovery", lambda _path: True
    )
    app = create_app()
    with TestClient(app) as client:
        # Lifespan completes during client startup; the reload branch must
        # have fired without raising.
        response = client.get("/health")
        assert response.status_code == 200


def test_boot_orchestra_swallows_discovery_exception(
    config_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("ERROR", logger="symphony.service")

    def explode(_path):
        raise RuntimeError("discovery boom")

    monkeypatch.setattr("symphony.service.run_startup_discovery", explode)
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
    assert "discovery" in caplog.text.lower()


def test_lifespan_cancels_boot_task_on_immediate_shutdown(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make the orchestra.start() coroutine slow so the lifespan shutdown
    fires before _boot_orchestra completes -- exercises the cancel branch."""
    from symphony.orchestra import Orchestra

    original_start = Orchestra.start

    async def slow_start(self):
        await asyncio.sleep(5)
        await original_start(self)

    monkeypatch.setattr(Orchestra, "start", slow_start)
    app = create_app()
    # Open and close the lifespan immediately.
    with TestClient(app):
        pass
