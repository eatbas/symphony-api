"""Tests for providers/kimi.py branch coverage."""
from __future__ import annotations

import json

from symphony.providers.base import ParseState
from symphony.providers.kimi import KimiAdapter


def _parse(line: str, state: ParseState | None = None) -> tuple[list, ParseState]:
    adapter = KimiAdapter()
    st = state or ParseState()
    events = adapter.parse_output_line(line, st)
    return events, st


class TestKimiToolCallSummary:
    def test_tool_call_with_path(self) -> None:
        payload = json.dumps(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "read_file",
                        "input": {"path": "/tmp/foo.txt"},
                    }
                ]
            }
        )
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "⚙ read_file: /tmp/foo.txt"}]

    def test_tool_call_with_file_path_alias(self) -> None:
        payload = json.dumps(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "edit",
                        "input": {"file_path": "/tmp/bar.txt"},
                    }
                ]
            }
        )
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "⚙ edit: /tmp/bar.txt"}]

    def test_tool_call_with_short_command(self) -> None:
        payload = json.dumps(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "bash",
                        "input": {"command": "ls -la"},
                    }
                ]
            }
        )
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "⚙ bash: ls -la"}]

    def test_tool_call_truncates_long_command(self) -> None:
        long_command = "echo " + "x" * 200
        payload = json.dumps(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "bash",
                        "input": {"command": long_command},
                    }
                ]
            }
        )
        events, _ = _parse(payload)
        text = events[0]["text"]
        assert text.startswith("⚙ bash: ")
        assert text.endswith("…")
        # Truncated to 120 chars of command + ellipsis.
        assert len(text.split(": ", 1)[1]) == 121

    def test_tool_call_with_no_input_keys(self) -> None:
        payload = json.dumps(
            {
                "content": [
                    {"type": "tool_use", "name": "noop", "input": {}},
                ]
            }
        )
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "⚙ noop"}]


class TestKimiToolResult:
    def test_tool_result_with_output_string(self) -> None:
        payload = json.dumps(
            {
                "content": [
                    {"type": "tool_result", "output": "ok done"},
                ]
            }
        )
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "ok done"}]

    def test_tool_result_with_content_fallback(self) -> None:
        payload = json.dumps(
            {
                "content": [
                    {"type": "tool_result", "content": "from content"},
                ]
            }
        )
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "from content"}]

    def test_tool_result_skipped_when_empty(self) -> None:
        payload = json.dumps({"content": [{"type": "tool_result", "output": "   "}]})
        events, _ = _parse(payload)
        assert events == []

    def test_role_tool_string_content(self) -> None:
        payload = json.dumps({"role": "tool", "content": "results from tool"})
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "results from tool"}]

    def test_role_tool_ignored_when_non_string_content(self) -> None:
        # Use a dict for content -- non-string, and obj.get("content")
        # is not a list so the top-level iteration is skipped too.
        payload = json.dumps({"role": "tool", "content": {"foo": "bar"}})
        events, _ = _parse(payload)
        assert events == []


class TestKimiToolCallsTopLevel:
    def test_top_level_tool_calls_with_dict_arguments(self) -> None:
        payload = json.dumps(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "edit",
                            "arguments": {"path": "/file"},
                        }
                    }
                ]
            }
        )
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "⚙ edit: /file"}]

    def test_top_level_tool_calls_with_json_string_arguments(self) -> None:
        payload = json.dumps(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps({"command": "pwd"}),
                        }
                    }
                ]
            }
        )
        events, _ = _parse(payload)
        assert events == [{"type": "output_delta", "text": "⚙ bash: pwd"}]

    def test_top_level_tool_calls_with_unparseable_arguments(self) -> None:
        payload = json.dumps(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "noop",
                            "arguments": "not-json",
                        }
                    }
                ]
            }
        )
        events, _ = _parse(payload)
        # _parse_tool_arguments returns {} → summary has neither path nor command.
        assert events == [{"type": "output_delta", "text": "⚙ noop"}]


class TestKimiFatalError:
    def test_detects_fatal_in_plain_text(self) -> None:
        events, state = _parse(
            "<system>ERROR: LLM provider error when running agent: Connection error.</system>"
        )
        assert state.error_message is not None
        assert "LLM provider error" in state.error_message
        # The non-JSON line is also emitted as output.
        assert events and events[0]["type"] == "output_delta"

    def test_detects_fatal_inside_json_text_item(self) -> None:
        # The fatal-error scan runs first against the raw line, so when the
        # JSON payload itself contains a fatal pattern the error_message is
        # set to the (stripped) raw line. The subsequent per-item scan is a
        # no-op because error_message is already locked in.
        payload = json.dumps(
            {
                "content": [
                    {
                        "type": "text",
                        "text": "Session expired -- please reauthenticate",
                    }
                ]
            }
        )
        _, state = _parse(payload)
        assert state.error_message is not None
        assert "Session expired" in state.error_message

    def test_text_item_without_fatal_pattern_does_not_set_error(self) -> None:
        payload = json.dumps({"content": [{"type": "text", "text": "all good"}]})
        events, state = _parse(payload)
        assert state.error_message is None
        assert events == [{"type": "output_delta", "text": "all good"}]

    def test_detects_fatal_inside_tool_result(self) -> None:
        payload = json.dumps(
            {
                "content": [
                    {
                        "type": "tool_result",
                        "output": "ERROR: Unable to connect to provider — retry later",
                    }
                ]
            }
        )
        _, state = _parse(payload)
        assert state.error_message is not None
        assert "Unable to connect to provider" in state.error_message


class TestKimiNonJsonPassthrough:
    def test_plain_text_line_is_emitted_as_output_delta(self) -> None:
        events, _ = _parse("just thinking out loud")
        assert events == [{"type": "output_delta", "text": "just thinking out loud"}]

    def test_blank_line_yields_no_events(self) -> None:
        events, _ = _parse("   \t  ")
        assert events == []

    def test_json_with_non_list_content_yields_nothing(self) -> None:
        events, _ = _parse(json.dumps({"content": "not a list"}))
        assert events == []


class TestKimiShellScript:
    def test_make_shell_script_includes_pythonioencoding_and_workspace(self) -> None:
        adapter = KimiAdapter()
        cmd = adapter._build_argv(
            executable="kimi",
            prompt="hello",
            model="default",
            session_ref="sess-1",
            provider_options={},
        )
        from symphony.providers.base import CommandSpec

        script = adapter.make_shell_script("/tmp/work", CommandSpec(argv=cmd, preset_session_ref="sess-1"))
        assert "PYTHONIOENCODING=utf-8" in script
        assert "/tmp/work" in script
        assert "__symphony_exit" in script


class TestKimiBuildArgv:
    def test_includes_thinking_flag_by_default(self) -> None:
        adapter = KimiAdapter()
        argv = adapter._build_argv(
            executable="kimi",
            prompt="x",
            model="default",
            session_ref="s",
            provider_options={},
        )
        assert "--thinking" in argv

    def test_disables_thinking_when_requested(self) -> None:
        adapter = KimiAdapter()
        argv = adapter._build_argv(
            executable="kimi",
            prompt="x",
            model="default",
            session_ref="s",
            provider_options={"thinking_mode": "disabled"},
        )
        assert "--no-thinking" in argv
        assert "--thinking" not in argv

    def test_includes_ralph_iterations_when_set(self) -> None:
        adapter = KimiAdapter()
        argv = adapter._build_argv(
            executable="kimi",
            prompt="x",
            model="default",
            session_ref="s",
            provider_options={"max_ralph_iterations": "3"},
        )
        assert "--max-ralph-iterations" in argv
        assert "3" in argv

    def test_model_override_is_applied(self) -> None:
        adapter = KimiAdapter()
        argv = adapter._build_argv(
            executable="kimi",
            prompt="x",
            model="kimi-pro",
            session_ref="s",
            provider_options={},
        )
        assert "--model" in argv
        assert "kimi-pro" in argv
