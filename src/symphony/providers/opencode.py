from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from .base import CommandSpec, ParseState, ProviderAdapter
from .options import boolean_thinking_schema, thinking_enabled
from ..models import InstrumentName
from ..usage.models import UsageSnapshot


class OpenCodeAdapter(ProviderAdapter):
    """Adapter for the OpenCode CLI (opencode-ai)."""

    name = InstrumentName.OPENCODE
    default_executable = "opencode"
    session_reference_format = "opaque-string"

    # Default provider prefix for models that don't already include one.
    _DEFAULT_PROVIDER = "zai-coding-plan"

    def _resolve_model(self, model: str) -> str:
        """Ensure the model has a provider/ prefix for the CLI."""
        if "/" in model or model == "default":
            return model
        return f"{self._DEFAULT_PROVIDER}/{model}"

    def build_new_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        provider_options: dict[str, Any],
    ) -> CommandSpec:
        argv = [executable, "run", "--format", "json"]
        if thinking_enabled(provider_options):
            argv.append("--thinking")
        self._apply_model_override(argv, self._resolve_model(model))
        argv.extend(self._extra_args(provider_options))
        argv.append(prompt)
        return CommandSpec(argv=argv)

    def build_resume_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        session_ref: str,
        provider_options: dict[str, Any],
    ) -> CommandSpec:
        argv = [executable, "run", "--format", "json"]
        if thinking_enabled(provider_options):
            argv.append("--thinking")
        argv.extend(["--session", session_ref])
        self._apply_model_override(argv, self._resolve_model(model))
        argv.extend(self._extra_args(provider_options))
        argv.append(prompt)
        return CommandSpec(argv=argv, preset_session_ref=session_ref)

    def model_option_schema(self, model: str) -> list[dict[str, Any]]:
        return boolean_thinking_schema(default="enabled")

    def parse_output_line(self, line: str, state: ParseState) -> list[dict[str, Any]]:
        obj = self._parse_json_or_warn(line, state)
        if obj is None:
            return []

        events: list[dict[str, Any]] = []
        event_type = obj.get("type", "")

        # Session ID is at the top level of every JSON event.
        session_id = obj.get("sessionID") or obj.get("sessionId") or obj.get("session_id")
        if not session_id:
            part = obj.get("part", {})
            if isinstance(part, dict):
                session_id = part.get("sessionID")

        if session_id and state.session_ref != str(session_id):
            state.session_ref = str(session_id)
            events.append({"type": "provider_session", "provider_session_ref": state.session_ref})

        # Text content: type "text" with part.text
        if event_type == "text":
            part = obj.get("part", {})
            if isinstance(part, dict):
                text = part.get("text", "")
                if isinstance(text, str) and text:
                    events.extend(self._append_chunk(state, text))

        # Error handling
        if event_type == "error" or obj.get("error"):
            error_data = obj.get("error") or obj.get("part", {}).get("error")
            state.error_message = str(error_data or obj.get("message", str(obj)))

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
        """OpenCode does not publish a quota or usage API.

        Returns a single ``not_supported`` snapshot so the response shape
        stays uniform with the other instruments, mirroring the
        :class:`AntigravityAdapter` policy.
        """
        return [
            UsageSnapshot(
                provider=InstrumentName.OPENCODE,
                supported=False,
                source="not_supported",
                note="OpenCode does not expose a quota API yet.",
                as_of=now.isoformat(),
            )
        ]
