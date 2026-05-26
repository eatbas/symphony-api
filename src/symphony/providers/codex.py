from __future__ import annotations

from typing import Any

from .base import CommandSpec, ParseState, ProviderAdapter
from .codex_options import SUPPORTED_REASONING_EFFORTS, codex_model_options
from .options import get_thinking_level
from ..models import InstrumentName


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

    def _resolve_model(self, model: str) -> str:
        return self.model_aliases.get(model, model)

    def model_option_schema(self, model: str) -> list[dict[str, Any]]:
        return codex_model_options(self._resolve_model(model))

    def _apply_reasoning_effort(self, argv: list[str], provider_options: dict) -> None:
        raw = get_thinking_level(provider_options, allowed=tuple(sorted(SUPPORTED_REASONING_EFFORTS)))
        if raw is None:
            return
        argv.extend(["-c", f'model_reasoning_effort="{raw}"'])
