from __future__ import annotations

import sys
from shlex import quote

import pytest

from symphony.shells import BashSession, detect_bash_path


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
