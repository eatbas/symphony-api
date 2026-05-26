from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import CommandSpec, ParseState, ProviderAdapter
from .codex_options import SUPPORTED_REASONING_EFFORTS, codex_model_options
from .options import get_thinking_level
from ..models import InstrumentName
from ..usage.aggregator import TurnSample
from ..usage.models import UsageCounters, UsageSnapshot
from ..usage.probes import build_session_log_snapshots_async


class CodexAdapter(ProviderAdapter):
    name = InstrumentName.CODEX
    default_executable = "codex"
    session_reference_format = "thread-id"
    model_aliases: dict[str, str] = {}

    # ``--full-auto`` is deprecated upstream (replaced by ``--sandbox
    # workspace-write``). Symphony already runs each codex invocation
    # inside a per-call bash subshell, so the additional sandbox layer
    # adds latency without buying meaningful isolation; the user
    # explicitly opted in to a yolo-style flow. ``--dangerously-bypass
    # -approvals-and-sandbox`` skips both approval prompts and the
    # sandbox in a single flag.
    _AUTO_APPROVE_ARGS = ["--dangerously-bypass-approvals-and-sandbox"]

    def build_new_command(self, *, executable: str, prompt: str, model: str, provider_options: dict) -> CommandSpec:
        argv = [executable, "exec", "--json", *self._AUTO_APPROVE_ARGS]
        self._apply_model_override(argv, self._resolve_model(model), flag="-m")
        self._apply_reasoning_effort(argv, provider_options)
        argv.extend(self._extra_args(provider_options))
        argv.append(prompt)
        return CommandSpec(argv=argv)

    def build_resume_command(self, *, executable: str, prompt: str, model: str, session_ref: str, provider_options: dict) -> CommandSpec:
        argv = [executable, "exec", "resume", "--json", *self._AUTO_APPROVE_ARGS]
        self._apply_model_override(argv, self._resolve_model(model), flag="-m")
        self._apply_reasoning_effort(argv, provider_options)
        argv.extend(self._extra_args(provider_options))
        argv.extend([session_ref, prompt])
        return CommandSpec(argv=argv, preset_session_ref=session_ref)

    def parse_output_line(self, line: str, state: ParseState) -> list[dict[str, object]]:
        obj = self._parse_json_or_warn(line, state)
        if obj is None:
            return []

        events: list[dict[str, object]] = []
        if obj.get("type") == "thread.started" and obj.get("thread_id"):
            state.session_ref = str(obj["thread_id"])
            events.append({"type": "provider_session", "provider_session_ref": state.session_ref})

        if obj.get("type") == "item.completed":
            item = obj.get("item", {})
            item_type = item.get("type")
            if item_type == "agent_message":
                events.extend(self._append_chunk(state, str(item.get("text", ""))))
            elif item_type == "error":
                state.warnings.append(str(item.get("message", "Codex reported an error item")))
        return events

    async def get_usage(
        self,
        *,
        executable: str,
        models: list[str],
        musician_lookup: Callable[[InstrumentName], Any | None],
        run_subprocess: Callable[..., Awaitable[tuple[int, str]]],
        now: datetime,
    ) -> list[UsageSnapshot]:
        """Return 5h-rolling and weekly snapshots from codex session logs.

        Codex stores session rollout files under ``~/.codex/sessions/`` so
        long as ``--ephemeral`` is not passed. Each ``turn.completed``
        event carries token usage; we aggregate those over both windows.
        Native plan-window quotas live behind a JSON-RPC app-server
        protocol that is too heavy to drive from a probe, so ``limit``
        and ``remaining`` are left ``None`` (see plan Risks).
        """
        return await build_session_log_snapshots_async(
            provider=InstrumentName.CODEX,
            root=_codex_sessions_root(),
            pattern="*.jsonl",
            parser=_parse_codex_line,
            now=now,
            not_found_note=(
                "No Codex session logs at ~/.codex/sessions -- run the CLI at least "
                "once without --ephemeral."
            ),
        )

    def _resolve_model(self, model: str) -> str:
        return self.model_aliases.get(model, model)

    def model_option_schema(self, model: str) -> list[dict[str, Any]]:
        return codex_model_options(self._resolve_model(model))

    def _apply_reasoning_effort(self, argv: list[str], provider_options: dict) -> None:
        raw = get_thinking_level(provider_options, allowed=tuple(sorted(SUPPORTED_REASONING_EFFORTS)))
        if raw is None:
            return
        argv.extend(["-c", f'model_reasoning_effort="{raw}"'])


def _codex_sessions_root() -> Path:
    """Resolve Codex's session-rollout directory."""
    return Path.home() / ".codex" / "sessions"


def _parse_codex_line(obj: dict[str, Any]) -> TurnSample | None:
    """Map a Codex session-log line to a :class:`TurnSample`.

    Codex emits ``turn.completed`` events with a ``usage`` block carrying
    ``input_tokens`` / ``output_tokens``. The timestamp lives at the top
    of the event in ISO-8601 form. Lines that don't match this shape
    (turn.started, item.* events, etc.) return ``None``.
    """
    if obj.get("type") != "turn.completed":
        return None

    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(output_tokens, int):
        return None
    if input_tokens is not None and not isinstance(input_tokens, int):
        return None

    ts = _parse_timestamp(obj)
    if ts is None:
        return None

    total = (input_tokens or 0) + output_tokens
    return TurnSample(
        timestamp=ts,
        counters=UsageCounters(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total or None,
        ),
    )


def _parse_timestamp(obj: dict[str, Any]) -> datetime | None:
    raw = obj.get("timestamp") or obj.get("ts")
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts
