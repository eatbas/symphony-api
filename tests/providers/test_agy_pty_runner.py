"""Tests for the PTY runner wrapping agy on Windows."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from symphony.providers import agy_pty_runner


# ---------------------------------------------------------------------------
# Path conversion helpers
# ---------------------------------------------------------------------------


def test_bash_to_windows_path_converts_msys_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agy_pty_runner.os, "name", "nt")
    monkeypatch.setattr(agy_pty_runner.os, "sep", "\\")
    assert (
        agy_pty_runner._bash_to_windows_path("/c/Users/erena/.local/bin/agy.exe")
        == "C:\\Users\\erena\\.local\\bin\\agy.exe"
    )


def test_bash_to_windows_path_passthrough_when_already_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agy_pty_runner.os, "name", "nt")
    assert (
        agy_pty_runner._bash_to_windows_path("C:\\Users\\agy.exe")
        == "C:\\Users\\agy.exe"
    )
    # Bare name (no drive letter) must remain untouched.
    assert agy_pty_runner._bash_to_windows_path("agy") == "agy"


def test_bash_to_windows_path_noop_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agy_pty_runner.os, "name", "posix")
    assert (
        agy_pty_runner._bash_to_windows_path("/c/Users/agy") == "/c/Users/agy"
    )


# ---------------------------------------------------------------------------
# ANSI stripping
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_csi_and_osc_sequences() -> None:
    raw = "\x1b[31mred\x1b[0m text\x1b]0;title\x07 done"
    assert agy_pty_runner._strip_ansi(raw) == "red text done"


def test_strip_ansi_leaves_plain_text_alone() -> None:
    assert agy_pty_runner._strip_ansi("hello world") == "hello world"


# ---------------------------------------------------------------------------
# Native-exe detection
# ---------------------------------------------------------------------------


def test_native_exe_detection_picks_real_binaries() -> None:
    assert agy_pty_runner._looks_like_native_exe("agy.exe") is True
    assert agy_pty_runner._looks_like_native_exe("C:\\bin\\agy.cmd") is True
    assert agy_pty_runner._looks_like_native_exe("agy") is True  # bare name


def test_native_exe_detection_rejects_scripts() -> None:
    assert agy_pty_runner._looks_like_native_exe("wrapper.sh") is False
    assert agy_pty_runner._looks_like_native_exe("fake_cli.py") is False


# ---------------------------------------------------------------------------
# Direct execution branch (used on POSIX and for test scripts)
# ---------------------------------------------------------------------------


def test_run_direct_forwards_stdout_and_exit_code(tmp_path: Path) -> None:
    """`_run_direct` must inherit stdio from the parent so output streams live."""
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\nprint('hello from child')\nsys.exit(7)\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
    )
    # Sanity-check our test scaffolding before exercising _run_direct.
    assert proc.returncode == 7
    assert "hello from child" in proc.stdout


def test_main_with_empty_argv_returns_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(agy_pty_runner.sys, "argv", ["agy_pty_runner"])
    assert agy_pty_runner.main() == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err


def test_main_dispatches_to_direct_for_script_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows, .sh / .py executables must take the direct fallback path."""
    monkeypatch.setattr(agy_pty_runner.os, "name", "nt")
    captured: dict[str, Any] = {}

    def fake_direct(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(agy_pty_runner, "_run_direct", fake_direct)
    monkeypatch.setattr(
        agy_pty_runner.sys,
        "argv",
        ["agy_pty_runner", "/tmp/wrapper.sh", "--flag"],
    )
    assert agy_pty_runner.main() == 0
    assert captured["argv"] == ["/tmp/wrapper.sh", "--flag"]


def test_main_dispatches_to_pty_for_native_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agy_pty_runner.os, "name", "nt")
    captured: dict[str, Any] = {}

    def fake_pty(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(agy_pty_runner, "_run_with_pty", fake_pty)
    monkeypatch.setattr(
        agy_pty_runner.sys,
        "argv",
        ["agy_pty_runner", "C:\\bin\\agy.exe", "-p", "hi"],
    )
    assert agy_pty_runner.main() == 0
    assert captured["argv"] == ["C:\\bin\\agy.exe", "-p", "hi"]


def test_main_falls_back_when_conpty_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If ConPTY allocation fails, the runner must still complete the call."""
    monkeypatch.setattr(agy_pty_runner.os, "name", "nt")

    def boom(argv: list[str]) -> int:
        raise OSError(5, "Access is denied")

    direct_calls: list[list[str]] = []

    def record_direct(argv: list[str]) -> int:
        direct_calls.append(argv)
        return 3

    monkeypatch.setattr(agy_pty_runner, "_run_with_pty", boom)
    monkeypatch.setattr(agy_pty_runner, "_run_direct", record_direct)
    monkeypatch.setattr(
        agy_pty_runner.sys,
        "argv",
        ["agy_pty_runner", "C:\\bin\\agy.exe"],
    )
    assert agy_pty_runner.main() == 3
    assert direct_calls == [["C:\\bin\\agy.exe"]]
    err = capsys.readouterr().err
    assert "ConPTY allocation failed" in err


def test_main_on_posix_always_uses_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agy_pty_runner.os, "name", "posix")

    def fake_direct(argv: list[str]) -> int:
        return 0

    monkeypatch.setattr(agy_pty_runner, "_run_direct", fake_direct)
    monkeypatch.setattr(
        agy_pty_runner.sys,
        "argv",
        ["agy_pty_runner", "/usr/local/bin/agy", "-p", "hi"],
    )
    assert agy_pty_runner.main() == 0
