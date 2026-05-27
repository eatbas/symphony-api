from symphony.models import ChatMode
from symphony.providers.base import ParseState
from symphony.providers.opencode import OpenCodeAdapter


def test_opencode_new_command_includes_format_json():
    adapter = OpenCodeAdapter()
    command = adapter.build_command(
        executable="opencode",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={},
    )
    assert "run" in command.argv
    assert "--format" in command.argv
    assert "json" in command.argv
    assert "--model" not in command.argv


def test_opencode_new_command_includes_model_with_provider_prefix():
    adapter = OpenCodeAdapter()
    command = adapter.build_command(
        executable="opencode",
        mode=ChatMode.NEW,
        prompt="hello",
        model="glm-5",
        session_ref=None,
        provider_options={},
    )
    assert "--model" in command.argv
    assert "zai-coding-plan/glm-5" in command.argv


def test_opencode_new_command_preserves_existing_provider_prefix():
    adapter = OpenCodeAdapter()
    command = adapter.build_command(
        executable="opencode",
        mode=ChatMode.NEW,
        prompt="hello",
        model="custom-provider/glm-5",
        session_ref=None,
        provider_options={},
    )
    assert "custom-provider/glm-5" in command.argv
    assert "zai-coding-plan/custom-provider/glm-5" not in command.argv


def test_opencode_resume_command_uses_session_flag():
    adapter = OpenCodeAdapter()
    command = adapter.build_command(
        executable="opencode",
        mode=ChatMode.RESUME,
        prompt="hello",
        model="glm-5",
        session_ref="ses-abc-123",
        provider_options={},
    )
    assert "--session" in command.argv
    assert "ses-abc-123" in command.argv
    assert command.preset_session_ref == "ses-abc-123"


def test_opencode_extra_args_appended():
    adapter = OpenCodeAdapter()
    command = adapter.build_command(
        executable="opencode",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={"extra_args": ["--verbose"]},
    )
    assert "--verbose" in command.argv


def test_opencode_thinking_mode_can_be_disabled():
    adapter = OpenCodeAdapter()
    command = adapter.build_command(
        executable="opencode",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={"thinking_mode": "disabled"},
    )
    assert "--thinking" not in command.argv


def test_opencode_model_option_schema_exposes_thinking_mode():
    adapter = OpenCodeAdapter()
    schema = adapter.model_option_schema("glm-5")
    assert schema[0]["key"] == "thinking_mode"
    assert schema[0]["default"] == "enabled"


def test_opencode_parse_extracts_session_id():
    adapter = OpenCodeAdapter()
    state = ParseState()
    events = adapter.parse_output_line('{"type":"init","sessionID":"ses-xyz-789"}', state)
    assert state.session_ref == "ses-xyz-789"
    assert any(e["type"] == "provider_session" for e in events)


def test_opencode_parse_extracts_session_from_part():
    adapter = OpenCodeAdapter()
    state = ParseState()
    events = adapter.parse_output_line('{"type":"text","part":{"sessionID":"ses-part-1","text":"hello"}}', state)
    assert state.session_ref == "ses-part-1"
    assert any(e["type"] == "provider_session" for e in events)


def test_opencode_parse_extracts_text_content():
    adapter = OpenCodeAdapter()
    state = ParseState()
    events = adapter.parse_output_line('{"type":"text","sessionID":"s1","part":{"text":"hello world"}}', state)
    assert any(e["type"] == "output_delta" for e in events)
    assert "hello world" in state.output_chunks


def test_opencode_parse_detects_error():
    adapter = OpenCodeAdapter()
    state = ParseState()
    adapter.parse_output_line('{"type":"error","error":"something went wrong"}', state)
    assert state.error_message is not None
    assert "something went wrong" in state.error_message


def test_opencode_parse_detects_error_in_obj():
    adapter = OpenCodeAdapter()
    state = ParseState()
    adapter.parse_output_line('{"type":"status","error":"connection lost"}', state)
    assert state.error_message is not None


def test_opencode_non_json_added_to_warnings():
    adapter = OpenCodeAdapter()
    state = ParseState()
    events = adapter.parse_output_line("not json at all", state)
    assert len(state.warnings) == 1
    assert events == []


def test_opencode_parse_ignores_empty_text():
    adapter = OpenCodeAdapter()
    state = ParseState()
    events = adapter.parse_output_line('{"type":"text","part":{"text":""}}', state)
    assert not any(e["type"] == "output_delta" for e in events)
