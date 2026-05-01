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


def test_claude_parse_flags_socket_disconnect_as_fatal():
    """Regression: when the claude API drops the socket mid-stream the
    CLI emits the error inside an assistant text chunk and then exits
    with code 1 -- but the user only saw "claude exited with code 1"
    and no idea why. The adapter must capture the actual API error so
    the score's failure reason is meaningful.
    """
    adapter = ClaudeAdapter()
    state = ParseState()
    line = (
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"API Error: The socket connection was closed unexpectedly. '
        'For more information, pass `verbose: true` in the second argument to fetch()"}]}}'
    )
    adapter.parse_output_line(line, state)
    assert state.error_message is not None
    assert "socket connection was closed" in state.error_message


def test_claude_parse_flags_unable_to_connect_as_fatal():
    """Regression for a real production run: claude printed
    "API Error: Unable to connect. Is the computer able to access the
    url?" then exited with code 1. The pattern list previously only
    covered the "socket connection was closed" variant, so the user
    saw the meaningless "claude exited with code 1" and had to dig
    through score JSON to find the real cause.
    """
    adapter = ClaudeAdapter()
    state = ParseState()
    line = (
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"API Error: Unable to connect. Is the computer able to access the url?"}]}}'
    )
    adapter.parse_output_line(line, state)
    assert state.error_message is not None
    assert "Unable to connect" in state.error_message


def test_claude_parse_flags_rate_limit_as_fatal():
    adapter = ClaudeAdapter()
    state = ParseState()
    line = (
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"API Error: 429 Too Many Requests"}]}}'
    )
    adapter.parse_output_line(line, state)
    assert state.error_message is not None
    assert "429" in state.error_message


def test_claude_parse_flags_server_error_as_fatal():
    adapter = ClaudeAdapter()
    state = ParseState()
    line = (
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"API Error: 503 Service Unavailable"}]}}'
    )
    adapter.parse_output_line(line, state)
    assert state.error_message is not None
    assert "503" in state.error_message
