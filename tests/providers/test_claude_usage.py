"""Tests for the Claude usage probe."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from symphony.providers.claude import ClaudeAdapter, _parse_claude_line


def _write_transcripts(home: Path, lines: list[str]) -> None:
    transcripts_dir = home / ".claude" / "projects" / "demo-workspace"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    (transcripts_dir / "session.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _assistant_line(ts: datetime, *, input_tokens: int, output_tokens: int) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts.isoformat(),
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hi"}],
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }
    )


def test_parse_line_extracts_token_counts() -> None:
    now = datetime.now(timezone.utc)
    sample = _parse_claude_line(json.loads(_assistant_line(now, input_tokens=10, output_tokens=4)))
    assert sample is not None
    assert sample.counters.input_tokens == 10
    assert sample.counters.output_tokens == 4
    assert sample.counters.total_tokens == 14


def test_parse_line_returns_none_when_timestamp_missing() -> None:
    obj = {"type": "assistant", "message": {"usage": {"input_tokens": 1, "output_tokens": 1}}}
    assert _parse_claude_line(obj) is None


def test_parse_line_returns_none_when_timestamp_malformed() -> None:
    """Covers the ``except ValueError`` branch in ``_parse_claude_line``."""
    obj = {
        "type": "assistant",
        "timestamp": "not-an-iso-timestamp",
        "message": {"usage": {"input_tokens": 1, "output_tokens": 1}},
    }
    assert _parse_claude_line(obj) is None


def test_parse_line_returns_none_when_message_is_not_a_dict() -> None:
    obj = {
        "type": "assistant",
        "timestamp": "2026-05-26T12:00:00Z",
        "message": None,
    }
    assert _parse_claude_line(obj) is None


def test_parse_line_resets_cache_read_when_non_int() -> None:
    """A truthy non-int ``cache_read_input_tokens`` must be reset to 0."""
    obj = {
        "type": "assistant",
        "timestamp": "2026-05-26T12:00:00Z",
        "message": {
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_creation_input_tokens": 4,
                "cache_read_input_tokens": "unexpected-string",
            }
        },
    }
    sample = _parse_claude_line(obj)
    assert sample is not None
    # cache_read should not contribute -- total_tokens = 1+2+4+0 = 7.
    assert sample.counters.total_tokens == 7


def test_parse_line_returns_none_when_usage_missing() -> None:
    obj = {"type": "assistant", "timestamp": "2026-05-26T12:00:00Z", "message": {}}
    assert _parse_claude_line(obj) is None


def test_parse_line_returns_none_when_tokens_not_integer() -> None:
    obj = {
        "type": "assistant",
        "timestamp": "2026-05-26T12:00:00Z",
        "message": {"usage": {"input_tokens": "10", "output_tokens": 5}},
    }
    assert _parse_claude_line(obj) is None


def test_parse_line_tolerates_naive_timestamp() -> None:
    obj = {
        "type": "assistant",
        "timestamp": "2026-05-26T12:00:00",  # no offset
        "message": {"usage": {"input_tokens": 1, "output_tokens": 2}},
    }
    sample = _parse_claude_line(obj)
    assert sample is not None
    assert sample.timestamp.tzinfo is not None


def test_parse_line_tolerates_non_integer_cache_fields() -> None:
    obj = {
        "type": "assistant",
        "timestamp": "2026-05-26T12:00:00Z",
        "message": {
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_creation_input_tokens": "weird",
                "cache_read_input_tokens": None,
            }
        },
    }
    sample = _parse_claude_line(obj)
    assert sample is not None
    assert sample.counters.total_tokens == 3


async def test_get_usage_returns_not_supported_when_root_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    adapter = ClaudeAdapter()

    snapshots = await adapter.get_usage(
        executable="claude",
        models=["sonnet"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=datetime.now(timezone.utc),
    )

    assert len(snapshots) == 1
    assert snapshots[0].supported is False
    assert snapshots[0].source == "not_supported"


async def test_get_usage_aggregates_both_windows(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    now = datetime.now(timezone.utc)
    _write_transcripts(
        tmp_path,
        [
            # within both 5h and weekly
            _assistant_line(now - timedelta(minutes=10), input_tokens=10, output_tokens=4),
            # within weekly but outside 5h
            _assistant_line(now - timedelta(hours=8), input_tokens=20, output_tokens=8),
        ],
    )

    adapter = ClaudeAdapter()
    snapshots = await adapter.get_usage(
        executable="claude",
        models=["sonnet"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=now,
    )

    assert len(snapshots) == 2
    by_window = {s.window: s for s in snapshots}
    five_h = by_window["5h_rolling"]
    weekly = by_window["weekly"]
    assert five_h.supported is True
    assert five_h.used.input_tokens == 10
    assert five_h.used.output_tokens == 4
    assert weekly.used.input_tokens == 30
    assert weekly.used.output_tokens == 12
