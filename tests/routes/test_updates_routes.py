"""Branch coverage for routes/updates.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from symphony.models import CLIVersionStatus, InstrumentName
from symphony.service import create_app


def _status(provider: InstrumentName = InstrumentName.CLAUDE) -> CLIVersionStatus:
    return CLIVersionStatus(
        provider=provider,
        executable=provider.value,
        current_version="1.0.0",
        latest_version="1.0.0",
        needs_update=False,
        last_checked="2024-01-01T00:00:00+00:00",
        next_check_at="2024-01-01T04:00:00+00:00",
        auto_update=False,
    )


def test_cli_version_check_single_returns_400_when_cli_missing(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        # Force the provider to be unavailable.
        app.state.orchestra.available_providers[InstrumentName.CLAUDE] = False
        response = client.post("/v1/cli-versions/claude/check")
        assert response.status_code == 400
        assert "not available" in response.json()["detail"]


def test_cli_version_check_single_returns_404_when_resolver_returns_none(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(
            app.state.updater, "check_single_provider", AsyncMock(return_value=None)
        ):
            response = client.post("/v1/cli-versions/claude/check")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


def test_cli_version_check_single_success(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(
            app.state.updater,
            "check_single_provider",
            AsyncMock(return_value=_status(InstrumentName.CLAUDE)),
        ):
            response = client.post("/v1/cli-versions/claude/check")
        assert response.status_code == 200
        assert response.json()["provider"] == "claude"


def test_cli_version_update_returns_400_when_cli_missing(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestra.available_providers[InstrumentName.CLAUDE] = False
        response = client.post("/v1/cli-versions/claude/update")
        assert response.status_code == 400


def test_cli_version_update_success(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(
            app.state.updater,
            "update_single_provider",
            AsyncMock(return_value=_status(InstrumentName.CLAUDE)),
        ):
            response = client.post("/v1/cli-versions/claude/update")
        assert response.status_code == 200
        assert response.json()["provider"] == "claude"


def test_cli_versions_returns_cache_when_populated(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        # Pre-populate the updater cache so the lazy probe is skipped.
        app.state.updater._cache_single(_status(InstrumentName.CLAUDE))
        response = client.get("/v1/cli-versions")
        assert response.status_code == 200
        assert len(response.json()) == 1


def test_cli_versions_filters_to_available_providers(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        # Pre-populate the cache with statuses for both an available and
        # an unavailable provider; only the available one should be returned.
        app.state.updater._cache_single(_status(InstrumentName.CLAUDE))
        app.state.orchestra.available_providers[InstrumentName.CLAUDE] = False
        response = client.get("/v1/cli-versions")
        assert response.status_code == 200
        assert response.json() == []


def test_cli_versions_check_runs_check_and_filters(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(
            app.state.updater,
            "check_and_update_all",
            AsyncMock(return_value=[_status(InstrumentName.CLAUDE)]),
        ):
            response = client.post("/v1/cli-versions/check")
        assert response.status_code == 200
        assert len(response.json()) == 1
