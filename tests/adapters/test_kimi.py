from symphony.models import ChatMode
from symphony.providers.base import ParseState
from symphony.providers.kimi import KimiAdapter


def test_kimi_new_command_assigns_session():
    adapter = KimiAdapter()
    command = adapter.build_command(
        executable="kimi",
        mode=ChatMode.NEW,
        prompt="hello",
        model="default",
        session_ref=None,
        provider_options={},
    )
    assert "--session" in command.argv
    assert "--print" in command.argv
    assert "--output-format" in command.argv
    assert command.preset_session_ref


def test_kimi_new_command_includes_model_when_not_default():
    adapter = KimiAdapter()
    command = adapter.build_command(
        executable="kimi",
        mode=ChatMode.NEW,
        prompt="hello",
        model="k2",
        session_ref=None,
        provider_options={},
    )
    assert "--model" in command.argv
    assert "k2" in command.argv


def test_kimi_resume_command_uses_session_flag():
    adapter = KimiAdapter()
    command = adapter.build_command(
        executable="kimi",
        mode=ChatMode.RESUME,
        prompt="hello",
        model="default",
        session_ref="kimi-sess-1",
        provider_options={},
    )
    assert "--session" in command.argv
    assert "kimi-sess-1" in command.argv


def test_kimi_parse_emits_output_delta():
    adapter = KimiAdapter()
    state = ParseState()
    events = adapter.parse_output_line('{"role":"assistant","content":[{"type":"text","text":"hello world"}]}', state)
    assert any(e["type"] == "output_delta" for e in events)
    assert "hello world" in state.output_chunks


def test_kimi_parse_emits_tool_use():
    adapter = KimiAdapter()
    state = ParseState()
    line = '{"content":[{"type":"tool_use","name":"write_file","input":{"path":"src/i18n.ts"}}]}'
    events = adapter.parse_output_line(line, state)
    assert any(e["type"] == "output_delta" for e in events)
    assert any("write_file" in chunk for chunk in state.output_chunks)
    assert any("src/i18n.ts" in chunk for chunk in state.output_chunks)


def test_kimi_parse_emits_tool_result():
    adapter = KimiAdapter()
    state = ParseState()
    line = '{"content":[{"type":"tool_result","output":"File written successfully"}]}'
    events = adapter.parse_output_line(line, state)
    assert any(e["type"] == "output_delta" for e in events)
    assert "File written successfully" in state.output_chunks


def test_kimi_parse_emits_plain_text():
    adapter = KimiAdapter()
    state = ParseState()
    events = adapter.parse_output_line("Thinking about the implementation...", state)
    assert any(e["type"] == "output_delta" for e in events)
    assert "Thinking about the implementation..." in state.output_chunks


def test_kimi_parse_skips_empty_lines():
    adapter = KimiAdapter()
    state = ParseState()
    events = adapter.parse_output_line("   ", state)
    assert events == []
