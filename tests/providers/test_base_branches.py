"""Tests for providers/base.py branch coverage."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import symphony.providers.base as base_mod
from symphony.models import ChatMode, InstrumentName
from symphony.providers.base import (
    CommandSpec,
    ParseState,
    ProviderAdapter,
    _check_via_bash,
    check_cli_available,
    set_bash_path,
)


@pytest.fixture(autouse=True)
def reset_bash_path() -> None:
    """Reset module-level bash override after each test."""
    yield
    base_mod._bash_path = None  # noqa: SLF001


# ---------------------------------------------------------------------------
# check_cli_available
# ---------------------------------------------------------------------------


class TestCheckCliAvailable:
    def test_explicit_path_returns_true_when_file_exists(self, tmp_path: Path) -> None:
        exe = tmp_path / "fake.sh"
        exe.write_text("#!/bin/sh\necho 1.0\n")
        os.chmod(exe, 0o755)
        assert check_cli_available(str(exe)) is True

    def test_explicit_path_returns_false_when_missing(self, tmp_path: Path) -> None:
        assert check_cli_available(str(tmp_path / "nope.sh")) is False

    def test_bare_name_falls_back_to_shutil_which_when_no_bash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/true")
        assert check_cli_available("true") is True

    def test_bare_name_returns_false_when_not_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        assert check_cli_available("definitely-not-installed-xyz") is False

    def test_bare_name_routes_through_configured_bash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Configure a bash and a fake script printing a version number.
        bash = shutil.which("bash")
        assert bash is not None
        set_bash_path(bash)

        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        fake_exe = fake_bin / "myversion"
        fake_exe.write_text("#!/bin/sh\necho 'myversion 1.2.3'\n")
        os.chmod(fake_exe, 0o755)
        monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))

        assert check_cli_available("myversion") is True


# ---------------------------------------------------------------------------
# _check_via_bash
# ---------------------------------------------------------------------------


class TestCheckViaBash:
    def test_returns_false_when_command_not_found(self) -> None:
        bash = shutil.which("bash")
        assert bash is not None
        assert _check_via_bash(bash, "definitely-missing-cmd-xyz-12345") is False

    def test_returns_true_when_version_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bash = shutil.which("bash")
        assert bash is not None
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        exe = fake_bin / "vtool"
        exe.write_text("#!/bin/sh\necho 'vtool 2.3'\n")
        os.chmod(exe, 0o755)
        monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
        assert _check_via_bash(bash, "vtool") is True

    def test_returns_false_when_output_has_no_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bash = shutil.which("bash")
        assert bash is not None
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        exe = fake_bin / "noverison"
        exe.write_text("#!/bin/sh\necho 'no number here'\n")
        os.chmod(exe, 0o755)
        monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
        assert _check_via_bash(bash, "noverison") is False

    def test_returns_false_on_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(*_a, **_kw):
            raise subprocess.TimeoutExpired(cmd="bash", timeout=10)

        monkeypatch.setattr(base_mod.subprocess, "run", fake_run)
        assert _check_via_bash("/bin/bash", "anything") is False

    def test_returns_false_on_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*_a, **_kw):
            raise OSError("nope")

        monkeypatch.setattr(base_mod.subprocess, "run", fake_run)
        assert _check_via_bash("/bin/bash", "anything") is False


# ---------------------------------------------------------------------------
# ProviderAdapter helpers
# ---------------------------------------------------------------------------


class _DummyAdapter(ProviderAdapter):
    name = InstrumentName.CLAUDE
    default_executable = "dummy"

    def build_new_command(self, **_kw) -> CommandSpec:
        return CommandSpec(argv=["dummy", "--new"])

    def build_resume_command(self, **_kw) -> CommandSpec:
        return CommandSpec(argv=["dummy", "--resume"])

    def parse_output_line(self, line, state):
        return []


class TestProviderAdapterCommands:
    def test_build_command_resume_raises_without_session_ref(self) -> None:
        adapter = _DummyAdapter()
        with pytest.raises(ValueError, match="session_ref required"):
            adapter.build_command(
                executable="dummy",
                mode=ChatMode.RESUME,
                prompt="x",
                model="m",
                session_ref=None,
                provider_options={},
            )

    def test_build_command_new_passes_through(self) -> None:
        adapter = _DummyAdapter()
        spec = adapter.build_command(
            executable="dummy",
            mode=ChatMode.NEW,
            prompt="x",
            model="m",
            session_ref=None,
            provider_options={},
        )
        assert spec.argv == ["dummy", "--new"]


class TestExtraArgs:
    def test_returns_empty_when_none(self) -> None:
        adapter = _DummyAdapter()
        assert adapter._extra_args({"extra_args": None}) == []

    def test_returns_empty_when_missing(self) -> None:
        adapter = _DummyAdapter()
        assert adapter._extra_args({}) == []

    def test_validates_list_of_strings(self) -> None:
        adapter = _DummyAdapter()
        assert adapter._extra_args({"extra_args": ["-x", "1"]}) == ["-x", "1"]

    def test_rejects_non_list(self) -> None:
        adapter = _DummyAdapter()
        with pytest.raises(ValueError, match="extra_args"):
            adapter._extra_args({"extra_args": "string"})

    def test_rejects_non_string_items(self) -> None:
        adapter = _DummyAdapter()
        with pytest.raises(ValueError, match="extra_args"):
            adapter._extra_args({"extra_args": ["-x", 5]})


class TestApplyModelOverride:
    def test_does_nothing_for_default(self) -> None:
        adapter = _DummyAdapter()
        argv = ["cli"]
        adapter._apply_model_override(argv, "default")
        assert argv == ["cli"]

    def test_appends_flag_for_non_default(self) -> None:
        adapter = _DummyAdapter()
        argv = ["cli"]
        adapter._apply_model_override(argv, "opus")
        assert argv == ["cli", "--model", "opus"]

    def test_respects_custom_flag(self) -> None:
        adapter = _DummyAdapter()
        argv = ["cli"]
        adapter._apply_model_override(argv, "opus", flag="-m")
        assert argv == ["cli", "-m", "opus"]


class TestParseJsonOrWarn:
    def test_records_warning_on_invalid_json(self) -> None:
        adapter = _DummyAdapter()
        state = ParseState()
        result = adapter._parse_json_or_warn("not json", state)
        assert result is None
        assert state.warnings == ["not json"]

    def test_returns_object_on_valid_json(self) -> None:
        adapter = _DummyAdapter()
        state = ParseState()
        result = adapter._parse_json_or_warn('{"a":1}', state)
        assert result == {"a": 1}
        assert state.warnings == []


class TestAppendChunk:
    def test_dedupes_consecutive_identical_text(self) -> None:
        adapter = _DummyAdapter()
        state = ParseState()
        first = adapter._append_chunk(state, "hello")
        second = adapter._append_chunk(state, "hello")
        assert first == [{"type": "output_delta", "text": "hello"}]
        assert second == []

    def test_strips_and_skips_empty_chunks(self) -> None:
        adapter = _DummyAdapter()
        state = ParseState()
        assert adapter._append_chunk(state, "   \n") == []
        assert state.output_chunks == []

    def test_emits_after_different_text(self) -> None:
        adapter = _DummyAdapter()
        state = ParseState()
        adapter._append_chunk(state, "a")
        events = adapter._append_chunk(state, "b")
        assert events == [{"type": "output_delta", "text": "b"}]


class TestDetectFatalError:
    def test_sets_first_match_and_locks_in(self) -> None:
        adapter = _DummyAdapter()
        state = ParseState()
        adapter._detect_fatal_error("oh no LLM provider error here", state, ("LLM provider error",))
        assert state.error_message == "oh no LLM provider error here"
        # Subsequent matches must not overwrite.
        adapter._detect_fatal_error("Session expired", state, ("Session expired",))
        assert state.error_message == "oh no LLM provider error here"

    def test_ignores_when_no_pattern_matches(self) -> None:
        adapter = _DummyAdapter()
        state = ParseState()
        adapter._detect_fatal_error("benign output", state, ("LLM provider error",))
        assert state.error_message is None

    def test_skips_empty_patterns(self) -> None:
        adapter = _DummyAdapter()
        state = ParseState()
        adapter._detect_fatal_error("anything", state, ("",))
        assert state.error_message is None


class TestNormalizeArgv:
    def test_translates_windows_drive_letters(self) -> None:
        adapter = _DummyAdapter()
        # The detection is positional: an arg of len >= 3 with [1:3] == ':\\'.
        result = adapter._normalize_argv(["cli", "C:\\Users\\test"])
        assert result[0] == "cli"
        assert result[1].startswith("/c/")

    def test_leaves_short_args_alone(self) -> None:
        adapter = _DummyAdapter()
        assert adapter._normalize_argv(["a", "b"]) == ["a", "b"]


class TestNewSessionRef:
    def test_default_returns_none(self) -> None:
        adapter = _DummyAdapter()
        assert adapter.new_session_ref() is None

    def test_uuid_helper_returns_uuid_string(self) -> None:
        adapter = _DummyAdapter()
        value = adapter._uuid()
        assert len(value) == 36
        assert value.count("-") == 4
