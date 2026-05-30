"""Tests for discovery/providers.py — per-provider model discovery."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import symphony.discovery.providers as providers_mod
from symphony.discovery.providers import (
    _discover_claude,
    _discover_codex,
    _discover_kimi,
    _discover_opencode,
    _dir_mtime,
    _grep_file,
    _npm_package_dir,
    _read_discovery_cache,
    _read_json_file,
    _write_discovery_cache,
)


@pytest.fixture()
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the discovery cache to a temp path."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(providers_mod, "_DISCOVERY_CACHE_FILE", cache_file)
    return cache_file


# ---------------------------------------------------------------------------
# Cache read/write
# ---------------------------------------------------------------------------


class TestReadDiscoveryCache:
    def test_returns_empty_dict_on_missing_file(self, isolated_cache: Path) -> None:
        assert _read_discovery_cache() == {}

    def test_returns_empty_dict_on_invalid_json(self, isolated_cache: Path) -> None:
        isolated_cache.write_text("{not json")
        assert _read_discovery_cache() == {}

    def test_returns_parsed_dict_on_valid_json(self, isolated_cache: Path) -> None:
        isolated_cache.write_text(json.dumps({"foo": "bar"}))
        assert _read_discovery_cache() == {"foo": "bar"}


class TestWriteDiscoveryCache:
    def test_creates_parent_dir_and_writes_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "nested" / "dir" / "cache.json"
        monkeypatch.setattr(providers_mod, "_DISCOVERY_CACHE_FILE", target)
        _write_discovery_cache({"claude": {"models": ["opus"]}})
        assert json.loads(target.read_text()) == {"claude": {"models": ["opus"]}}

    def test_swallows_oserror_silently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "cache.json"
        monkeypatch.setattr(providers_mod, "_DISCOVERY_CACHE_FILE", target)

        def boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        # Should not raise.
        _write_discovery_cache({"claude": {"models": []}})


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


class TestDirMtime:
    def test_returns_mtime_for_existing_dir(self, tmp_path: Path) -> None:
        assert _dir_mtime(tmp_path) > 0

    def test_returns_zero_for_missing_path(self, tmp_path: Path) -> None:
        assert _dir_mtime(tmp_path / "nope") == 0.0


class TestReadJsonFile:
    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert _read_json_file(tmp_path / "missing.json") is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        assert _read_json_file(bad) is None

    def test_returns_dict_for_valid_json(self, tmp_path: Path) -> None:
        good = tmp_path / "good.json"
        good.write_text(json.dumps({"k": "v"}))
        assert _read_json_file(good) == {"k": "v"}


class TestNpmPackageDir:
    def test_returns_none_when_cli_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: None)
        assert _npm_package_dir("claude", "@anthropic-ai/claude-code") is None

    def test_resolves_node_modules_sibling_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "claude"
        wrapper.write_text("#!/bin/sh\n")
        pkg_dir = bin_dir / "node_modules" / "@anthropic-ai" / "claude-code"
        pkg_dir.mkdir(parents=True)

        monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: str(wrapper))
        assert _npm_package_dir("claude", "@anthropic-ai/claude-code") == pkg_dir

    def test_falls_back_to_parent_dir_when_sibling_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Real path is bin/claude with no node_modules sibling, but
        # the unresolved parent has node_modules.
        real_bin = tmp_path / "real"
        real_bin.mkdir()
        real_wrapper = real_bin / "claude"
        real_wrapper.write_text("#!/bin/sh\n")
        cmd_dir = tmp_path / "cmd"
        cmd_dir.mkdir()
        cmd_wrapper = cmd_dir / "claude.cmd"
        cmd_wrapper.symlink_to(real_wrapper)
        pkg_dir = cmd_dir / "node_modules" / "@anthropic-ai" / "claude-code"
        pkg_dir.mkdir(parents=True)

        monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: str(cmd_wrapper))
        result = _npm_package_dir("claude", "@anthropic-ai/claude-code")
        assert result == pkg_dir

    def test_returns_none_when_no_package_anywhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper = tmp_path / "claude"
        wrapper.write_text("#!/bin/sh\n")
        monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: str(wrapper))
        assert _npm_package_dir("claude", "@anthropic-ai/claude-code") is None


class TestGrepFile:
    def test_returns_matches(self, tmp_path: Path) -> None:
        path = tmp_path / "data.txt"
        path.write_text("foo bar foo baz")
        assert _grep_file(path, r"foo") == ["foo", "foo"]

    def test_returns_empty_on_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "nope.txt"
        monkeypatch.setattr(
            Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("nope"))
        )
        assert _grep_file(path, r"foo") == []


# ---------------------------------------------------------------------------
# Claude discovery
# ---------------------------------------------------------------------------


class TestDiscoverClaude:
    def test_returns_none_when_package_dir_missing(
        self, monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
    ) -> None:
        monkeypatch.setattr(providers_mod, "_npm_package_dir", lambda *_: None)
        assert _discover_claude() is None

    def test_parses_aliases_from_bundle_js(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
    ) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        bundle = pkg / "bundle.js"
        bundle.write_text(
            'var mR9=["sonnet","opus","haiku","best","sonnet[1m]","opus[1m]"];',
        )
        monkeypatch.setattr(providers_mod, "_npm_package_dir", lambda *_: pkg)
        result = _discover_claude()
        assert result == ["haiku", "opus", "opus[1m]", "sonnet"]
        # Cache populated.
        cache = json.loads(isolated_cache.read_text())
        assert cache["claude"]["models"] == ["haiku", "opus", "opus[1m]", "sonnet"]

    def test_uses_cached_result_when_mtime_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
    ) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        mtime = pkg.stat().st_mtime
        isolated_cache.write_text(
            json.dumps({"claude": {"mtime": mtime, "models": ["sonnet", "opus"]}})
        )
        monkeypatch.setattr(providers_mod, "_npm_package_dir", lambda *_: pkg)
        # No bundle.js -- if cache misses, we'd return None.
        assert _discover_claude() == ["sonnet", "opus"]

    def test_returns_none_when_no_matching_pattern(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
    ) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "bundle.js").write_text("var nothing_useful_here = 1;")
        monkeypatch.setattr(providers_mod, "_npm_package_dir", lambda *_: pkg)
        assert _discover_claude() is None


# ---------------------------------------------------------------------------
# Codex discovery
# ---------------------------------------------------------------------------


class TestDiscoverCodex:
    def test_returns_none_when_cache_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        assert _discover_codex() is None

    def test_reads_listed_models_and_filters_via_filter_codex(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "models_cache.json").write_text(
            json.dumps(
                {
                    "models": [
                        {"slug": "gpt-5.3", "visibility": "list"},  # below 5.4 floor -> filtered
                        {"slug": "gpt-5.4", "visibility": "list"},
                        {"slug": "gpt-5.5", "visibility": "list"},
                        {"slug": "gpt-4-old", "visibility": "list"},  # filtered out (old)
                        {"slug": "gpt-5.6-hidden", "visibility": "hidden"},  # skipped (hidden)
                        {"slug": "", "visibility": "list"},  # empty skipped
                    ]
                }
            )
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        assert _discover_codex() == ["gpt-5.4", "gpt-5.5"]

    def test_returns_none_when_no_models_left(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "models_cache.json").write_text(json.dumps({"models": []}))
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        assert _discover_codex() is None


# ---------------------------------------------------------------------------
# Kimi discovery
# ---------------------------------------------------------------------------


class TestDiscoverKimi:
    def test_returns_none_when_config_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        assert _discover_kimi() is None

    def test_returns_none_on_oserror_reading_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kimi_dir = tmp_path / ".kimi"
        kimi_dir.mkdir()
        (kimi_dir / "config.toml").write_text("anything")
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

        def boom(*_a, **_kw):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", boom)
        assert _discover_kimi() is None

    def test_parses_model_table_headers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kimi_dir = tmp_path / ".kimi"
        kimi_dir.mkdir()
        (kimi_dir / "config.toml").write_text(
            '[models."kimi-code/kimi-for-coding"]\n'
            'enabled = true\n'
            '[models."kimi-code/kimi-fast"]\n'
            'enabled = true\n'
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        assert _discover_kimi() == [
            "kimi-code/kimi-fast",
            "kimi-code/kimi-for-coding",
        ]

    def test_returns_none_when_config_has_no_models(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kimi_dir = tmp_path / ".kimi"
        kimi_dir.mkdir()
        (kimi_dir / "config.toml").write_text("[other]\nfoo = 1\n")
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        assert _discover_kimi() is None


class TestDiscoverOpencode:
    """Coverage for ``_discover_opencode``.

    OpenCode discovery now delegates to the OpenRouter catalogue
    fetcher; the tests below verify the integration point by patching
    the async discoverer to return a fixed list (success), ``None``
    (network failure), or to raise (defensive event-loop fallback).
    """

    def test_returns_openrouter_selection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_discover() -> list[str] | None:
            return ["openrouter/qwen/qwen3-coder:free", "openrouter/openai/gpt-oss-120b:free"]

        monkeypatch.setattr(
            "symphony.discovery.openrouter.discover_openrouter_free_models",
            fake_discover,
        )
        assert _discover_opencode() == [
            "openrouter/qwen/qwen3-coder:free",
            "openrouter/openai/gpt-oss-120b:free",
        ]

    def test_returns_none_when_discoverer_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_discover() -> list[str] | None:
            return None

        monkeypatch.setattr(
            "symphony.discovery.openrouter.discover_openrouter_free_models",
            fake_discover,
        )
        assert _discover_opencode() is None

    def test_runs_inside_active_loop_via_new_loop_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ``asyncio.run`` raises (e.g. nested-loop scenarios) the
        function must fall back to a fresh event loop."""

        calls = {"asyncio_run": 0, "new_loop": 0}
        original_run = providers_mod.asyncio.run

        def fake_run(coro):
            calls["asyncio_run"] += 1
            coro.close()
            raise RuntimeError("asyncio.run() cannot be called from a running event loop")

        async def fake_discover() -> list[str] | None:
            return ["openrouter/x/y:free"]

        monkeypatch.setattr(providers_mod.asyncio, "run", fake_run)
        monkeypatch.setattr(
            "symphony.discovery.openrouter.discover_openrouter_free_models",
            fake_discover,
        )

        class _LoopProxy:
            def __init__(self):
                self._inner = original_run

            def run_until_complete(self, coro):
                calls["new_loop"] += 1
                # Drive the coroutine to completion synchronously.
                try:
                    coro.send(None)
                except StopIteration as stop:
                    return stop.value
                return None

            def close(self) -> None:
                return None

        monkeypatch.setattr(
            providers_mod.asyncio,
            "new_event_loop",
            lambda: _LoopProxy(),
        )
        assert _discover_opencode() == ["openrouter/x/y:free"]
        assert calls == {"asyncio_run": 1, "new_loop": 1}
