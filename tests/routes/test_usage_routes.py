"""Branch coverage for routes/usage.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from symphony.models import InstrumentName, UsageSnapshot
from symphony.service import create_app


def _snapshot(
    provider: InstrumentName = InstrumentName.CLAUDE,
    *,
    supported: bool = False,
) -> UsageSnapshot:
    return UsageSnapshot(
        provider=provider,
        supported=supported,
        source="not_supported" if not supported else "session_log",
        note="probe not implemented" if not supported else None,
    )


def test_usage_endpoint_lazy_probe_on_cold_cache(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(
            app.state.usage_monitor,
            "probe_all",
            AsyncMock(return_value=[_snapshot(InstrumentName.CLAUDE)]),
        ):
            response = client.get("/v1/usage")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["provider"] == "claude"
        assert body[0]["source"] == "not_supported"


def test_usage_endpoint_returns_cache_when_populated(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.usage_monitor.cache_single(
            InstrumentName.CLAUDE, [_snapshot(InstrumentName.CLAUDE)]
        )
        response = client.get("/v1/usage")
        assert response.status_code == 200
        assert len(response.json()) == 1


def test_usage_endpoint_falls_through_to_not_supported_when_no_session_logs(
    config_path, tmp_path, monkeypatch
) -> None:
    """End-to-end check that the lazy probe path calls each adapter's
    ``get_usage`` and returns a uniform list of ``not_supported``
    snapshots when no provider has a session-log root on disk.

    Real provider probes walk ``~/.claude/projects``, ``~/.codex/sessions``,
    and ``$KIMI_SHARE_DIR/sessions``. We point ``HOME`` / ``USERPROFILE``
    at an empty temp dir so the probes return ``not_supported`` for every
    provider regardless of the developer's local CLI history.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path / "kimi-empty"))
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/v1/usage")
    assert response.status_code == 200
    body = response.json()
    providers = {entry["provider"] for entry in body}
    assert providers == {"antigravity", "codex", "claude", "kimi", "opencode"}
    for entry in body:
        assert entry["supported"] is False
        assert entry["source"] == "not_supported"


def test_usage_refresh_forces_probe(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(
            app.state.usage_monitor,
            "probe_all",
            AsyncMock(return_value=[_snapshot(InstrumentName.KIMI)]),
        ):
            response = client.post("/v1/usage/refresh")
        assert response.status_code == 200
        assert response.json()[0]["provider"] == "kimi"


def test_usage_single_returns_400_when_cli_missing(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestra.available_providers[InstrumentName.CLAUDE] = False
        response = client.get("/v1/usage/claude")
        assert response.status_code == 400
        assert "not available" in response.json()["detail"]


def test_usage_single_lazy_probe_when_cache_cold(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(
            app.state.usage_monitor,
            "probe_single",
            AsyncMock(return_value=[_snapshot(InstrumentName.CLAUDE)]),
        ):
            response = client.get("/v1/usage/claude")
        assert response.status_code == 200
        assert response.json()[0]["provider"] == "claude"


def test_usage_single_returns_cache_when_present(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.usage_monitor.cache_single(
            InstrumentName.CLAUDE, [_snapshot(InstrumentName.CLAUDE)]
        )
        response = client.get("/v1/usage/claude")
        assert response.status_code == 200
        assert len(response.json()) == 1


def test_usage_single_refresh_success(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(
            app.state.usage_monitor,
            "probe_single",
            AsyncMock(return_value=[_snapshot(InstrumentName.CODEX)]),
        ):
            response = client.post("/v1/usage/codex/refresh")
        assert response.status_code == 200
        assert response.json()[0]["provider"] == "codex"


def test_usage_single_refresh_returns_400_when_cli_missing(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.orchestra.available_providers[InstrumentName.CLAUDE] = False
        response = client.post("/v1/usage/claude/refresh")
        assert response.status_code == 400
