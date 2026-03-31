from __future__ import annotations

import shlex

from .base import CommandSpec, ParseState, ProviderAdapter
from ..models import InstrumentName
from ..shells import to_bash_path


class KimiAdapter(ProviderAdapter):
    name = InstrumentName.KIMI
    default_executable = "kimi"
    session_reference_format = "opaque-string"

    def new_session_ref(self) -> str | None:
        return self._uuid()

    def build_new_command(self, *, executable: str, prompt: str, model: str, provider_options: dict) -> CommandSpec:
        session_ref = self.new_session_ref()
        argv = [
            executable,
            "--yolo",
            "--thinking",
            "--session",
            session_ref,
            "--print",
            "--prompt",
            prompt,
            "--output-format",
            "stream-json",
        ]
        self._apply_model_override(argv, model)
        argv.extend(self._extra_args(provider_options))
        return CommandSpec(argv=argv, preset_session_ref=session_ref)

    def build_resume_command(self, *, executable: str, prompt: str, model: str, session_ref: str, provider_options: dict) -> CommandSpec:
        argv = [
            executable,
            "--yolo",
            "--thinking",
            "--session",
            session_ref,
            "--print",
            "--prompt",
            prompt,
            "--output-format",
            "stream-json",
        ]
        self._apply_model_override(argv, model)
        argv.extend(self._extra_args(provider_options))
        return CommandSpec(argv=argv, preset_session_ref=session_ref)

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

    def parse_output_line(self, line: str, state: ParseState) -> list[dict[str, object]]:
        obj = self._parse_json(line)
        if obj is None:
            # Non-JSON lines (plain text progress, thinking, etc.) — emit as-is.
            stripped = line.strip()
            if stripped:
                return self._append_chunk(state, stripped)
            return []

        events: list[dict[str, object]] = []
        for item in obj.get("content", []):
            item_type = item.get("type", "")
            if item_type == "text":
                events.extend(self._append_chunk(state, str(item.get("text", ""))))
            elif item_type == "tool_use":
                name = item.get("name", "unknown")
                tool_input = item.get("input", {})
                summary = self._summarise_tool_call(name, tool_input)
                events.extend(self._append_chunk(state, summary))
            elif item_type == "tool_result":
                output = str(item.get("output", item.get("content", "")))
                if output.strip():
                    # Truncate long tool results to keep the stream readable.
                    display = output.strip()[:300]
                    events.extend(self._append_chunk(state, display))
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
