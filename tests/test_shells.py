from __future__ import annotations

import sys
from shlex import quote
from types import SimpleNamespace

import pytest

import symphony.shells as shells
from symphony.shells import BashSession, detect_bash_path, windows_subprocess_kwargs


@pytest.mark.asyncio
async def test_bash_session_handles_long_output_without_newline() -> None:
    session = BashSession(detect_bash_path())
    captured: list[str] = []
    python = quote(sys.executable)

    try:
        exit_code = await session.run_script(
            f"{python} -c \"import sys; sys.stdout.write('x' * 70000)\"",
            lambda line: _collect_output(captured, line),
        )
    finally:
        await session.stop()

    assert exit_code == 0
    assert captured == ["x" * 70000]


async def _collect_output(captured: list[str], line: str) -> None:
    captured.append(line)


def test_windows_subprocess_kwargs_returns_creationflags_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shells, "os", SimpleNamespace(name="nt"), raising=False)
    monkeypatch.setattr(shells, "subprocess", SimpleNamespace(CREATE_NO_WINDOW=123), raising=False)

    assert windows_subprocess_kwargs() == {"creationflags": 123}
