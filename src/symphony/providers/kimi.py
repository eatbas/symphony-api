from __future__ import annotations

import os
import shlex
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import CommandSpec, ParseState, ProviderAdapter
from .options import boolean_thinking_schema, get_ralph_iterations, ralph_iterations_schema, thinking_enabled
from ..models import InstrumentName
from ..shells import to_bash_path
from ..usage.aggregator import TurnSample
from ..usage.models import UsageCounters, UsageSnapshot
from ..usage.probes import build_session_log_snapshots_async


class KimiAdapter(ProviderAdapter):
    name = InstrumentName.KIMI
    default_executable = "kimi"
    session_reference_format = "opaque-string"
    _AUTOMATION_ARGS = ["--print", "--output-format", "stream-json"]
    # Substrings that the kimi-for-coding CLI prints when its underlying
    # LLM connection or session has unrecoverably failed. After printing
    # one of these the CLI may sit idle without exiting; the executor
    # treats any of them as a terminal failure and stops the run.
    _FATAL_ERROR_PATTERNS: tuple[str, ...] = (
        "LLM provider error when running agent",
        "ERROR: Unable to connect to provider",
        "Session expired -- please reauthenticate",
    )

    def new_session_ref(self) -> str | None:
        return self._uuid()

    def build_new_command(self, *, executable: str, prompt: str, model: str, provider_options: dict) -> CommandSpec:
        session_ref = self.new_session_ref()
        return CommandSpec(
            argv=self._build_argv(
                executable=executable,
                prompt=prompt,
                model=model,
                session_ref=session_ref,
                provider_options=provider_options,
            ),
            preset_session_ref=session_ref,
        )

    def build_resume_command(self, *, executable: str, prompt: str, model: str, session_ref: str, provider_options: dict) -> CommandSpec:
        return CommandSpec(
            argv=self._build_argv(
                executable=executable,
                prompt=prompt,
                model=model,
                session_ref=session_ref,
                provider_options=provider_options,
            ),
            preset_session_ref=session_ref,
        )

    def _build_argv(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        session_ref: str | None,
        provider_options: dict,
    ) -> list[str]:
        argv = [
            executable,
            "--yolo",
        ]
        argv.append("--thinking" if thinking_enabled(provider_options) else "--no-thinking")
        argv.extend(
            [
                "--session",
                session_ref,
                *self._AUTOMATION_ARGS,
                "--prompt",
                prompt,
            ]
        )
        self._apply_model_override(argv, model)
        ralph = get_ralph_iterations(provider_options)
        if ralph is not None:
            argv.extend(["--max-ralph-iterations", str(ralph)])
        argv.extend(self._extra_args(provider_options))
        return argv

    def model_option_schema(self, model: str) -> list[dict[str, Any]]:
        return boolean_thinking_schema(default="enabled") + ralph_iterations_schema(default="1")

    def make_shell_script(self, workspace_path: str, command: CommandSpec) -> str:
        workspace = shlex.quote(to_bash_path(workspace_path))
        shell_command = shlex.join(self._normalize_argv(command.argv))
        return (
            f"export PYTHONIOENCODING=utf-8\n"
            f"if ! cd -- {workspace}; then\n"
            f"  echo 'Failed to enter workspace: {workspace_path}'\n"
            f"  __symphony_exit=97\n"
            f"else\n"
            f"  {shell_command} < /dev/null\n"
            f"  __symphony_exit=$?\n"
            f"fi"
        )

    async def get_usage(
        self,
        *,
        executable: str,
        models: list[str],
        musician_lookup: Callable[[InstrumentName], Any | None],
        run_subprocess: Callable[..., Awaitable[tuple[int, str]]],
        now: datetime,
    ) -> list[UsageSnapshot]:
        """Return 5h-rolling and weekly snapshots from kimi session logs.

        Kimi-for-coding stores sessions and logs under ``~/.kimi``
        (override via ``KIMI_SHARE_DIR``). The stream-json output of each
        turn includes usage counters; we walk the JSONL files and
        aggregate per window. No plan-window quota is published by the
        CLI, so ``limit`` and ``remaining`` are left ``None``.
        """
        return await build_session_log_snapshots_async(
            provider=InstrumentName.KIMI,
            root=_kimi_sessions_root(),
            pattern="*.jsonl",
            parser=_parse_kimi_line,
            now=now,
            not_found_note=(
                "No Kimi session logs at $KIMI_SHARE_DIR/sessions (defaults to "
                "~/.kimi/sessions) -- run the CLI at least once."
            ),
        )

    def parse_output_line(self, line: str, state: ParseState) -> list[dict[str, object]]:
        # Always scan the raw line for known fatal error markers --
        # kimi often emits these as plain text wrapped in
        # ``<system>...</system>`` tags rather than as JSON, and once
        # we've seen one the CLI may stay alive without producing
        # further useful output.
        self._detect_fatal_error(line, state, self._FATAL_ERROR_PATTERNS)

        obj = self._parse_json(line)
        if obj is None:
            # Non-JSON lines (plain text progress, thinking, etc.) — emit as-is.
            stripped = line.strip()
            if stripped:
                return self._append_chunk(state, stripped)
            return []

        events: list[dict[str, object]] = []
        content_items = obj.get("content", [])
        if not isinstance(content_items, list):
            content_items = []

        for item in content_items:
            item_type = item.get("type", "")
            if item_type == "text":
                text = str(item.get("text", ""))
                self._detect_fatal_error(text, state, self._FATAL_ERROR_PATTERNS)
                events.extend(self._append_chunk(state, text))
            elif item_type == "tool_use":
                name = item.get("name", "unknown")
                tool_input = item.get("input", {})
                summary = self._summarise_tool_call(name, tool_input)
                events.extend(self._append_chunk(state, summary))
            elif item_type == "tool_result":
                output = str(item.get("output", item.get("content", "")))
                if output.strip():
                    self._detect_fatal_error(output, state, self._FATAL_ERROR_PATTERNS)
                    events.extend(self._append_chunk(state, self._summarise_tool_result(output)))

        for tool_call in obj.get("tool_calls", []):
            function = tool_call.get("function", {})
            name = str(function.get("name", "unknown"))
            tool_input = self._parse_tool_arguments(function.get("arguments"))
            summary = self._summarise_tool_call(name, tool_input)
            events.extend(self._append_chunk(state, summary))

        if obj.get("role") == "tool":
            content = obj.get("content")
            if isinstance(content, str) and content.strip():
                self._detect_fatal_error(content, state, self._FATAL_ERROR_PATTERNS)
                events.extend(self._append_chunk(state, self._summarise_tool_result(content)))
        return events

    @staticmethod
    def _summarise_tool_call(name: str, tool_input: dict) -> str:
        """Produce a concise human-readable summary of a tool call."""
        path = tool_input.get("path") or tool_input.get("file_path") or ""
        command = tool_input.get("command") or ""
        if path:
            return f"⚙ {name}: {path}"
        if command:
            # Show first 120 chars of the command.
            short = command[:120] + ("…" if len(command) > 120 else "")
            return f"⚙ {name}: {short}"
        return f"⚙ {name}"

    def _parse_tool_arguments(self, raw_arguments: object) -> dict:
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if isinstance(raw_arguments, str):
            parsed = self._parse_json(raw_arguments)
            if isinstance(parsed, dict):
                return parsed
        return {}

    @staticmethod
    def _summarise_tool_result(output: str) -> str:
        stripped = output.strip()
        return stripped[:300]


def _kimi_sessions_root() -> Path:
    """Resolve Kimi's session directory.

    Honours ``KIMI_SHARE_DIR`` per the Kimi CLI documentation, defaulting
    to ``~/.kimi/sessions`` when the override is unset or empty.
    """
    share = os.environ.get("KIMI_SHARE_DIR")
    base = Path(share) if share else Path.home() / ".kimi"
    return base / "sessions"


def _parse_kimi_line(obj: dict[str, Any]) -> TurnSample | None:
    """Map a Kimi session-log line to a :class:`TurnSample`.

    The kimi stream-json format wraps each model turn as a JSON object
    containing ``usage`` (input/output token counts) plus a timestamp.
    Lines without those fields (tool calls, progress, plain text) return
    ``None``.
    """
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
    if input_tokens is not None and not isinstance(input_tokens, int):
        return None
    if not isinstance(output_tokens, int):
        return None

    ts = _parse_kimi_timestamp(obj)
    if ts is None:
        return None

    total_field = usage.get("total_tokens")
    if isinstance(total_field, int):
        total = total_field
    else:
        total = (input_tokens or 0) + output_tokens

    return TurnSample(
        timestamp=ts,
        counters=UsageCounters(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total or None,
        ),
    )


def _parse_kimi_timestamp(obj: dict[str, Any]) -> datetime | None:
    raw = obj.get("timestamp") or obj.get("ts") or obj.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts
