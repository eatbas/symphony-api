"""PTY-wrapped runner for `agy --print` on Windows.

Background
==========
``agy 1.0.2`` in print mode silently refuses to write its response to
stdout when stdout is not a TTY on Windows. Symphony's bash sessions
capture CLI output through pipes, so the response is lost. This
runner allocates a pseudo-terminal via the Windows **ConPTY** API
(``CreatePseudoConsole`` in ``kernel32``, Windows 10+), runs ``agy``
attached to it, strips ANSI escape sequences, and streams the cleaned
output to its own stdout so the parent bash session can read it
line-by-line.

We talk to ConPTY directly through ``ctypes`` rather than pulling in
``pywinpty``: the pywinpty ARM64 wheel ships native ``.pyd``/``.dll``
artefacts that are not loadable on Windows 11 ARM64, and the source
build needs the full Rust toolchain. ConPTY is part of the Windows
SDK and present on every modern Windows install, so a ctypes wrapper
is the most portable answer.

On POSIX the wrapper is unnecessary -- ``agy`` emits stdout normally
without a TTY -- so the runner ``execvp``'s the target argv directly
with no extra overhead.

Usage (from Symphony's Antigravity adapter)
-------------------------------------------
``python -m symphony.providers.agy_pty_runner <agy_path> [args...]``

The first positional argument is the agy executable; the remaining
arguments are forwarded verbatim.

Exit code: the runner exits with the wrapped child's exit code so
Symphony's executor can detect CLI failures the usual way.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

# ANSI CSI/OSC sequences that the Windows ConPTY may inject (cursor
# moves, colour codes, mode switches). Stripped before forwarding so
# the response text stays clean.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[=>]")

# A wide PTY size avoids visual line-wrapping inside the response.
# 300 columns sits comfortably above any normal LLM response width.
_PTY_ROWS = 40
_PTY_COLS = 300

# Sentinel block size for reads off the ConPTY master pipe.
_READ_CHUNK = 4096

# ``agy`` treats a ConPTY as an interactive TUI session and never exits
# on its own after streaming the response. We use a quiet-timeout
# watchdog: once the child stops producing output for this many seconds
# we terminate it. The value must comfortably exceed two upstream
# quiet windows: the per-character "drip" animation pause (a few
# hundred ms) and the longer pause between the TUI splash render and
# the first response chunk while agy authenticates and resolves the
# model against the Antigravity backend (observed at up to ~10 s on
# Windows ARM64).
_QUIET_TIMEOUT_SECONDS = 30.0

# Polling interval inside the read loop. Short enough for snappy
# detection, long enough that the loop is not a hot CPU spinner.
_POLL_INTERVAL_SECONDS = 0.1


def _bash_to_windows_path(path: str) -> str:
    """Convert MSYS-style ``/c/foo/bar`` to ``C:\\foo\\bar``.

    Symphony's adapter base normalises Windows paths to MSYS form
    when building argv so Git Bash can locate the binaries; the Win32
    APIs require native Windows paths.
    """
    if os.name != "nt":
        return path
    if len(path) >= 3 and path[0] == "/" and path[2] == "/" and path[1].isalpha():
        return f"{path[1].upper()}:\\{path[3:].replace('/', os.sep)}"
    return path


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _looks_like_native_exe(path: str) -> bool:
    """Return True when *path* is a real Windows binary we can ConPTY-spawn.

    The ConPTY runner cannot directly spawn ``.sh`` test wrappers or
    ``.py`` scripts; for those we fall back to a direct subprocess
    invocation. The real ``agy.exe`` is always a native binary.
    """
    lower = path.lower()
    return lower.endswith((".exe", ".cmd", ".bat", ".com")) or "." not in os.path.basename(lower)


def _run_with_pty(argv: list[str]) -> int:  # pragma: no cover - Win32 ConPTY path
    """Spawn ``argv`` inside a Windows ConPTY and stream output to stdout.

    Coverage note: this function relies on the Windows ConPTY API and a
    live ``agy`` binary to exercise; it can only be validated through
    integration runs on Windows. Unit tests cover the dispatch logic
    in :func:`main` instead.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # --- Win32 constants ----------------------------------------------------
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    STILL_ACTIVE = 259

    # --- Structures ---------------------------------------------------------
    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", STARTUPINFOW),
            ("lpAttributeList", wintypes.LPVOID),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    # --- Signatures ---------------------------------------------------------
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL

    kernel32.CreatePseudoConsole.argtypes = [
        COORD,
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT

    kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
    kernel32.ClosePseudoConsole.restype = None

    kernel32.InitializeProcThreadAttributeList.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL

    kernel32.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL

    kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    kernel32.DeleteProcThreadAttributeList.restype = None

    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOEXW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL

    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL

    kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL

    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    # --- Step 1: pipes for child stdin (in_*) and child stdout (out_*) -----
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    in_read = wintypes.HANDLE()
    in_write = wintypes.HANDLE()
    out_read = wintypes.HANDLE()
    out_write = wintypes.HANDLE()
    if not kernel32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), ctypes.byref(sa), 0):
        raise OSError(ctypes.get_last_error(), "CreatePipe(in) failed")
    if not kernel32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), ctypes.byref(sa), 0):
        raise OSError(ctypes.get_last_error(), "CreatePipe(out) failed")

    # --- Step 2: pseudo-console wrapping the read+write ends ---------------
    hpc = wintypes.HANDLE()
    size = COORD(_PTY_COLS, _PTY_ROWS)
    hr = kernel32.CreatePseudoConsole(size, in_read, out_write, 0, ctypes.byref(hpc))
    if hr != 0:
        raise OSError(hr, "CreatePseudoConsole failed")
    # The child inherits these handles; parent doesn't need them.
    kernel32.CloseHandle(in_read)
    kernel32.CloseHandle(out_write)

    # --- Step 3: attribute list with the pseudo console handle -------------
    list_size = ctypes.c_size_t(0)
    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(list_size))
    attr_buf = (ctypes.c_byte * list_size.value)()
    if not kernel32.InitializeProcThreadAttributeList(attr_buf, 1, 0, ctypes.byref(list_size)):
        raise OSError(ctypes.get_last_error(), "InitializeProcThreadAttributeList failed")
    if not kernel32.UpdateProcThreadAttribute(
        attr_buf,
        0,
        ctypes.c_void_p(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
        hpc,
        ctypes.sizeof(wintypes.HANDLE),
        None,
        None,
    ):
        raise OSError(ctypes.get_last_error(), "UpdateProcThreadAttribute failed")

    si_ex = STARTUPINFOEXW()
    si_ex.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
    si_ex.lpAttributeList = ctypes.cast(attr_buf, wintypes.LPVOID)
    pi = PROCESS_INFORMATION()

    # --- Step 4: build command line and spawn ------------------------------
    argv_native = [_bash_to_windows_path(a) for a in argv]
    # CreateProcessW takes a writable buffer for the command line.
    cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv_native))

    if not kernel32.CreateProcessW(
        None,
        cmdline,
        None,
        None,
        False,
        EXTENDED_STARTUPINFO_PRESENT,
        None,
        None,
        ctypes.byref(si_ex),
        ctypes.byref(pi),
    ):
        err = ctypes.get_last_error()
        kernel32.DeleteProcThreadAttributeList(attr_buf)
        kernel32.ClosePseudoConsole(hpc)
        kernel32.CloseHandle(in_write)
        kernel32.CloseHandle(out_read)
        raise OSError(err, f"CreateProcessW failed (winerror {err})")

    # --- Step 5: stream output, terminate after a quiet period -------------
    # ``agy`` interprets the ConPTY as a live TUI and never closes
    # stdout on its own. We poll for available bytes via
    # ``PeekNamedPipe`` instead of a blocking ReadFile, watch the
    # elapsed time since the last byte, and ``TerminateProcess`` once
    # the child has been silent for ``_QUIET_TIMEOUT_SECONDS``.
    buf = ctypes.create_string_buffer(_READ_CHUNK)
    bytes_read = wintypes.DWORD()
    bytes_avail = wintypes.DWORD()
    pending = ""
    last_data_time: float | None = None  # ``None`` until first byte arrives.
    terminated = False
    try:
        while True:
            # Always check the child first. If it exited cleanly we're
            # done. The peek call below can spuriously fail on ConPTY
            # output handles immediately after spawn, so we never use
            # PeekNamedPipe failure as an exit signal.
            wait = kernel32.WaitForSingleObject(pi.hProcess, 0)
            child_exited = wait == 0  # WAIT_OBJECT_0 = signalled

            ok = kernel32.PeekNamedPipe(
                out_read,
                None,
                0,
                None,
                ctypes.byref(bytes_avail),
                None,
            )

            if ok and bytes_avail.value > 0:
                to_read = min(bytes_avail.value, _READ_CHUNK)
                ok = kernel32.ReadFile(
                    out_read,
                    buf,
                    to_read,
                    ctypes.byref(bytes_read),
                    None,
                )
                if not ok or bytes_read.value == 0:
                    if child_exited:
                        break
                    time.sleep(_POLL_INTERVAL_SECONDS)
                    continue
                last_data_time = time.monotonic()
                chunk = buf.raw[: bytes_read.value].decode("utf-8", errors="replace")
                pending += chunk
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    sys.stdout.write(_strip_ansi(line.rstrip("\r")) + "\n")
                    sys.stdout.flush()
                continue

            # No data right now. If the child has exited and there is
            # nothing left to drain we're done. Otherwise wait, checking
            # the quiet-timeout watchdog (only armed after the first
            # byte so agy's multi-second TUI startup is never killed
            # early).
            if child_exited:
                break
            if (
                last_data_time is not None
                and time.monotonic() - last_data_time > _QUIET_TIMEOUT_SECONDS
            ):
                kernel32.TerminateProcess(pi.hProcess, 0)
                terminated = True
                break
            time.sleep(_POLL_INTERVAL_SECONDS)
    finally:
        if pending.strip():
            sys.stdout.write(_strip_ansi(pending.rstrip("\r")) + "\n")
            sys.stdout.flush()
        kernel32.WaitForSingleObject(pi.hProcess, 0xFFFFFFFF)  # INFINITE
        exit_code = wintypes.DWORD(STILL_ACTIVE)
        kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code))
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)
        kernel32.DeleteProcThreadAttributeList(attr_buf)
        kernel32.ClosePseudoConsole(hpc)
        kernel32.CloseHandle(in_write)
        kernel32.CloseHandle(out_read)
    # When we forced the child to quit because the response was complete
    # we still report success -- the agy non-exit on ConPTY is the
    # upstream bug we are routing around.
    if terminated:
        return 0
    return int(exit_code.value)


def _run_direct(argv: list[str]) -> int:
    """Run *argv* without PTY allocation, inheriting stdio from the parent.

    Used on POSIX (where ``agy`` writes to non-TTY stdout fine) and
    for test bash wrappers / .py scripts that the ConPTY path cannot
    spawn directly. ``stdin`` is piped from ``os.devnull`` so the
    child never blocks waiting for input.
    """
    with open(os.devnull, "rb") as devnull:
        proc = subprocess.run(argv, stdin=devnull, check=False)
    return proc.returncode


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write("usage: agy_pty_runner <agy_executable> [args...]\n")
        return 2

    # ConPTY output (and the agy TUI) emits Unicode glyphs that the
    # default Windows console code page (cp1252) cannot encode. Force
    # the runner's stdout/stderr to UTF-8 with a replacement strategy
    # so a stray glyph never crashes the wrapper mid-stream.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - reconfigure unavailable
        pass

    debug = os.environ.get("SYMPHONY_AGY_PTY_DEBUG") == "1"
    if debug:
        sys.stderr.write(f"agy_pty_runner: argv={argv!r} os.name={os.name}\n")
        sys.stderr.write(
            f"agy_pty_runner: native_exe({argv[0]!r})={_looks_like_native_exe(argv[0])}\n",
        )

    if os.name != "nt" or not _looks_like_native_exe(argv[0]):
        if debug:
            sys.stderr.write("agy_pty_runner: taking direct branch\n")
        return _run_direct(argv)

    if debug:
        sys.stderr.write("agy_pty_runner: taking ConPTY branch\n")
    try:
        return _run_with_pty(argv)
    except OSError as exc:
        sys.stderr.write(
            f"agy_pty_runner: ConPTY allocation failed ({exc!s}); falling back "
            "to direct invocation. Antigravity may not produce output.\n",
        )
        return _run_direct(argv)


if __name__ == "__main__":
    sys.exit(main())
