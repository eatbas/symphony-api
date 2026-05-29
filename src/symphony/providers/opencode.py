from __future__ import annotations

from typing import Any

from .base import CommandSpec, ParseState, ProviderAdapter
from .options import boolean_thinking_schema, thinking_enabled
from ..models import InstrumentName


class OpenCodeAdapter(ProviderAdapter):
    """Adapter for the OpenCode CLI (opencode-ai)."""

    name = InstrumentName.OPENCODE
    default_executable = "opencode"
    session_reference_format = "opaque-string"

    @staticmethod
    def _require_subprovider_prefix(model: str) -> None:
        """Reject model identifiers that lack an explicit sub-provider.

        OpenCode routes to multiple upstream providers (OpenRouter,
        Anthropic, OpenAI, …) and requires the model identifier to carry
        a ``<sub-provider>/<model>`` prefix.  Symphony refuses unprefixed
        identifiers up-front so a misconfigured musician fails fast
        rather than letting the CLI return a cryptic upstream error.
        ``"default"`` is the lone exception; it tells the CLI to use the
        provider's own default model.
        """
        if model == "default" or "/" in model:
            return
        raise ValueError(
            "OpenCode model identifiers must include a sub-provider "
            "prefix, e.g. 'openrouter/qwen/qwen3-coder:free'."
        )

    def build_new_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        provider_options: dict[str, Any],
    ) -> CommandSpec:
        self._require_subprovider_prefix(model)
        argv = [executable, "run", "--format", "json"]
        if thinking_enabled(provider_options):
            argv.append("--thinking")
        self._apply_model_override(argv, model)
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
        self._require_subprovider_prefix(model)
        argv = [executable, "run", "--format", "json"]
        if thinking_enabled(provider_options):
            argv.append("--thinking")
        argv.extend(["--session", session_ref])
        self._apply_model_override(argv, model)
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
