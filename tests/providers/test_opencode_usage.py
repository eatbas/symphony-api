"""Tests for the OpenCode usage probe.

The probe calls ``GET https://openrouter.ai/api/v1/key`` to surface the
free-tier rate-limit window.  All HTTP interactions are stubbed via
``httpx.MockTransport`` so the suite stays offline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from symphony.providers import opencode as opencode_mod
from symphony.providers.opencode import OpenCodeAdapter


def _call_usage(adapter: OpenCodeAdapter):
    return adapter.get_usage(
        executable="opencode",
        models=["openrouter/qwen/qwen3-coder:free"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=datetime.now(timezone.utc),
    )


def _install_transport(monkeypatch, handler):
    """Patch ``httpx.AsyncClient`` to use a mock transport for one call."""
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


async def test_get_usage_returns_not_supported_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    adapter = OpenCodeAdapter()
    snapshots = await _call_usage(adapter)
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.supported is False
    assert snapshot.source == "not_supported"
    assert "OPENROUTER_API_KEY" in (snapshot.note or "")


async def test_get_usage_populates_from_openrouter_key_response(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "data": {
                    "label": "My Key",
                    "limit": 100.0,
                    "limit_remaining": 60.0,
                    "usage": 40.0,
                    "is_free_tier": True,
                }
            },
        )

    _install_transport(monkeypatch, handler)
    adapter = OpenCodeAdapter()
    snapshots = await _call_usage(adapter)
    snapshot = snapshots[0]
    assert snapshot.supported is True
    assert snapshot.source == "api"
    assert snapshot.window == "rolling"
    assert snapshot.used and snapshot.used.cost_usd == pytest.approx(40.0)
    assert snapshot.limit and snapshot.limit.cost_usd == pytest.approx(100.0)
    assert snapshot.remaining and snapshot.remaining.cost_usd == pytest.approx(60.0)
    assert snapshot.percent_remaining == pytest.approx(60.0)
    assert "free tier" in (snapshot.note or "")
    assert "My Key" in (snapshot.note or "")


async def test_get_usage_handles_missing_limit_fields(monkeypatch) -> None:
    """When the API returns ``limit: null`` (unlimited keys), the
    snapshot must still be supported but with no percent_remaining."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-unlimited")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "label": None,
                    "limit": None,
                    "limit_remaining": None,
                    "usage": 5.0,
                    "is_free_tier": False,
                }
            },
        )

    _install_transport(monkeypatch, handler)
    adapter = OpenCodeAdapter()
    snapshots = await _call_usage(adapter)
    snapshot = snapshots[0]
    assert snapshot.supported is True
    assert snapshot.limit is None
    assert snapshot.remaining is None


async def test_get_usage_handles_limit_set_but_remaining_missing(monkeypatch) -> None:
    """When ``limit`` is a positive number but ``limit_remaining`` is
    null, the percent calculation must short-circuit and return None
    instead of dividing by an unknown numerator."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-partial")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "label": "Partial Key",
                    "limit": 100.0,
                    "limit_remaining": None,
                    "usage": 25.0,
                    "is_free_tier": False,
                }
            },
        )

    _install_transport(monkeypatch, handler)
    adapter = OpenCodeAdapter()
    snapshots = await _call_usage(adapter)
    snapshot = snapshots[0]
    assert snapshot.supported is True
    assert snapshot.percent_remaining is None
    assert snapshot.limit and snapshot.limit.cost_usd == pytest.approx(100.0)
    assert snapshot.remaining is None
    assert snapshot.used and snapshot.used.cost_usd == pytest.approx(25.0)


async def test_get_usage_returns_not_supported_on_http_error(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    _install_transport(monkeypatch, handler)
    adapter = OpenCodeAdapter()
    snapshots = await _call_usage(adapter)
    snapshot = snapshots[0]
    assert snapshot.supported is False
    assert snapshot.source == "not_supported"
    assert "failed" in (snapshot.note or "")


async def test_get_usage_returns_not_supported_on_non_2xx(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorised"})

    _install_transport(monkeypatch, handler)
    adapter = OpenCodeAdapter()
    snapshots = await _call_usage(adapter)
    snapshot = snapshots[0]
    assert snapshot.supported is False
    assert "401" in (snapshot.note or "")


async def test_get_usage_returns_not_supported_on_invalid_json(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    _install_transport(monkeypatch, handler)
    adapter = OpenCodeAdapter()
    snapshots = await _call_usage(adapter)
    snapshot = snapshots[0]
    assert snapshot.supported is False
    assert "JSON" in (snapshot.note or "")
