from symphony.models import ChatMode
from symphony.providers.base import ParseState
from symphony.providers.codex import CodexAdapter


def test_codex_new_command_bypasses_approvals_and_sandbox():
    """The deprecated --full-auto is replaced by the explicit bypass flag."""
    adapter = CodexAdapter()
    command = adapter.build_command(
        executable="codex",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={},
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in command.argv
    assert "--full-auto" not in command.argv
    assert "--json" in command.argv


def test_codex_new_command_includes_model_when_not_default():
    adapter = CodexAdapter()
    command = adapter.build_command(
        executable="codex",
        mode=ChatMode.NEW,
        prompt="hello",
        model="o3",
        session_ref=None,
        provider_options={},
    )
    assert "-m" in command.argv
    assert "o3" in command.argv


def test_codex_new_command_includes_thinking_level():
    adapter = CodexAdapter()
    command = adapter.build_command(
        executable="codex",
        mode=ChatMode.NEW,
        prompt="hello",
        model="gpt-5.5",
        session_ref=None,
        provider_options={"thinking_level": "high"},
    )
    assert "-c" in command.argv
    assert 'model_reasoning_effort="high"' in command.argv


def test_codex_resume_command_uses_json_and_bypass_flag():
    adapter = CodexAdapter()
    command = adapter.build_command(
        executable="codex",
        mode=ChatMode.RESUME,
        prompt="hello",
        model="default",
        session_ref="thread-1",
        provider_options={},
    )
    assert command.argv[:3] == ["codex", "exec", "resume"]
    assert "--json" in command.argv
    assert "--dangerously-bypass-approvals-and-sandbox" in command.argv
    assert "--full-auto" not in command.argv


def test_codex_parse_extracts_thread_id():
    adapter = CodexAdapter()
    state = ParseState()
    events = adapter.parse_output_line('{"type":"thread.started","thread_id":"t-789"}', state)
    assert state.session_ref == "t-789"
    assert any(e["type"] == "provider_session" for e in events)


def test_codex_parse_captures_error_item_as_warning():
    adapter = CodexAdapter()
    state = ParseState()
    adapter.parse_output_line('{"type":"item.completed","item":{"type":"error","message":"oops"}}', state)
    assert len(state.warnings) == 1
    assert "oops" in state.warnings[0]
