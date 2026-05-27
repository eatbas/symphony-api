"""Tests for the Antigravity usage probe (always not_supported)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from symphony.providers import antigravity as antigravity_module
from symphony.providers.antigravity import AntigravityAdapter


async def test_get_usage_returns_single_not_supported_snapshot() -> None:
    adapter = AntigravityAdapter()
    snapshots = await adapter.get_usage(
        executable="agy",
        models=["gemini-3.5-flash"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=datetime.now(timezone.utc),
    )
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.provider.value == "antigravity"
    assert snapshot.supported is False
    assert snapshot.source == "not_supported"
    assert snapshot.window is None
    assert snapshot.note is not None
    assert "Antigravity" in snapshot.note


def test_normalise_workspace_returns_empty_path_unchanged() -> None:
    """Covers the ``not path`` short-circuit in ``_normalise_workspace``."""
    assert AntigravityAdapter._normalise_workspace("") == ""


def test_normalise_workspace_converts_msys_to_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the Windows-side conversion branch (line 205)."""
    monkeypatch.setattr(antigravity_module.os, "name", "nt")
    monkeypatch.setattr(antigravity_module.os, "sep", "\\")
    assert AntigravityAdapter._normalise_workspace("/c/Users/eren") == "C:\\Users\\eren"


def test_normalise_workspace_passes_through_non_matching_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path that doesn't match the MSYS form should be returned verbatim."""
    monkeypatch.setattr(antigravity_module.os, "name", "nt")
    # Path missing the drive-letter prefix that the conversion expects.
    assert AntigravityAdapter._normalise_workspace("C:\\Users\\eren") == "C:\\Users\\eren"


def test_write_settings_resets_non_dict_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If settings.json exists but contains a JSON array, the writer must
    discard it and start from a fresh dict (covers line 225)."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("[]", encoding="utf-8")  # JSON, but not a dict
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_path)

    AntigravityAdapter._write_settings("Some Label", "C:\\workspace")

    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert isinstance(written, dict)
    assert written.get("model") == "Some Label"
    assert "C:\\workspace" in written.get("trustedWorkspaces", [])
