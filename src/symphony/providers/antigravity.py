from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .base import CommandSpec, ParseState, ProviderAdapter
from ..models import InstrumentName

# Set to ``"1"`` by the test harness to bypass the PTY wrapper.  The
# pywinpty wrapper can only spawn real Windows executables; ``.sh``
# wrappers used by ``tests/fakes`` must be invoked directly so the
# integration tests keep working.
_NO_PTY_ENV = "SYMPHONY_ANTIGRAVITY_NO_PTY"

logger = logging.getLogger("symphony.providers.antigravity")

# Antigravity reads its active model from this JSON file at start-up.
# The CLI has no ``--model`` flag, so per-call model selection is done
# by mutating this file under a lock before each invocation.
_SETTINGS_PATH = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"

# Slug → in-app display label.  These labels mirror the official Antigravity
# model catalogue documented at antigravity.google/docs/models and the
# in-CLI ``/model`` picker.  When ``agy`` rejects an unknown label it falls
# back to its previous selection silently, so the labels must match exactly.
_MODEL_LABELS: dict[str, str] = {
    "gemini-3.5-flash": "Gemini 3.5 Flash (High)",
    "gemini-3.1-pro-high": "Gemini 3.1 Pro (High)",
    "gemini-3.1-pro-low": "Gemini 3.1 Pro (Low)",
    "gemini-3-flash": "Gemini 3 Flash",
    "claude-sonnet-4.6-thinking": "Claude Sonnet 4.6 (Thinking)",
    "claude-opus-4.6-thinking": "Claude Opus 4.6 (Thinking)",
    "gpt-oss-120b": "GPT-OSS-120B",
}


class AntigravityAdapter(ProviderAdapter):
    """Google Antigravity (``agy``) — successor to the Gemini CLI.

    The CLI does not expose a ``--model`` flag; model selection lives in
    ``~/.gemini/antigravity-cli/settings.json`` and is rewritten by
    :meth:`before_invocation` to the label matching the musician's model
    slug.  An adapter-level :class:`asyncio.Lock` serialises all
    Antigravity musicians so concurrent calls cannot race on the settings
    file.

    ``-p`` / ``--print`` mode does not yet emit a conversation ID on
    stdout, so resume-by-ID is not supported until the upstream gap closes.
    """

    name = InstrumentName.ANTIGRAVITY
    default_executable = "agy"
    session_reference_format = "uuid"
    supports_resume = False
    # The adapter switches models server-side via settings.json, so the
    # capability flag advertises model override support to clients.
    supports_model_override = True

    def __init__(self) -> None:
        # Instance-level lock; the registry holds a single AntigravityAdapter
        # shared by every Antigravity musician, so every concurrent call goes
        # through the same lock.
        self._settings_lock = asyncio.Lock()

    def build_new_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        provider_options: dict,
    ) -> CommandSpec:
        agy_argv = [
            executable,
            "--dangerously-skip-permissions",
            # ``-p`` print mode runs the prompt non-interactively. agy
            # streams its response over the ConPTY allocated by
            # ``agy_pty_runner`` (Windows requires a real PTY for the
            # response to be written at all) and the runner terminates
            # the child via a quiet-timeout watchdog once streaming
            # finishes.
            "-p",
            prompt,
        ]
        agy_argv.extend(self._extra_args(provider_options))
        # ``agy --print`` on Windows refuses to write to non-TTY stdout,
        # so production runs wrap the call in a PTY allocator. Tests opt
        # out via ``SYMPHONY_ANTIGRAVITY_NO_PTY=1`` because pywinpty
        # cannot spawn the ``.sh`` fake-CLI wrappers.
        if os.environ.get(_NO_PTY_ENV) == "1":
            return CommandSpec(argv=agy_argv)
        argv = [
            sys.executable,
            "-u",  # unbuffered stdout so each PTY line arrives promptly.
            "-m",
            "symphony.providers.agy_pty_runner",
            *agy_argv,
        ]
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
        """``agy --print`` writes the assistant response as plain text.

        Each non-empty line is appended to the response chunks; deduping
        is handled by :meth:`ProviderAdapter._append_chunk`.
        """
        return self._append_chunk(state, line)

    async def before_invocation(self, model: str, workspace_path: str) -> None:
        """Acquire the lock and write the resolved label to settings.json.

        Also marks *workspace_path* as a trusted workspace so the TUI
        does not prompt for confirmation when the prompt-interactive
        ConPTY session starts up.

        Holding the lock until :meth:`after_invocation` ensures no other
        Antigravity musician can overwrite the file while ``agy`` is
        reading it.
        """
        await self._settings_lock.acquire()
        try:
            label = _MODEL_LABELS.get(model)
            if label is None:
                # Unknown slug: leave the model untouched so the CLI keeps
                # whatever the user picked interactively. Surface a log
                # message so misconfigurations are visible.
                logger.warning(
                    "antigravity: no display label mapped for model %r; "
                    "settings.json model field left unchanged",
                    model,
                )
            self._write_settings(label, workspace_path)
        except Exception:
            # Release the lock on any failure so the next call is not
            # deadlocked by a half-completed before_invocation.
            self._settings_lock.release()
            raise

    async def after_invocation(self) -> None:
        if self._settings_lock.locked():
            self._settings_lock.release()

    @staticmethod
    def _normalise_workspace(path: str) -> str:
        """Convert ``/c/foo/bar`` to ``C:\\foo\\bar`` on Windows.

        agy compares against the native Windows form when checking
        ``trustedWorkspaces``; an MSYS-style path is not recognised.
        On POSIX the path is returned unchanged.
        """
        if os.name != "nt" or not path:
            return path
        if (
            len(path) >= 3
            and path[0] == "/"
            and path[2] == "/"
            and path[1].isalpha()
        ):
            return f"{path[1].upper()}:\\{path[3:].replace('/', os.sep)}"
        return path

    @classmethod
    def _write_settings(cls, label: str | None, workspace_path: str) -> None:
        """Read-modify-write ``settings.json`` with the new model + trust.

        Updates ``model`` (when *label* is non-``None``) and ensures
        *workspace_path* (converted to native style) appears in
        ``trustedWorkspaces`` so the TUI does not prompt at startup.
        Preserves all other keys (theme, keybindings, etc.) and creates
        the file when missing. Writes atomically via a temp file +
        rename so a crash mid-write cannot corrupt the config.
        """
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        if label is not None:
            existing["model"] = label
        trusted = existing.get("trustedWorkspaces")
        if not isinstance(trusted, list):
            trusted = []
        native_workspace = cls._normalise_workspace(workspace_path)
        if native_workspace and native_workspace not in trusted:
            trusted.append(native_workspace)
        existing["trustedWorkspaces"] = trusted
        tmp_path = _SETTINGS_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        os.replace(tmp_path, _SETTINGS_PATH)
