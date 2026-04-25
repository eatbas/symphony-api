from symphony.models import ChatMode
from symphony.providers.base import ParseState
from symphony.providers.claude import ClaudeAdapter


def test_claude_new_command_assigns_session_id():
    adapter = ClaudeAdapter()
    command = adapter.build_command(
        executable="claude",
        mode=ChatMode.NEW,
        prompt="hello",
        model="opus",
        session_ref=None,
        provider_options={},
    )
    assert "--session-id" in command.argv
    assert "--model" in command.argv
    assert command.preset_session_ref


def test_claude_new_command_omits_model_for_default():
    adapter = ClaudeAdapter()
    command = adapter.build_command(
        executable="claude",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={},
    )
    assert "--model" not in command.argv


def test_claude_resume_command_uses_resume_flag():
    adapter = ClaudeAdapter()
    command = adapter.build_command(
        executable="claude",
        mode=ChatMode.RESUME,
        prompt="hello",
        model="default",
        session_ref="abc-123",
        provider_options={},
    )
    assert "--resume" in command.argv
    assert "abc-123" in command.argv
    assert "--session-id" not in command.argv


def test_claude_resume_command_accepts_max_turns_override():
    adapter = ClaudeAdapter()
    command = adapter.build_command(
        executable="claude",
        mode=ChatMode.RESUME,
        prompt="hello",
        model="default",
        session_ref="abc-123",
        provider_options={"max_turns": 200},
    )
    assert "--max-turns" in command.argv
    max_turns_index = command.argv.index("--max-turns")
    assert command.argv[max_turns_index + 1] == "200"


def test_claude_command_accepts_thinking_level():
    adapter = ClaudeAdapter()
    command = adapter.build_command(
        executable="claude",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={"thinking_level": "high"},
    )
    assert "--effort" in command.argv
    effort_index = command.argv.index("--effort")
    assert command.argv[effort_index + 1] == "high"


def test_claude_model_option_schema_exposes_thinking_level():
    adapter = ClaudeAdapter()
    schema = adapter.model_option_schema("opus")
    assert schema[0]["key"] == "thinking_level"
    assert schema[0]["default"] == "xhigh"
    assert [choice["value"] for choice in schema[0]["choices"]] == ["low", "medium", "high", "xhigh", "max"]


def test_claude_haiku_model_option_schema_omits_thinking_level():
    adapter = ClaudeAdapter()
    assert adapter.model_option_schema("haiku") == []


def test_claude_haiku_ignores_stale_thinking_level():
    adapter = ClaudeAdapter()
    command = adapter.build_command(
        executable="claude",
        mode=ChatMode.NEW,
        prompt="hello",
        model="haiku",
        session_ref=None,
        provider_options={"thinking_level": "high"},
    )
    assert "--effort" not in command.argv


def test_claude_sonnet_model_option_schema_omits_opus_only_efforts():
    adapter = ClaudeAdapter()
    schema = adapter.model_option_schema("sonnet")
    assert schema[0]["default"] == "high"
    assert [choice["value"] for choice in schema[0]["choices"]] == ["low", "medium", "high"]


def test_claude_parse_extracts_session_id():
    adapter = ClaudeAdapter()
    state = ParseState()
    events = adapter.parse_output_line('{"type":"system","subtype":"init","session_id":"sess-456"}', state)
    assert state.session_ref == "sess-456"
    assert any(e["type"] == "provider_session" for e in events)


def test_claude_parse_detects_error_result():
    adapter = ClaudeAdapter()
    state = ParseState()
    adapter.parse_output_line('{"type":"result","subtype":"error","result":"something went wrong"}', state)
    assert state.error_message is not None
