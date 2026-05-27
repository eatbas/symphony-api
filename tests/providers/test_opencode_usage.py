"""Tests for the OpenCode usage probe (always not_supported)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from symphony.providers.opencode import OpenCodeAdapter


async def test_get_usage_returns_single_not_supported_snapshot() -> None:
    adapter = OpenCodeAdapter()
    snapshots = await adapter.get_usage(
        executable="opencode",
        models=["glm-5"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=datetime.now(timezone.utc),
    )
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.provider.value == "opencode"
    assert snapshot.supported is False
    assert snapshot.source == "not_supported"
    assert snapshot.window is None
    assert snapshot.note is not None
    assert "OpenCode" in snapshot.note
