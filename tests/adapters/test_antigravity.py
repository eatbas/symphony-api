import pytest

from symphony.models import ChatMode
from symphony.providers.antigravity import AntigravityAdapter
from symphony.providers.base import ParseState


def test_antigravity_new_command_uses_print_and_stream_json():
    adapter = AntigravityAdapter()
    command = adapter.build_command(
        executable="agy",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={},
    )
    assert command.argv[0] == "agy"
    assert "-p" in command.argv
    assert "hello" in command.argv
    assert "--output-format" in command.argv
    assert "stream-json" in command.argv


def test_antigravity_new_command_omits_model_flag_even_when_not_default():
    """Antigravity has no --model flag yet; the adapter must never emit one."""
    adapter = AntigravityAdapter()
    command = adapter.build_command(
        executable="agy",
        mode=ChatMode.NEW,
        prompt="hello",
        model="gemini-3-pro",
        session_ref=None,
        provider_options={},
    )
    assert "--model" not in command.argv
    assert "-m" not in command.argv
    assert "gemini-3-pro" not in command.argv


def test_antigravity_disables_resume_and_model_override():
    adapter = AntigravityAdapter()
    assert adapter.supports_resume is False
    assert adapter.supports_model_override is False


def test_antigravity_resume_raises_not_implemented():
    adapter = AntigravityAdapter()
    with pytest.raises(NotImplementedError):
        adapter.build_resume_command(
            executable="agy",
            prompt="hello",
            model="default",
            session_ref="some-uuid",
            provider_options={},
        )


def test_antigravity_parse_extracts_session_id():
    adapter = AntigravityAdapter()
    state = ParseState()
    events = adapter.parse_output_line(
        '{"type":"init","session_id":"uuid-123","model":"gemini-3-flash"}',
        state,
    )
    assert state.session_ref == "uuid-123"
    assert any(e["type"] == "provider_session" for e in events)


def test_antigravity_parse_emits_assistant_message_as_output_delta():
    adapter = AntigravityAdapter()
    state = ParseState()
    events = adapter.parse_output_line(
        '{"type":"message","role":"assistant","content":"hello world"}',
        state,
    )
    assert any(e["type"] == "output_delta" for e in events)
    assert "hello world" in state.output_chunks


def test_antigravity_parse_detects_error_result():
    adapter = AntigravityAdapter()
    state = ParseState()
    adapter.parse_output_line('{"type":"result","status":"error"}', state)
    assert state.error_message is not None


def test_antigravity_non_json_line_added_to_warnings():
    adapter = AntigravityAdapter()
    state = ParseState()
    events = adapter.parse_output_line("this is not json", state)
    assert len(state.warnings) == 1
    assert events == []


def test_antigravity_extra_args_appended_to_command():
    adapter = AntigravityAdapter()
    command = adapter.build_command(
        executable="agy",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={"extra_args": ["--verbose"]},
    )
    assert "--verbose" in command.argv


def test_antigravity_model_option_schema_is_empty():
    adapter = AntigravityAdapter()
    assert adapter.model_option_schema("gemini-3-flash") == []


def test_antigravity_extra_args_rejects_non_list():
    adapter = AntigravityAdapter()
    with pytest.raises(ValueError, match="extra_args must be a list"):
        adapter.build_command(
            executable="agy",
            mode=ChatMode.NEW,
            prompt="hello",
            model="default",
            session_ref=None,
            provider_options={"extra_args": "--bad"},
        )
