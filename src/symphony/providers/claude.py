from __future__ import annotations

from .base import CommandSpec, ParseState, ProviderAdapter
from ..models import InstrumentName


class ClaudeAdapter(ProviderAdapter):
    name = InstrumentName.CLAUDE
    default_executable = "claude"
    session_reference_format = "uuid"

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
            "--enable-auto-mode",
            "--no-chrome",
            "--session-id",
            session_ref,
            prompt,
        ]
        self._apply_model_override(argv, model)
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
            "--enable-auto-mode",
            "--no-chrome",
            "--effort",
            "high",
            "--resume",
            session_ref,
            prompt,
        ]
        self._apply_model_override(argv, model)
        self._apply_max_turns(argv, provider_options)
        argv.extend(self._extra_args(provider_options))
        return CommandSpec(argv=argv, preset_session_ref=session_ref)

    def _apply_max_turns(self, argv: list[str], provider_options: dict) -> None:
        raw = provider_options.get("max_turns")
        if raw is None:
            return
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise ValueError("provider_options.max_turns must be a positive integer")
        argv.extend(["--max-turns", str(raw)])

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
                    events.extend(self._append_chunk(state, str(item.get("text", ""))))

        if obj.get("type") == "result" and obj.get("subtype") != "success":
            errors = obj.get("errors") or [obj.get("result") or "Claude command failed"]
            state.error_message = "; ".join(str(item) for item in errors if item)
        return events
