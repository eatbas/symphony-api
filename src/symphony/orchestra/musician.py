from __future__ import annotations

import asyncio
from typing import Any

from ..models import ChatRequest, ChatResponse, InstrumentName, MusicianInfo
from ..models.enums import ScoreStatus
from ..providers.base import ProviderAdapter
from ..shells import BashSession, ScoreCancelledError, ShellSessionError
from .score import ScoreHandle, _safe_error_message


class Musician:
    def __init__(
        self,
        *,
        provider: InstrumentName,
        model: str,
        adapter: ProviderAdapter,
        executable: str,
        shell_path: str,
        default_options: dict[str, Any],
        session_models: dict[tuple[InstrumentName, str], str],
        cli_timeout: float = 300.0,
        idle_timeout: float = 300.0,
    ) -> None:
        self.provider = provider
        self.model = model
        self.adapter = adapter
        self.executable = executable
        self.shell_backend = shell_path
        self.default_options = default_options
        self.session_models = session_models
        self.cli_timeout = cli_timeout or 0.0
        self.idle_timeout = idle_timeout or 0.0
        self.shell = BashSession(shell_path)
        self.queue: asyncio.Queue[tuple[ChatRequest, ScoreHandle]] = asyncio.Queue()
        self.busy = False
        self.ready = False
        self.last_error: str | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._current_handle: ScoreHandle | None = None

    async def start(self) -> None:
        try:
            await self.shell.start()
            self.ready = True
            self.last_error = None
        except Exception as exc:  # pragma: no cover
            self.ready = False
            self.last_error = str(exc)
        if self._runner_task is None:
            self._runner_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._runner_task:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass
        await self.shell.stop()

    async def submit(self, request: ChatRequest, handle: ScoreHandle | None = None) -> ScoreHandle:
        loop = asyncio.get_running_loop()
        handle = handle or ScoreHandle(
            result_future=loop.create_future(),
            provider=request.provider,
            model=request.model,
        )
        if handle.result_future is None:
            handle.result_future = loop.create_future()
        handle.provider = request.provider
        handle.model = request.model
        await self.queue.put((request, handle))
        return handle

    async def run_quick_command(self, script: str, timeout: float | None = None) -> tuple[int, str]:
        lines: list[str] = []

        async def collect(line: str) -> None:
            lines.append(line)

        coro = self.shell.run_script(script, collect)
        if timeout is not None:
            exit_code = await asyncio.wait_for(coro, timeout=timeout)
        else:
            exit_code = await coro
        return exit_code, "\n".join(lines)

    def info(self) -> MusicianInfo:
        return MusicianInfo(
            provider=self.provider,
            model=self.model,
            shell_backend=self.shell_backend,
            ready=self.ready,
            busy=self.busy,
            queue_length=self.queue.qsize(),
            last_error=self.last_error,
        )

    async def _run(self) -> None:
        while True:
            request, handle = await self.queue.get()
            self.busy = True
            try:
                # Skip scores cancelled while queued
                if handle.cancelled.is_set():
                    handle.status = ScoreStatus.STOPPED
                    await handle.publish(
                        {
                            "type": "stopped",
                            "score_id": handle.score_id,
                            "provider": self.provider.value,
                            "model": self.model,
                        }
                    )
                    if handle.result_future and not handle.result_future.done():
                        handle.result_future.set_exception(
                            ScoreCancelledError(f"Score {handle.score_id} cancelled while queued")
                        )
                    continue

                handle.status = ScoreStatus.RUNNING
                self._current_handle = handle

                if not self.ready or self.shell.process is None or self.shell.process.returncode is not None:
                    await self.shell.start()
                    self.ready = True
                    self.last_error = None
                response = await self._execute_request(request, handle)
                handle.status = ScoreStatus.COMPLETED
                if handle.result_future and not handle.result_future.done():
                    handle.result_future.set_result(response)
            except ScoreCancelledError:
                handle.status = ScoreStatus.STOPPED
                if handle.result_future and not handle.result_future.done():
                    handle.result_future.set_exception(
                        ScoreCancelledError(f"Score {handle.score_id} was stopped")
                    )
            except Exception as exc:
                error_msg = _safe_error_message(exc)
                self.last_error = error_msg
                handle.status = ScoreStatus.FAILED

                shell_alive = self.shell.process is not None and self.shell.process.returncode is None
                if not shell_alive:
                    self.ready = False

                await handle.publish(
                    {
                        "type": "failed",
                        "error": error_msg,
                        "provider": self.provider.value,
                        "model": self.model,
                    }
                )
                if handle.result_future and not handle.result_future.done():
                    handle.result_future.set_exception(exc)
            finally:
                self._current_handle = None
                self.busy = False
                self.queue.task_done()

    async def _execute_request(self, request: ChatRequest, handle: ScoreHandle) -> ChatResponse:
        if request.mode.value == "resume" and request.provider_session_ref:
            existing_model = self.session_models.get((self.provider, request.provider_session_ref))
            if existing_model and existing_model != request.model:
                raise ShellSessionError(
                    f"Session {request.provider_session_ref} was created under model "
                    f"{existing_model} and cannot be resumed with {request.model}"
                )

        provider_options = {**self.default_options, **request.provider_options}
        command = self.adapter.build_command(
            executable=self.executable,
            mode=request.mode,
            prompt=request.prompt,
            model=request.model,
            session_ref=request.provider_session_ref,
            provider_options=provider_options,
        )
        parse_state = self.adapter.initial_parse_state(command.preset_session_ref or request.provider_session_ref)

        await handle.publish(
            {
                "type": "run_started",
                "provider": self.provider.value,
                "model": request.model,
                "score_id": handle.score_id,
            }
        )
        if parse_state.session_ref:
            await handle.publish({"type": "provider_session", "provider_session_ref": parse_state.session_ref})

        idle_event = asyncio.Event()
        idle_event.set()  # Mark as active initially.

        async def on_line(line: str) -> None:
            idle_event.set()  # Reset idle timer on every output line.
            if handle.cancelled.is_set():
                return
            for event in self.adapter.parse_output_line(line, parse_state):
                await handle.publish(event)

        script = self.adapter.make_shell_script(request.workspace_path, command)
        timeout = self.cli_timeout if self.cli_timeout > 0 else None
        cancel_watcher = asyncio.create_task(self._cancel_watcher(handle))
        idle_watcher = asyncio.create_task(
            self._idle_watcher(handle, idle_event)
        ) if self.idle_timeout > 0 else None
        try:
            exit_code = await asyncio.wait_for(
                self.shell.run_script(script, on_line),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self.shell.interrupt()
            await asyncio.sleep(0.5)
            # If bash is still alive, forcibly restart it
            if self.shell.process and self.shell.process.returncode is None:
                await self.shell.stop()
                await self.shell.start()
            raise ShellSessionError(
                f"{self.provider.value} CLI timed out after {self.cli_timeout:.0f}s"
            )
        except ShellSessionError as exc:
            if handle.cancelled.is_set():
                raise ScoreCancelledError(f"Score {handle.score_id} was stopped") from exc
            raise
        finally:
            for task in (cancel_watcher, idle_watcher):
                if task is not None and not task.done():
                    task.cancel()
            for task in (cancel_watcher, idle_watcher):
                if task is not None:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

        # Check if the score was cancelled during execution
        if handle.cancelled.is_set():
            await handle.publish(
                {
                    "type": "stopped",
                    "score_id": handle.score_id,
                    "provider": self.provider.value,
                    "model": self.model,
                }
            )
            raise ScoreCancelledError(f"Score {handle.score_id} was stopped")

        final_text = "\n".join(parse_state.output_chunks).strip()
        response = ChatResponse(
            provider=self.provider,
            model=request.model,
            provider_session_ref=parse_state.session_ref,
            final_text=final_text,
            exit_code=exit_code,
            warnings=parse_state.warnings,
            score_id=handle.score_id,
        )

        if parse_state.error_message or exit_code != 0:
            error_message = parse_state.error_message or f"{self.provider.value} exited with code {exit_code}"
            await handle.publish(
                {
                    "type": "failed",
                    "provider": self.provider.value,
                    "model": request.model,
                    "provider_session_ref": parse_state.session_ref,
                    "exit_code": exit_code,
                    "warnings": parse_state.warnings,
                    "error": error_message,
                }
            )
            raise ShellSessionError(error_message)

        if parse_state.session_ref:
            self.session_models[(self.provider, parse_state.session_ref)] = request.model

        await handle.publish(
            {
                "type": "completed",
                "provider": self.provider.value,
                "model": request.model,
                "provider_session_ref": parse_state.session_ref,
                "final_text": final_text,
                "exit_code": exit_code,
                "warnings": parse_state.warnings,
                "score_id": handle.score_id,
            }
        )
        return response

    async def _idle_watcher(self, handle: ScoreHandle, idle_event: asyncio.Event) -> None:
        """Kill the running CLI if no output is received for ``idle_timeout`` seconds.

        Runs as a background task alongside :meth:`_execute_request`. Each line of
        CLI output sets ``idle_event``; this watcher clears it and waits. If the
        event is not set again within the timeout window, the CLI is assumed stuck
        and the shell is interrupted, causing the run to fail with a clear message.
        """
        while not handle.cancelled.is_set():
            idle_event.clear()
            try:
                await asyncio.wait_for(idle_event.wait(), timeout=self.idle_timeout)
            except asyncio.TimeoutError:
                if handle.cancelled.is_set():
                    return
                await self.shell.interrupt()
                await asyncio.sleep(0.5)
                if self.shell.process and self.shell.process.returncode is None:
                    await self.shell.stop()
                    await self.shell.start()
                    self.ready = True
                else:
                    self.ready = False
                raise ShellSessionError(
                    f"{self.provider.value} CLI produced no output for "
                    f"{self.idle_timeout:.0f}s — assumed stuck"
                )

    async def _cancel_watcher(self, handle: ScoreHandle) -> None:
        """Kill the running CLI as soon as the score's cancelled flag is set.

        Runs as a background task alongside :meth:`_execute_request`.  When the
        user presses Stop, :meth:`Orchestra.stop_score` sets ``handle.cancelled``;
        this watcher notices and interrupts the shell, killing the entire CLI
        process tree immediately.  The shell is restarted so the musician is idle
        and ready for the next score.
        """
        await handle.cancelled.wait()
        await self.shell.interrupt()
        try:
            await self.shell.start()
            self.ready = True
        except Exception:
            self.ready = False
