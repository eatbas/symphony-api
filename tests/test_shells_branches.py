"""Additional branch coverage for symphony.shells."""
from __future__ import annotations

import asyncio
from shlex import quote
import sys

import pytest

import symphony.shells as shells
from symphony.shells import BashSession, detect_bash_path, to_bash_path


# ---------------------------------------------------------------------------
# to_bash_path
# ---------------------------------------------------------------------------


class TestToBashPath:
    def test_passthrough_for_posix_path(self) -> None:
        assert to_bash_path("/home/user/project") == "/home/user/project"

    def test_translates_windows_drive_prefix(self) -> None:
        assert to_bash_path(r"C:\Users\Alice") == "/c/Users/Alice"

    def test_translates_lowercase_drive(self) -> None:
        assert to_bash_path(r"d:\work") == "/d/work"

    def test_translates_forward_slash_drive(self) -> None:
        assert to_bash_path("E:/Projects/foo") == "/e/Projects/foo"

    def test_normalises_backslashes_when_no_drive_letter(self) -> None:
        assert to_bash_path(r"some\nested\path") == "some/nested/path"


# ---------------------------------------------------------------------------
# detect_bash_path
# ---------------------------------------------------------------------------


class TestDetectBashPath:
    def test_uses_override_when_provided(self) -> None:
        assert detect_bash_path("/custom/bash") == "/custom/bash"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only test; on Windows detect_bash_path resolves to bash.exe, not 'bash'.",
    )
    def test_returns_which_bash_on_posix(self) -> None:
        # On Linux/Mac CI this returns whatever bash is on PATH.
        result = detect_bash_path()
        assert result.endswith("bash")


# ---------------------------------------------------------------------------
# BashSession behaviours
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bash_session_stop_when_process_already_exited() -> None:
    session = BashSession(detect_bash_path())
    await session.start()
    # Kill underlying process directly so stop() takes the already-exited path.
    assert session.process is not None
    session.process.kill()
    await session.process.wait()
    # stop() must not raise even though the bash process is already gone.
    await session.stop()
    assert session.process is None


@pytest.mark.asyncio
async def test_bash_session_stop_when_never_started() -> None:
    session = BashSession(detect_bash_path())
    # Calling stop() before start() must be a no-op (covers early return).
    await session.stop()
    assert session.process is None


@pytest.mark.asyncio
async def test_bash_session_run_script_returns_nonzero_exit_code() -> None:
    session = BashSession(detect_bash_path())
    captured: list[str] = []
    try:
        exit_code = await session.run_script(
            "echo hi\n__symphony_exit=7",
            lambda line: _collect(captured, line),
        )
    finally:
        await session.stop()
    assert exit_code == 7
    assert "hi" in captured


@pytest.mark.asyncio
async def test_bash_session_interrupt_after_start_terminates_process() -> None:
    session = BashSession(detect_bash_path())
    await session.start()
    try:
        await session.interrupt()
        # After interrupt, the process should be cleared.
        assert session.process is None
    finally:
        # Defensive — already cleared by interrupt.
        await session.stop()


@pytest.mark.asyncio
async def test_bash_session_interrupt_noop_when_no_process() -> None:
    session = BashSession(detect_bash_path())
    # interrupt() before start() must early-return without raising.
    await session.interrupt()


@pytest.mark.asyncio
async def test_bash_session_captures_stdout_chunks() -> None:
    """Smoke: covers the begin/end marker handling + reader loop."""
    session = BashSession(detect_bash_path())
    captured: list[str] = []
    python = quote(sys.executable)
    try:
        exit_code = await session.run_script(
            f"{python} -c \"print('alpha'); print('beta')\"",
            lambda line: _collect(captured, line),
        )
    finally:
        await session.stop()
    assert exit_code == 0
    assert captured[:2] == ["alpha", "beta"]


async def _collect(captured: list[str], line: str) -> None:
    captured.append(line)
