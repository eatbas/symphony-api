from __future__ import annotations

from .base import CommandSpec, ParseState, ProviderAdapter
from ..models import InstrumentName


class AntigravityAdapter(ProviderAdapter):
    """Google Antigravity (``agy``) — successor to the Gemini CLI.

    Headless invocation mirrors the stream-json contract used by the
    other adapters. Resume and model override are intentionally
    disabled while upstream gaps remain:

    * ``-p`` mode does not yet emit the conversation ID on stdout
      (upstream issue #7), so resume-by-ID is unreliable.
    * There is no ``--model`` flag yet; model selection lives in
      ``~/.gemini/antigravity-cli/settings.json``.

    Both can be enabled here once the CLI exposes them.
    """

    name = InstrumentName.ANTIGRAVITY
    default_executable = "agy"
    session_reference_format = "uuid"
    supports_resume = False
    supports_model_override = False

    def build_new_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        provider_options: dict,
    ) -> CommandSpec:
        argv = [executable, "-p", prompt, "--output-format", "stream-json"]
        argv.extend(self._extra_args(provider_options))
        return CommandSpec(argv=argv)

    def build_resume_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        session_ref: str,
        provider_options: dict,
    ) -> CommandSpec:
        raise NotImplementedError(
            "Antigravity resume is not supported yet — `agy -p` does not emit "
            "the conversation ID. See upstream issue #7."
        )

    def parse_output_line(self, line: str, state: ParseState) -> list[dict[str, object]]:
        obj = self._parse_json_or_warn(line, state)
        if obj is None:
            return []

        events: list[dict[str, object]] = []
        if obj.get("type") == "init" and obj.get("session_id"):
            state.session_ref = str(obj["session_id"])
            events.append({"type": "provider_session", "provider_session_ref": state.session_ref})
        if obj.get("type") == "message" and obj.get("role") == "assistant":
            events.extend(self._append_chunk(state, str(obj.get("content", ""))))
        if obj.get("type") == "result" and obj.get("status") != "success":
            state.error_message = str(obj)
        return events
