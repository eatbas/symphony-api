"""Tests for updater/registry.py — install detection + semver comparison."""
from __future__ import annotations

import os
import shutil

import pytest

import symphony.updater.registry as registry_mod
from symphony.updater.registry import (
    PACKAGE_REGISTRY,
    detect_install_method,
    needs_update,
)


# ---------------------------------------------------------------------------
# Install method detection
# ---------------------------------------------------------------------------


class TestDetectInstallMethod:
    def test_returns_unknown_when_which_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        assert detect_install_method("missing-cli") == "unknown"

    def test_returns_npm_when_path_traverses_node_modules(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        npm_path = "/home/user/.nvm/versions/node/v20.0.0/lib/node_modules/@openai/codex/bin/codex"
        monkeypatch.setattr(shutil, "which", lambda _name: npm_path)
        monkeypatch.setattr(registry_mod.os.path, "realpath", lambda p: p)
        assert detect_install_method("codex") == "npm"

    def test_returns_native_when_path_lives_under_local_share_versions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        native_path = f"/home/user{os.sep}.local{os.sep}share{os.sep}claude{os.sep}versions{os.sep}1.0.0{os.sep}claude"
        monkeypatch.setattr(shutil, "which", lambda _name: native_path)
        monkeypatch.setattr(registry_mod.os.path, "realpath", lambda p: p)
        assert detect_install_method("claude") == "native"

    def test_returns_unknown_for_other_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/tool")
        monkeypatch.setattr(registry_mod.os.path, "realpath", lambda p: p)
        assert detect_install_method("tool") == "unknown"


# ---------------------------------------------------------------------------
# needs_update
# ---------------------------------------------------------------------------


class TestNeedsUpdate:
    def test_false_when_current_is_none(self) -> None:
        assert needs_update(None, "1.0.0") is False

    def test_false_when_latest_is_none(self) -> None:
        assert needs_update("1.0.0", None) is False

    def test_false_when_both_none(self) -> None:
        assert needs_update(None, None) is False

    def test_true_when_current_is_older(self) -> None:
        assert needs_update("1.0.0", "1.0.1") is True

    def test_false_when_versions_equal(self) -> None:
        assert needs_update("1.0.0", "1.0.0") is False

    def test_false_when_current_is_newer(self) -> None:
        assert needs_update("2.0.0", "1.99.99") is False

    def test_falls_back_to_string_compare_on_bad_semver(self) -> None:
        # Non-numeric parts → _version_tuple raises ValueError → string compare.
        assert needs_update("1.0.beta", "1.0.beta") is False
        assert needs_update("1.0.beta", "1.0.rc") is True


# ---------------------------------------------------------------------------
# Static registry
# ---------------------------------------------------------------------------


class TestPackageRegistry:
    def test_contains_all_known_providers(self) -> None:
        for key in ("claude", "codex", "agy", "kimi"):
            assert key in PACKAGE_REGISTRY

    def test_claude_uses_native_update(self) -> None:
        info = PACKAGE_REGISTRY["claude"]
        assert info.manager == "native"
        assert info.update_cmd == "claude update"

    def test_codex_uses_npm_manager(self) -> None:
        assert PACKAGE_REGISTRY["codex"].manager == "npm"

    def test_kimi_uses_uv_manager(self) -> None:
        assert PACKAGE_REGISTRY["kimi"].manager == "uv"
