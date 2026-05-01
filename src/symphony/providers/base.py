from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import ChatMode, InstrumentName
from ..shells import to_bash_path, windows_subprocess_kwargs


_VERSION_PATTERN = re.compile(r"\d+\.\d+")

# Will be set by Orchestra during startup so the smoke test runs through
# the same Git Bash that musicians use.
_bash_path: str | None = None


def set_bash_path(path: str) -> None:
    """Called once at startup to configure the bash used for CLI smoke tests."""
    global _bash_path  # noqa: PLW0603
    _bash_path = path


def check_cli_available(executable: str) -> bool:
    """Return True if *executable* can actually be invoked **from Git Bash**.

    * **Explicit paths** (containing a separator) — only checked for file
      existence; they are invoked through a bash musician so a direct smoke-test
      would fail for ``.sh`` wrappers on Windows.
    * **Bare command names** — smoke-tested via Git Bash with
      ``command -v <exe> && <exe> --version``.  This ensures we see exactly
      what the musicians will see — no Windows ``.cmd`` stubs that bash can't
      find.
    """
    # Explicit path → just check the file exists.
    if os.sep in executable or "/" in executable:
        return Path(executable).is_file()

    bash = _bash_path
    if bash:
        # Run through Git Bash -- same environment as musicians.
        return _check_via_bash(bash, executable)

    # Fallback (non-Windows or bash not yet configured).
    return shutil.which(executable) is not None


def _check_via_bash(bash: str, executable: str) -> bool:
    """Run ``<exe> --version`` inside Git Bash and verify the output."""
    script = f'command -v {shlex.quote(executable)} >/dev/null 2>&1 && {shlex.quote(executable)} --version 2>&1'
    try:
        result = subprocess.run(
            [bash, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            **windows_subprocess_kwargs(),
        )
        if result.returncode != 0:
            return False
        output = result.stdout.decode("utf-8", errors="replace")
        return bool(_VERSION_PATTERN.search(output))
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


@dataclass(slots=True)
class CommandSpec:
    argv: list[str]
    preset_session_ref: str | None = None


@dataclass(slots=True)
class ParseState:
    session_ref: str | None = None
    warnings: list[str] = field(default_factory=list)
    output_chunks: list[str] = field(default_factory=list)
    last_emitted_chunk: str | None = None
    error_message: str | None = None


class ProviderAdapter:
    name: InstrumentName
    supports_resume = True
    supports_model_override = True
    session_reference_format = "provider-native"
    default_executable: str

    def resolve_executable(self, override: str | None) -> str:
        return override or self.default_executable

    def is_available(self, override: str | None = None) -> bool:
        """Check whether the resolved CLI executable is findable on PATH."""
        return check_cli_available(self.resolve_executable(override))

    def build_command(
        self,
        *,
        executable: str,
        mode: ChatMode,
        prompt: str,
        model: str,
        session_ref: str | None,
        provider_options: dict[str, Any],
    ) -> CommandSpec:
        if mode is ChatMode.NEW:
            return self.build_new_command(
                executable=executable,
                prompt=prompt,
                model=model,
                provider_options=provider_options,
            )
        if session_ref is None:
            raise ValueError("session_ref required for resume mode")
        return self.build_resume_command(
            executable=executable,
            prompt=prompt,
            model=model,
            session_ref=session_ref,
            provider_options=provider_options,
        )

    def build_new_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        provider_options: dict[str, Any],
    ) -> CommandSpec:
        raise NotImplementedError

    def build_resume_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        session_ref: str,
        provider_options: dict[str, Any],
    ) -> CommandSpec:
        raise NotImplementedError

    def initial_parse_state(self, preset_session_ref: str | None = None) -> ParseState:
        return ParseState(session_ref=preset_session_ref)

    def parse_output_line(self, line: str, state: ParseState) -> list[dict[str, Any]]:
        raise NotImplementedError

    def model_option_schema(self, model: str) -> list[dict[str, Any]]:
        return []

    def make_shell_script(self, workspace_path: str, command: CommandSpec) -> str:
        workspace = shlex.quote(to_bash_path(workspace_path))
        shell_command = shlex.join(self._normalize_argv(command.argv))
        return (
            f"if ! cd -- {workspace}; then\n"
            f"  echo 'Failed to enter workspace: {workspace_path}'\n"
            f"  __symphony_exit=97\n"
            f"else\n"
            f"  {shell_command} < /dev/null\n"
            f"  __symphony_exit=$?\n"
            f"fi"
        )

    def _normalize_argv(self, argv: list[str]) -> list[str]:
        normalized: list[str] = []
        for arg in argv:
            if len(arg) >= 3 and arg[1:3] == ":\\":
                normalized.append(to_bash_path(arg))
            else:
                normalized.append(arg)
        return normalized

    def _extra_args(self, provider_options: dict[str, Any]) -> list[str]:
        raw = provider_options.get("extra_args", [])
        if raw is None:
            return []
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ValueError("provider_options.extra_args must be a list of strings")
        return raw

    def _apply_model_override(self, argv: list[str], model: str, *, flag: str = "--model") -> None:
        if model != "default":
            argv.extend([flag, model])

    def _parse_json_or_warn(self, line: str, state: ParseState) -> dict[str, Any] | None:
        obj = self._parse_json(line)
        if obj is None:
            state.warnings.append(line)
            return None
        return obj

    def _append_chunk(self, state: ParseState, chunk: str) -> list[dict[str, Any]]:
        text = chunk.strip()
        if not text:
            return []
        if state.last_emitted_chunk == text:
            return []
        state.last_emitted_chunk = text
        state.output_chunks.append(text)
        return [{"type": "output_delta", "text": text}]

    def _parse_json(self, line: str) -> dict[str, Any] | None:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _detect_fatal_error(
        self,
        text: str,
        state: ParseState,
        patterns: tuple[str, ...],
    ) -> None:
        """Mark ``state`` as failed when the CLI prints a known fatal message.

        Some CLIs print a clear error indicator -- e.g. an LLM provider
        connection drop -- and then sit idle without exiting. The
        executor watches ``state.error_message`` and, when an adapter
        sets it from inside :meth:`parse_output_line`, interrupts the
        shell so the score is finalised promptly with the captured
        message instead of hanging in "running" forever.

        We only set ``error_message`` once per run (first match wins)
        so subsequent output cannot accidentally clear or overwrite the
        original failure cause.
        """
        if state.error_message is not None:
            return
        for pattern in patterns:
            if pattern and pattern in text:
                state.error_message = text.strip()
                return

    def new_session_ref(self) -> str | None:
        return None

    def _uuid(self) -> str:
        return str(uuid.uuid4())
