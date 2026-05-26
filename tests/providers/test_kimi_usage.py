"""Tests for the Kimi usage probe."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from symphony.providers.kimi import KimiAdapter, _kimi_sessions_root, _parse_kimi_line


def _write_sessions(home: Path, lines: list[str]) -> None:
    sessions_dir = home / ".kimi" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "session.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _turn(ts: datetime, *, input_tokens: int | None, output_tokens: int) -> str:
    usage = {"output_tokens": output_tokens}
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    return json.dumps(
        {
            "role": "assistant",
            "timestamp": ts.isoformat(),
            "usage": usage,
        }
    )


def test_parse_line_extracts_tokens() -> None:
    sample = _parse_kimi_line(
        json.loads(_turn(datetime.now(timezone.utc), input_tokens=10, output_tokens=4))
    )
    assert sample is not None
    assert sample.counters.input_tokens == 10
    assert sample.counters.output_tokens == 4
    assert sample.counters.total_tokens == 14


def test_parse_line_accepts_prompt_completion_aliases() -> None:
    obj = {
        "timestamp": "2026-05-26T12:00:00Z",
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    sample = _parse_kimi_line(obj)
    assert sample is not None
    assert sample.counters.input_tokens == 7
    assert sample.counters.output_tokens == 3


def test_parse_line_uses_explicit_total_tokens_when_present() -> None:
    obj = {
        "timestamp": "2026-05-26T12:00:00Z",
        "usage": {"input_tokens": 5, "output_tokens": 7, "total_tokens": 42},
    }
    sample = _parse_kimi_line(obj)
    assert sample is not None
    assert sample.counters.total_tokens == 42


def test_parse_line_returns_none_without_usage() -> None:
    obj = {"timestamp": "2026-05-26T12:00:00Z", "role": "assistant"}
    assert _parse_kimi_line(obj) is None


def test_parse_line_returns_none_without_output_tokens() -> None:
    obj = {"timestamp": "2026-05-26T12:00:00Z", "usage": {"input_tokens": 5}}
    assert _parse_kimi_line(obj) is None


def test_parse_line_returns_none_with_non_int_input() -> None:
    obj = {
        "timestamp": "2026-05-26T12:00:00Z",
        "usage": {"input_tokens": "five", "output_tokens": 3},
    }
    assert _parse_kimi_line(obj) is None


def test_parse_line_returns_none_for_invalid_timestamp() -> None:
    obj = {
        "timestamp": "not-a-date",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    assert _parse_kimi_line(obj) is None


def test_kimi_sessions_root_honours_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path / "custom"))
    assert _kimi_sessions_root() == tmp_path / "custom" / "sessions"


def test_kimi_sessions_root_defaults_to_home(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("KIMI_SHARE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert _kimi_sessions_root() == Path(tmp_path) / ".kimi" / "sessions"


async def test_get_usage_returns_not_supported_when_root_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("KIMI_SHARE_DIR", str(tmp_path / "missing"))
    adapter = KimiAdapter()

    snapshots = await adapter.get_usage(
        executable="kimi",
        models=["kimi-code"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=datetime.now(timezone.utc),
    )

    assert len(snapshots) == 1
    assert snapshots[0].supported is False


async def test_get_usage_aggregates_both_windows(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("KIMI_SHARE_DIR", raising=False)
    now = datetime.now(timezone.utc)
    _write_sessions(
        tmp_path,
        [
            _turn(now - timedelta(minutes=10), input_tokens=10, output_tokens=5),
            _turn(now - timedelta(hours=9), input_tokens=None, output_tokens=3),
        ],
    )

    adapter = KimiAdapter()
    snapshots = await adapter.get_usage(
        executable="kimi",
        models=["kimi-code"],
        musician_lookup=lambda _p: None,
        run_subprocess=AsyncMock(return_value=(0, "")),
        now=now,
    )

    by_window = {s.window: s for s in snapshots}
    assert by_window["5h_rolling"].used.output_tokens == 5
    assert by_window["weekly"].used.output_tokens == 8
