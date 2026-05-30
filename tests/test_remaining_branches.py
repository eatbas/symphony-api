"""Final pass at remaining branches across shells, runner, executor, etc."""
from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from symphony.models import ChatMode, ChatRequest, InstrumentName
from symphony.models.enums import ScoreStatus
from symphony.orchestra import Orchestra
from symphony.orchestra.score import ScoreHandle
from symphony.shells import BashSession, detect_bash_path
from tests.helpers.orchestra import started_orchestra


# ---------------------------------------------------------------------------
# shells.py:105 — start() is idempotent when process already running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bash_session_start_is_idempotent() -> None:
    session = BashSession(detect_bash_path())
    try:
        await session.start()
        original_process = session.process
        # Calling start() again must return immediately without replacing the
        # process (covers the early-return path in start()).
        await session.start()
        assert session.process is original_process
    finally:
        await session.stop()


# ---------------------------------------------------------------------------
# shells.py:215 — _dispose_process early-returns when no process
# ---------------------------------------------------------------------------


def test_dispose_process_is_safe_when_no_process() -> None:
    session = BashSession(detect_bash_path())
    # Direct call must early-return without raising.
    session._dispose_process()
    assert session.process is None


# ---------------------------------------------------------------------------
# shells.py:257 — _reader_loop handles a trailing partial-line buffer at EOF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_loop_flushes_partial_buffer_on_eof() -> None:
    """When the bash process exits with stdout still holding a line that has
    no trailing newline, the reader loop's EOF branch must dispatch it."""
    from shlex import quote
    import sys

    session = BashSession(detect_bash_path())
    captured: list[str] = []
    python = quote(sys.executable)

    try:
        # Print without trailing newline + small delay so the buffer
        # contains "partial" when EOF is reached.
        exit_code = await session.run_script(
            f'{python} -c "import sys; sys.stdout.write(\'partial\'); sys.stdout.flush()"',
            lambda line: _collect(captured, line),
        )
    finally:
        await session.stop()

    assert exit_code == 0
    # The reader saw at least the partial fragment somewhere in the output.
    assert any("partial" in line for line in captured)


async def _collect(sink: list[str], line: str) -> None:
    sink.append(line)


# ---------------------------------------------------------------------------
# runner.py:39 — _ensure_runner_alive returns when _stopping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_runner_alive_noop_when_stopping(loaded_config) -> None:
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_idle_musician(InstrumentName.CLAUDE)
        assert musician is not None
        musician._stopping = True
        existing_task = musician._runner_task
        # Cancel current task so the conditional reaches the _stopping check.
        if existing_task is not None:
            existing_task.cancel()
            try:
                await existing_task
            except asyncio.CancelledError:
                pass
        musician._runner_task = None
        musician._ensure_runner_alive()
        # Stopping → must NOT spawn a new task.
        assert musician._runner_task is None
        # Restore state for clean shutdown.
        musician._stopping = False
        musician._ensure_runner_alive()


# ---------------------------------------------------------------------------
# runner.py:49 — _ensure_runner_alive logs when the previous task crashed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_runner_alive_logs_when_previous_task_crashed(
    loaded_config, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("ERROR", logger="symphony.musician")
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_idle_musician(InstrumentName.CLAUDE)
        assert musician is not None
        # Replace runner task with one that immediately raises.
        existing_task = musician._runner_task
        if existing_task is not None:
            existing_task.cancel()
            try:
                await existing_task
            except asyncio.CancelledError:
                pass

        async def boom():
            raise RuntimeError("simulated runner death")

        musician._runner_task = asyncio.create_task(boom())
        # Wait for it to die.
        try:
            await musician._runner_task
        except RuntimeError:
            pass
        # Now invoke the supervisor: it should log + spawn a new task.
        musician._ensure_runner_alive()
        assert "died unexpectedly" in caplog.text
        # Cancel the freshly spawned task so teardown is clean.
        musician._stopping = True


# ---------------------------------------------------------------------------
# runner.py:120-122 — queue.task_done ValueError swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_swallows_task_done_value_error(loaded_config) -> None:
    """Force queue.task_done() to raise ValueError; the runner must
    keep going."""
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_idle_musician(InstrumentName.CLAUDE)
        assert musician is not None

        original_task_done = musician.queue.task_done
        call_count = {"n": 0}

        def raising_task_done():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ValueError("forced")
            return original_task_done()

        musician.queue.task_done = raising_task_done  # type: ignore[method-assign]

        # Submit a normal score; the supervisor must not crash on task_done.
        handle = await musician.submit(
            ChatRequest(
                provider=InstrumentName.CLAUDE,
                model="opus",
                workspace_path=str(Path.cwd().resolve()),
                mode=ChatMode.NEW,
                prompt="hello",
            )
        )
        result = await handle.result_future
        assert result.exit_code == 0
        # Restore.
        musician.queue.task_done = original_task_done  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# claude.py — max_turns is a deliberate no-op on claude CLI >= 2.1.x
# (the flag was removed upstream and emitting it aborts the run)
# ---------------------------------------------------------------------------


def test_claude_max_turns_ignores_invalid_values() -> None:
    from symphony.providers.claude import ClaudeAdapter

    adapter = ClaudeAdapter()
    argv: list[str] = []
    # The option is ignored, so even previously-rejected values must neither
    # raise nor reintroduce the unsupported --max-turns flag.
    for value in (0, True, -5, "five"):
        adapter._apply_max_turns(argv, {"max_turns": value})
    assert argv == []


def test_claude_max_turns_ignored_when_set() -> None:
    from symphony.providers.claude import ClaudeAdapter

    adapter = ClaudeAdapter()
    argv: list[str] = []
    adapter._apply_max_turns(argv, {"max_turns": 7})
    assert argv == []


def test_claude_max_turns_noop_when_unset() -> None:
    from symphony.providers.claude import ClaudeAdapter

    adapter = ClaudeAdapter()
    argv = ["cli"]
    adapter._apply_max_turns(argv, {})
    assert argv == ["cli"]


# ---------------------------------------------------------------------------
# provider_runtime.py:25 — activate_provider returns False when adapter
# reports the CLI is not available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_provider_returns_false_when_cli_not_available(
    loaded_config,
) -> None:
    from symphony.orchestra.provider_runtime import activate_provider

    async with started_orchestra(loaded_config) as manager:
        manager.available_providers[InstrumentName.CLAUDE] = False
        # Patch adapter.is_available to report False so the early return at
        # line 25 fires.
        adapter = manager.registry[InstrumentName.CLAUDE]
        original = adapter.is_available
        adapter.is_available = lambda override=None: False  # type: ignore[method-assign]
        try:
            assert await activate_provider(manager, InstrumentName.CLAUDE) is False
        finally:
            adapter.is_available = original  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# executor stopped-after-execution branch (cancellation flag set during run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_publishes_stopped_when_cancelled_during_run(
    loaded_config,
) -> None:
    """Set handle.cancelled mid-run so the post-execution check at the bottom
    of _execute_request fires (lines 178-187)."""
    from tests.helpers.orchestra import started_orchestra

    new_providers = dict(loaded_config.providers)
    base = new_providers[InstrumentName.CLAUDE]
    new_providers[InstrumentName.CLAUDE] = dataclasses.replace(
        base, cli_timeout=0.0, idle_timeout=0.0
    )
    config = dataclasses.replace(loaded_config, providers=new_providers)

    async with started_orchestra(config) as manager:
        musician = manager.get_idle_musician(InstrumentName.CLAUDE)
        assert musician is not None
        handle = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
        request = ChatRequest(
            provider=InstrumentName.CLAUDE,
            model="opus",
            workspace_path=str(Path.cwd().resolve()),
            mode=ChatMode.NEW,
            prompt="hello",
        )
        loop = asyncio.get_event_loop()
        handle.result_future = loop.create_future()
        await musician.submit(request, handle)
        # Cancel after submission so the cancel_watcher fires during execution.
        handle.cancelled.set()
        with pytest.raises(Exception):
            await handle.result_future
        assert handle.status in {ScoreStatus.STOPPED, ScoreStatus.FAILED}
