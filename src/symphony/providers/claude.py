from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import CommandSpec, ParseState, ProviderAdapter
from .options import get_thinking_level, thinking_level_schema
from ..models import InstrumentName
from ..usage.aggregator import TurnSample
from ..usage.models import UsageCounters, UsageSnapshot
from ..usage.probes import build_session_log_snapshots_async


class ClaudeAdapter(ProviderAdapter):
    name = InstrumentName.CLAUDE
    default_executable = "claude"
    session_reference_format = "uuid"
    # Substrings that indicate the claude CLI has hit an unrecoverable
    # API error. The CLI sometimes prints these as part of an assistant
    # text chunk and then exits with a confusing generic code; surfacing
    # them lets the executor finalise the score with the actual cause
    # instead of "claude exited with code 1".
    #
    # We match the prefix "API Error:" alone because claude emits many
    # variants -- "Unable to connect", "The socket connection was
    # closed", "Connection error", "503 Service Unavailable", "429 Too
    # Many Requests", and others. The prefix is only printed by the CLI
    # when an actual API call has failed, so the false-positive risk is
    # very low.
    _FATAL_ERROR_PATTERNS: tuple[str, ...] = (
        "API Error:",
    )

    def new_session_ref(self) -> str | None:
        return self._uuid()

    def build_new_command(self, *, executable: str, prompt: str, model: str, provider_options: dict) -> CommandSpec:
        session_ref = self.new_session_ref()
        argv = [
            executable,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            # Bypass tool-permission prompts: Symphony invokes Claude
            # non-interactively from a sandbox bash, so the prompts
            # would otherwise stall the run.
            "--dangerously-skip-permissions",
            "--enable-auto-mode",
            "--no-chrome",
            "--session-id",
            session_ref,
            prompt,
        ]
        self._apply_model_override(argv, model)
        self._apply_effort(argv, model, provider_options)
        self._apply_max_turns(argv, provider_options)
        argv.extend(self._extra_args(provider_options))
        return CommandSpec(argv=argv, preset_session_ref=session_ref)

    def build_resume_command(self, *, executable: str, prompt: str, model: str, session_ref: str, provider_options: dict) -> CommandSpec:
        argv = [
            executable,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "--enable-auto-mode",
            "--no-chrome",
            "--resume",
            session_ref,
            prompt,
        ]
        self._apply_model_override(argv, model)
        self._apply_effort(argv, model, provider_options)
        self._apply_max_turns(argv, provider_options)
        argv.extend(self._extra_args(provider_options))
        return CommandSpec(argv=argv, preset_session_ref=session_ref)

    def model_option_schema(self, model: str) -> list[dict[str, Any]]:
        levels = self._effort_levels_for_model(model)
        if not levels:
            return []
        default = "xhigh" if "xhigh" in levels else "high"
        return thinking_level_schema(levels=levels, default=default)

    def _apply_effort(self, argv: list[str], model: str, provider_options: dict) -> None:
        levels = self._effort_levels_for_model(model)
        if not levels:
            return
        effort = get_thinking_level(provider_options, allowed=levels)
        if effort:
            argv.extend(["--effort", effort])

    @staticmethod
    def _effort_levels_for_model(model: str) -> tuple[str, ...]:
        normalized = model.lower()
        if "haiku" in normalized:
            return ()
        if "opus" in normalized:
            return ("low", "medium", "high", "xhigh", "max")
        if "sonnet" in normalized:
            return ("low", "medium", "high")
        return ("low", "medium", "high")

    def _apply_max_turns(self, argv: list[str], provider_options: dict) -> None:
        raw = provider_options.get("max_turns")
        if raw is None:
            return
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise ValueError("provider_options.max_turns must be a positive integer")
        argv.extend(["--max-turns", str(raw)])

    async def get_usage(
        self,
        *,
        executable: str,
        models: list[str],
        musician_lookup: Callable[[InstrumentName], Any | None],
        run_subprocess: Callable[..., Awaitable[tuple[int, str]]],
        now: datetime,
    ) -> list[UsageSnapshot]:
        """Return 5h-rolling and weekly snapshots aggregated from transcripts.

        Claude Code stores conversation transcripts under
        ``~/.claude/projects/<workspace-slug>/<uuid>.jsonl``. Each
        assistant message carries a ``message.usage`` object with
        ``input_tokens``, ``output_tokens``, and the two cache token
        counters. We aggregate those over both windows.
        """
        return await build_session_log_snapshots_async(
            provider=InstrumentName.CLAUDE,
            root=_claude_transcripts_root(),
            pattern="*.jsonl",
            parser=_parse_claude_line,
            now=now,
            not_found_note=(
                "No Claude transcripts at ~/.claude/projects -- run the CLI at least once."
            ),
        )

    def parse_output_line(self, line: str, state: ParseState) -> list[dict[str, object]]:
        obj = self._parse_json_or_warn(line, state)
        if obj is None:
            return []

        events: list[dict[str, object]] = []
        if obj.get("session_id") and state.session_ref != str(obj["session_id"]):
            state.session_ref = str(obj["session_id"])
            events.append({"type": "provider_session", "provider_session_ref": state.session_ref})

        if obj.get("type") == "assistant":
            message = obj.get("message", {})
            for item in message.get("content", []):
                if item.get("type") == "text":
                    text = str(item.get("text", ""))
                    # Claude streams its API errors as assistant text;
                    # spot them so we don't wait for the CLI to also
                    # crash separately.
                    self._detect_fatal_error(text, state, self._FATAL_ERROR_PATTERNS)
                    events.extend(self._append_chunk(state, text))

        if obj.get("type") == "result" and obj.get("subtype") != "success":
            errors = obj.get("errors") or [obj.get("result") or "Claude command failed"]
            state.error_message = "; ".join(str(item) for item in errors if item)
        return events


def _claude_transcripts_root() -> Path:
    """Resolve Claude Code's transcript directory.

    Pulled out as a module-level helper so tests can monkey-patch the
    function rather than the user's actual home directory.
    """
    return Path.home() / ".claude" / "projects"


def _parse_claude_line(obj: dict[str, Any]) -> TurnSample | None:
    """Map a transcript line to a :class:`TurnSample`.

    Returns ``None`` for lines that lack a usable ISO-8601 ``timestamp``
    or a ``message.usage`` object with integer token counts. Cache tokens
    are folded into ``total_tokens`` so the aggregate reflects real
    consumption against the underlying plan.
    """
    ts_raw = obj.get("timestamp")
    if not isinstance(ts_raw, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None

    cache_in = usage.get("cache_creation_input_tokens") or 0
    cache_read = usage.get("cache_read_input_tokens") or 0
    if not isinstance(cache_in, int):
        cache_in = 0
    if not isinstance(cache_read, int):
        cache_read = 0

    total = input_tokens + output_tokens + cache_in + cache_read
    return TurnSample(
        timestamp=ts,
        counters=UsageCounters(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
        ),
    )
