"""Tests for the Codex usage probe."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from symphony.providers.codex import CodexAdapter, _parse_codex_line


def _write_sessions(home: Path, lines: list[str]) -> None:
    sessions_dir = home / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "rollout.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _turn_completed(ts: datetime, *, input_tokens: int, output_tokens: int) -> str:
    return json.dumps(
        {
            "type": "turn.completed",
            "timestamp": ts.isoformat(),
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


def test_parse_line_only_accepts_turn_completed() -> None:
    obj = {
        "type": "thread.started",
        "timestamp": "2026-05-26T12:00:00Z",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    assert _parse_codex_line(obj) is None


def test_parse_line_extracts_tokens_and_total() -> None:
    sample = _parse_codex_line(
        json.loads(_turn_completed(datetime.now(timezone.utc), input_tokens=10, output_tokens=4))
    )
    assert sample is not None
    assert sample.counters.input_tokens == 10
    assert sample.counters.output_tokens == 4
    assert sample.counters.total_tokens == 14


def test_parse_line_returns_none_when_output_missing() -> None:
    obj = {
        "type": "turn.completed",
        "timestamp": "2026-05-26T12:00:00Z",
        "usage": {"input_tokens": 1},
    }
    assert _parse_codex_line(obj) is None


def test_parse_line_returns_none_when_usage_block_missing() -> None:
    """Covers the ``usage is not a dict`` early return in ``_parse_codex_line``."""
    obj = {"type": "turn.completed", "timestamp": "2026-05-26T12:00:00Z"}
    assert _parse_codex_line(obj) is None


def test_parse_line_returns_none_when_timestamp_malformed() -> None:
    """Covers the ``except ValueError`` branch in ``_parse_timestamp``."""
    obj = {
        "type": "turn.completed",
        "timestamp": "definitely-not-iso",
        "usage": {"output_tokens": 3},
    }
    assert _parse_codex_line(obj) is None


def test_parse_line_attaches_utc_to_naive_timestamp() -> None:
    """Covers the ``tzinfo is None`` fixup in ``_parse_timestamp``."""
    obj = {
        "type": "turn.completed",
        "timestamp": "2026-05-26T12:00:00",  # no offset
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    sample = _parse_codex_line(obj)
    assert sample is not None
    assert sample.timestamp.tzinfo is not None


def test_parse_line_returns_none_when_timestamp_missing() -> None:
    obj = {
        "type": "turn.completed",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    assert _parse_codex_line(obj) is None


def test_parse_line_accepts_ts_alias() -> None:
    obj = {
        "type": "turn.completed",
        "ts": "2026-05-26T12:00:00Z",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    sample = _parse_codex_line(obj)
    assert sample is not None


def test_parse_line_rejects_non_int_input_tokens() -> None:
    obj = {
        "type": "turn.completed",
        "timestamp": "2026-05-26T12:00:00Z",
        "usage": {"input_tokens": "10", "output_tokens": 2},
    }
    assert _parse_codex_line(obj) is None


def test_parse_line_handles_missing_input_tokens_gracefully() -> None:
    """Codex sometimes only reports output tokens; the parser should still
    accept the sample with input_tokens=None."""
    obj = {
        "type": "turn.completed",
        "timestamp": "2026-05-26T12:00:00Z",
        "usage": {"output_tokens": 3},
    }
    sample = _parse_codex_line(obj)
    assert sample is not None
    assert sample.counters.input_tokens is None
    assert sample.counters.output_tokens == 3


async def test_get_usage_returns_not_supported_when_root_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    adapter = CodexAdapter()

    snapshots = await adapter.get_usage(
        executable="codex",
        models=["gpt-5.4"],
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
    _write_sessions(
        tmp_path,
        [
            _turn_completed(now - timedelta(minutes=30), input_tokens=10, output_tokens=4),
            _turn_completed(now - timedelta(hours=10), input_tokens=20, output_tokens=8),
            # Non-turn.completed event should be ignored.
            json.dumps({"type": "thread.started", "timestamp": now.isoformat()}),
        ],
    )

    adapter = CodexAdapter()
    snapshots = await adapter.get_usage(
        executable="codex",
        models=["gpt-5.4"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=now,
    )

    assert len(snapshots) == 2
    by_window = {s.window: s for s in snapshots}
    assert by_window["5h_rolling"].used.output_tokens == 4
    assert by_window["weekly"].used.output_tokens == 12
