from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import shutil
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

_WINDOWS_DRIVE = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")


class ShellSessionError(RuntimeError):
    pass


class ScoreCancelledError(RuntimeError):
    """Raised when a score is cancelled by the user."""

    pass


class GitBashNotFoundError(RuntimeError):
    """Raised on Windows when Git Bash cannot be located."""

    def __init__(self) -> None:
        super().__init__(
            "Git Bash is required on Windows but was not found. "
            "Please install Git for Windows: https://git-scm.com/download/win"
        )


@dataclass(slots=True)
class _ActiveRun:
    token: str
    on_line: Callable[[str], Awaitable[None]]
    future: asyncio.Future[int]
    started: bool = False


def to_bash_path(value: str) -> str:
    match = _WINDOWS_DRIVE.match(value)
    if match:
        rest = match.group("rest").replace("\\", "/")
        return f"/{match.group('drive').lower()}/{rest}"
    return value.replace("\\", "/")


def windows_subprocess_kwargs() -> dict[str, object]:
    """Return subprocess kwargs that hide console windows on Windows."""
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return kwargs


def detect_bash_path(override: str | None = None) -> str:
    """Resolve the bash executable path.

    On non-Windows platforms any ``bash`` on *PATH* is acceptable.
    On Windows **Git Bash** is required — raises :class:`GitBashNotFoundError`
    when it cannot be located.
    """
    if override:
        return override
    if os.name != "nt":
        return shutil.which("bash") or "bash"

    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    found = shutil.which("bash")
    if found:
        return found

    raise GitBashNotFoundError()


class BashSession:
    def __init__(self, shell_path: str):
        self.shell_path = shell_path
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._current_run: _ActiveRun | None = None
        self._run_lock = asyncio.Lock()
        self._interrupt_lock = asyncio.Lock()

    async def ensure_started(self) -> None:
        if self.process and self.process.returncode is None:
            return
        await self.start()

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        kwargs = windows_subprocess_kwargs()
        env = {**os.environ, "PYTHONUTF8": "1"}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        self.process = await asyncio.create_subprocess_exec(
            self.shell_path,
            "--noprofile",
            "--norc",
            "-s",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            **kwargs,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def stop(self) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin and not process.stdin.is_closing():
            process.stdin.write(b"exit\n")
            try:
                await process.stdin.drain()
            except ConnectionResetError:
                pass
        await process.wait()
        if self._reader_task:
            await self._reader_task
        self._dispose_process()

    async def interrupt(self) -> None:
        """Stop the active shell tree so the current CLI cannot keep running."""
        async with self._interrupt_lock:
            process = self.process
            if process is None or process.returncode is not None:
                return

            if os.name == "nt":
                await self._kill_windows_process_tree(process.pid)
                await process.wait()
                await self._stop_reader_task()
                self._dispose_process()
                return

            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                return

            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
                await self._stop_reader_task()
                self._dispose_process()
                return
            except asyncio.TimeoutError:
                pass

            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            await process.wait()
            await self._stop_reader_task()
            self._dispose_process()

    async def _kill_windows_process_tree(self, pid: int) -> None:
        """Kill the bash process and its entire child tree with taskkill /T."""
        kwargs: dict[str, object] = {
            "stdout": asyncio.subprocess.DEVNULL,
            "stderr": asyncio.subprocess.DEVNULL,
        }
        kwargs.update(windows_subprocess_kwargs())
        proc = await asyncio.create_subprocess_exec(
            "taskkill", "/T", "/F", "/PID", str(pid),
            **kwargs,
        )
        await proc.wait()

    async def _stop_reader_task(self) -> None:
        current = self._current_run
        if current and not current.future.done():
            current.future.set_exception(ShellSessionError("bash musician terminated unexpectedly"))

        reader_task = self._reader_task
        if reader_task is None or reader_task.done():
            return

        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task

    def _dispose_process(self) -> None:
        process = self.process
        if process is None:
            return
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()
        self.process = None
        self._reader_task = None

    async def run_script(self, script: str, on_line: Callable[[str], Awaitable[None]]) -> int:
        await self.ensure_started()
        assert self.process and self.process.stdin

        async with self._run_lock:
            token = uuid.uuid4().hex
            loop = asyncio.get_running_loop()
            future: asyncio.Future[int] = loop.create_future()
            self._current_run = _ActiveRun(token=token, on_line=on_line, future=future)
            wrapped = self._wrap_script(token, script)
            self.process.stdin.write(wrapped.encode("utf-8"))
            await self.process.stdin.drain()
            try:
                return await future
            finally:
                self._current_run = None

    def _wrap_script(self, token: str, script: str) -> str:
        begin = f"__SYMPHONY_BEGIN__{token}"
        end = f"__SYMPHONY_END__{token}"
        return (
            f"printf '%s\\n' '{begin}'\n"
            f"__symphony_exit=0\n"
            f"{script}\n"
            f"printf '\\n%s:%s\\n' '{end}' \"$__symphony_exit\"\n"
        )

    async def _reader_loop(self) -> None:
        assert self.process and self.process.stdout
        buffer = bytearray()
        try:
            while True:
                chunk = await self.process.stdout.read(4096)
                if not chunk:
                    if buffer:
                        await self._handle_output_line(bytes(buffer))
                    current = self._current_run
                    if current and not current.future.done():
                        current.future.set_exception(ShellSessionError("bash musician terminated unexpectedly"))
                    break

                buffer.extend(chunk)
                while True:
                    newline_index = buffer.find(b"\n")
                    if newline_index < 0:
                        break

                    raw_line = bytes(buffer[: newline_index + 1])
                    del buffer[: newline_index + 1]
                    await self._handle_output_line(raw_line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = self._current_run
            if current and not current.future.done():
                current.future.set_exception(ShellSessionError(f"bash musician output reader failed: {exc}"))

    async def _handle_output_line(self, raw_line: bytes) -> None:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        current = self._current_run
        if current is None:
            return

        begin_marker = f"__SYMPHONY_BEGIN__{current.token}"
        end_prefix = f"__SYMPHONY_END__{current.token}:"
        if line == begin_marker:
            current.started = True
            return
        if line.startswith(end_prefix):
            exit_code = int(line.split(":", 1)[1])
            if not current.future.done():
                current.future.set_result(exit_code)
            current.started = False
            return
        if current.started:
            await current.on_line(line)
