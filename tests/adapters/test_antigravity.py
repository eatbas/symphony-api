import asyncio
import json
import sys
from pathlib import Path

import pytest

from symphony.models import ChatMode
from symphony.providers import antigravity as antigravity_module
from symphony.providers.antigravity import AntigravityAdapter
from symphony.providers.base import ParseState


def test_antigravity_new_command_uses_print_mode_and_skip_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYMPHONY_ANTIGRAVITY_NO_PTY", "1")
    adapter = AntigravityAdapter()
    command = adapter.build_command(
        executable="agy",
        mode=ChatMode.NEW,
        prompt="hello",
        model="gemini-3.5-flash",
        session_ref=None,
        provider_options={},
    )
    assert command.argv[0] == "agy"
    # `-p` is the print-mode short flag; the ConPTY in agy_pty_runner
    # makes agy stream its response back to stdout (otherwise silent
    # on Windows non-TTY).
    assert "-p" in command.argv
    assert "hello" in command.argv
    assert "--dangerously-skip-permissions" in command.argv
    # Legacy --output-format / stream-json flag is rejected by current
    # agy and must not appear.
    assert "--output-format" not in command.argv
    assert "stream-json" not in command.argv


def test_antigravity_new_command_wraps_agy_in_pty_runner_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In production (no opt-out env var) agy is invoked via the PTY wrapper."""
    monkeypatch.delenv("SYMPHONY_ANTIGRAVITY_NO_PTY", raising=False)
    adapter = AntigravityAdapter()
    command = adapter.build_command(
        executable="agy",
        mode=ChatMode.NEW,
        prompt="hello",
        model="gemini-3.5-flash",
        session_ref=None,
        provider_options={},
    )
    assert command.argv[0] == sys.executable
    # `python -m symphony.providers.agy_pty_runner` is the wrapper entry.
    assert "symphony.providers.agy_pty_runner" in command.argv
    # agy and its own flags come after the runner module argument.
    assert "agy" in command.argv
    assert "-p" in command.argv
    assert "hello" in command.argv
    assert "--dangerously-skip-permissions" in command.argv


def test_antigravity_new_command_omits_model_flag_even_when_not_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Antigravity has no --model flag; the adapter must never emit one."""
    # Inspect the bare agy argv (no PTY wrapper) so the python `-m` flag
    # does not collide with the `-m` short-form model flag check.
    monkeypatch.setenv("SYMPHONY_ANTIGRAVITY_NO_PTY", "1")
    adapter = AntigravityAdapter()
    command = adapter.build_command(
        executable="agy",
        mode=ChatMode.NEW,
        prompt="hello",
        model="gemini-3.1-pro-low",
        session_ref=None,
        provider_options={},
    )
    assert "--model" not in command.argv
    assert "-m" not in command.argv
    # The model slug must not leak into argv either; it is applied
    # server-side via settings.json instead.
    assert "gemini-3.1-pro-low" not in command.argv


def test_antigravity_supports_model_override_via_settings():
    """Model switching is real now: implemented via settings.json mutation."""
    adapter = AntigravityAdapter()
    assert adapter.supports_resume is False
    assert adapter.supports_model_override is True


def test_antigravity_resume_raises_not_implemented():
    adapter = AntigravityAdapter()
    with pytest.raises(NotImplementedError):
        adapter.build_resume_command(
            executable="agy",
            prompt="hello",
            model="gemini-3.5-flash",
            session_ref="some-uuid",
            provider_options={},
        )


def test_antigravity_parse_treats_each_line_as_response_text():
    adapter = AntigravityAdapter()
    state = ParseState()
    events_a = adapter.parse_output_line("hello world", state)
    events_b = adapter.parse_output_line("second line", state)

    assert any(e["type"] == "output_delta" for e in events_a)
    assert any(e["type"] == "output_delta" for e in events_b)
    assert state.output_chunks == ["hello world", "second line"]


def test_antigravity_parse_skips_blank_lines():
    adapter = AntigravityAdapter()
    state = ParseState()
    assert adapter.parse_output_line("   ", state) == []
    assert state.output_chunks == []


def test_antigravity_parse_does_not_extract_session_ref():
    """`agy -p` does not emit a conversation ID; parser must not fabricate one."""
    adapter = AntigravityAdapter()
    state = ParseState()
    adapter.parse_output_line("a response", state)
    assert state.session_ref is None


def test_antigravity_extra_args_appended_to_command():
    adapter = AntigravityAdapter()
    command = adapter.build_command(
        executable="agy",
        mode=ChatMode.NEW,
        prompt="hello",
        model="gemini-3.5-flash",
        session_ref=None,
        provider_options={"extra_args": ["--verbose"]},
    )
    assert "--verbose" in command.argv


def test_antigravity_model_option_schema_is_empty():
    adapter = AntigravityAdapter()
    assert adapter.model_option_schema("gemini-3.5-flash") == []


def test_antigravity_extra_args_rejects_non_list():
    adapter = AntigravityAdapter()
    with pytest.raises(ValueError, match="extra_args must be a list"):
        adapter.build_command(
            executable="agy",
            mode=ChatMode.NEW,
            prompt="hello",
            model="gemini-3.5-flash",
            session_ref=None,
            provider_options={"extra_args": "--bad"},
        )


# ---------------------------------------------------------------------------
# before_invocation / after_invocation lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_invocation_writes_resolved_label_to_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"model": "Gemini 3.5 Flash (High)", "trustedWorkspaces": ["x"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_file)

    adapter = AntigravityAdapter()
    await adapter.before_invocation("claude-opus-4.6-thinking", "C:/proj")
    try:
        contents = json.loads(settings_file.read_text(encoding="utf-8"))
        assert contents["model"] == "Claude Opus 4.6 (Thinking)"
        # Pre-existing trusted workspaces preserved; new one appended.
        assert "x" in contents["trustedWorkspaces"]
        assert "C:/proj" in contents["trustedWorkspaces"]
    finally:
        await adapter.after_invocation()


@pytest.mark.asyncio
async def test_before_invocation_does_not_duplicate_trusted_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"trustedWorkspaces": ["C:/proj"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_file)

    adapter = AntigravityAdapter()
    await adapter.before_invocation("gemini-3.5-flash", "C:/proj")
    try:
        contents = json.loads(settings_file.read_text(encoding="utf-8"))
        assert contents["trustedWorkspaces"].count("C:/proj") == 1
    finally:
        await adapter.after_invocation()


@pytest.mark.asyncio
async def test_before_invocation_unknown_slug_still_marks_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown slug keeps the existing model but still records workspace trust."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"model": "Gemini 3.5 Flash (High)"}), encoding="utf-8"
    )
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_file)

    adapter = AntigravityAdapter()
    await adapter.before_invocation("totally-unknown-slug", "C:/proj")
    try:
        contents = json.loads(settings_file.read_text(encoding="utf-8"))
        assert contents["model"] == "Gemini 3.5 Flash (High)"
        assert "C:/proj" in contents["trustedWorkspaces"]
    finally:
        await adapter.after_invocation()


@pytest.mark.asyncio
async def test_before_invocation_creates_settings_file_if_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_file = tmp_path / "nested" / "settings.json"
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_file)

    adapter = AntigravityAdapter()
    await adapter.before_invocation("gpt-oss-120b", "C:/work")
    try:
        contents = json.loads(settings_file.read_text(encoding="utf-8"))
        assert contents["model"] == "GPT-OSS-120B"
        assert contents["trustedWorkspaces"] == ["C:/work"]
    finally:
        await adapter.after_invocation()


@pytest.mark.asyncio
async def test_lock_serialises_concurrent_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two simultaneous before/after pairs must not interleave their writes."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_file)

    adapter = AntigravityAdapter()
    observed: list[str] = []

    async def call(model: str) -> None:
        await adapter.before_invocation(model, "C:/proj")
        observed.append(
            json.loads(settings_file.read_text(encoding="utf-8"))["model"]
        )
        # Yield control so the other task can attempt to grab the lock.
        await asyncio.sleep(0)
        await adapter.after_invocation()

    await asyncio.gather(
        call("gemini-3.5-flash"),
        call("claude-sonnet-4.6-thinking"),
    )

    # Both calls completed and each saw its own label, never the other's.
    assert "Gemini 3.5 Flash (High)" in observed
    assert "Claude Sonnet 4.6 (Thinking)" in observed
    # Lock is released at the end.
    assert not adapter._settings_lock.locked()


@pytest.mark.asyncio
async def test_after_invocation_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling after_invocation without a held lock must not raise."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_file)

    adapter = AntigravityAdapter()
    # No before_invocation first; lock is not held.
    await adapter.after_invocation()
    assert not adapter._settings_lock.locked()


@pytest.mark.asyncio
async def test_before_invocation_releases_lock_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the settings write fails, the lock must be released so subsequent calls do not deadlock."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_file)

    adapter = AntigravityAdapter()

    def boom(label, workspace):  # noqa: ANN001 - matched _write_settings shape
        raise OSError("disk full")

    monkeypatch.setattr(adapter, "_write_settings", boom)
    with pytest.raises(OSError):
        await adapter.before_invocation("gemini-3.5-flash", "C:/proj")
    assert not adapter._settings_lock.locked()


def test_normalise_workspace_returns_empty_path_unchanged() -> None:
    """Covers the ``not path`` short-circuit in ``_normalise_workspace``."""
    assert AntigravityAdapter._normalise_workspace("") == ""


def test_normalise_workspace_converts_msys_to_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the MSYS-to-Windows conversion branch."""
    monkeypatch.setattr(antigravity_module.os, "name", "nt")
    monkeypatch.setattr(antigravity_module.os, "sep", "\\")
    assert AntigravityAdapter._normalise_workspace("/c/Users/eren") == "C:\\Users\\eren"


def test_normalise_workspace_passes_through_non_matching_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native Windows path lacking the MSYS prefix is returned verbatim."""
    monkeypatch.setattr(antigravity_module.os, "name", "nt")
    assert AntigravityAdapter._normalise_workspace("C:\\Users\\eren") == "C:\\Users\\eren"


def test_write_settings_resets_non_dict_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If settings.json exists but contains a JSON array, the writer must
    discard it and start from a fresh dict."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("[]", encoding="utf-8")  # JSON, but not a dict
    monkeypatch.setattr(antigravity_module, "_SETTINGS_PATH", settings_path)

    AntigravityAdapter._write_settings("Some Label", "C:\\workspace")

    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert isinstance(written, dict)
    assert written.get("model") == "Some Label"
    assert "C:\\workspace" in written.get("trustedWorkspaces", [])
