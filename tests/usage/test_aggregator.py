"""Unit tests for the bounded JSONL aggregator."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from symphony.usage.aggregator import (
    MAX_LINES_PER_FILE,
    TurnSample,
    aggregate_window,
    collect_samples,
)
from symphony.usage.models import UsageCounters


def _parser(obj: dict) -> TurnSample | None:
    ts_raw = obj.get("ts")
    tokens = obj.get("tokens")
    if not isinstance(ts_raw, str) or not isinstance(tokens, int):
        return None
    ts = datetime.fromisoformat(ts_raw)
    return TurnSample(
        timestamp=ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts,
        counters=UsageCounters(input_tokens=tokens, total_tokens=tokens),
    )


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_collect_samples_returns_empty_for_missing_root(tmp_path: Path) -> None:
    assert (
        collect_samples(
            root=tmp_path / "missing",
            pattern="*.jsonl",
            parser=_parser,
            cutoff=datetime.now(timezone.utc) - timedelta(days=7),
        )
        == []
    )


def test_collect_samples_reads_recent_lines(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    log = tmp_path / "a.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps({"ts": now.isoformat(), "tokens": 10}),
            json.dumps({"ts": (now - timedelta(hours=2)).isoformat(), "tokens": 5}),
        ],
    )

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=now - timedelta(days=7),
    )

    assert len(samples) == 2
    assert sum(s.counters.input_tokens for s in samples) == 15


def test_collect_samples_drops_lines_older_than_cutoff(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    log = tmp_path / "a.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps({"ts": (now - timedelta(days=30)).isoformat(), "tokens": 999}),
            json.dumps({"ts": now.isoformat(), "tokens": 7}),
        ],
    )

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=now - timedelta(days=7),
    )

    assert len(samples) == 1
    assert samples[0].counters.input_tokens == 7


def test_collect_samples_skips_malformed_json(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    log = tmp_path / "a.jsonl"
    _write_jsonl(
        log,
        [
            "not json at all",
            "[]",  # JSON but not a dict
            json.dumps({"ts": "not-iso", "tokens": 5}),  # parser returns None
            json.dumps({"ts": now.isoformat(), "tokens": 3}),
        ],
    )

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=now - timedelta(days=7),
    )

    assert len(samples) == 1
    assert samples[0].counters.input_tokens == 3


def test_collect_samples_caps_lines_per_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "symphony.usage.aggregator.MAX_LINES_PER_FILE", 3
    )
    now = datetime.now(timezone.utc)
    log = tmp_path / "a.jsonl"
    _write_jsonl(
        log,
        [json.dumps({"ts": now.isoformat(), "tokens": 1}) for _ in range(10)],
    )

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=now - timedelta(days=7),
    )

    assert len(samples) == 3


def test_collect_samples_caps_total_lines(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "symphony.usage.aggregator.MAX_TOTAL_LINES", 2
    )
    now = datetime.now(timezone.utc)
    _write_jsonl(
        tmp_path / "a.jsonl",
        [json.dumps({"ts": now.isoformat(), "tokens": 1}) for _ in range(5)],
    )
    _write_jsonl(
        tmp_path / "b.jsonl",
        [json.dumps({"ts": now.isoformat(), "tokens": 1}) for _ in range(5)],
    )

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=now - timedelta(days=7),
    )

    assert len(samples) == 2


def test_collect_samples_recovers_from_parser_exception(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    _write_jsonl(
        tmp_path / "a.jsonl",
        [
            json.dumps({"ts": now.isoformat(), "tokens": 1}),
            json.dumps({"ts": now.isoformat(), "tokens": 2}),
        ],
    )

    def boom(_obj):
        raise ValueError("nope")

    assert collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=boom,
        cutoff=now - timedelta(days=7),
    ) == []


def test_aggregate_window_returns_empty_counters_when_no_samples() -> None:
    now = datetime.now(timezone.utc)
    counters = aggregate_window([], now=now, window=timedelta(hours=5))
    assert counters == UsageCounters()


def test_aggregate_window_sums_only_in_window_samples() -> None:
    now = datetime.now(timezone.utc)
    samples = [
        TurnSample(
            timestamp=now - timedelta(minutes=10),
            counters=UsageCounters(input_tokens=10, output_tokens=5, total_tokens=15),
        ),
        TurnSample(
            timestamp=now - timedelta(hours=6),  # outside 5h window
            counters=UsageCounters(input_tokens=99, output_tokens=99),
        ),
        TurnSample(
            timestamp=now - timedelta(minutes=2),
            counters=UsageCounters(input_tokens=20, output_tokens=10, total_tokens=30),
        ),
    ]
    counters = aggregate_window(samples, now=now, window=timedelta(hours=5))
    assert counters.requests == 2
    assert counters.input_tokens == 30
    assert counters.output_tokens == 15
    assert counters.total_tokens == 45


def test_aggregate_window_skips_none_fields() -> None:
    now = datetime.now(timezone.utc)
    samples = [
        TurnSample(
            timestamp=now - timedelta(minutes=1),
            counters=UsageCounters(input_tokens=5),  # only input_tokens
        ),
        TurnSample(
            timestamp=now - timedelta(minutes=2),
            counters=UsageCounters(cost_usd=0.25),  # only cost
        ),
    ]
    counters = aggregate_window(samples, now=now, window=timedelta(hours=5))
    assert counters.input_tokens == 5
    assert counters.output_tokens is None
    assert counters.cost_usd == 0.25


def test_max_lines_per_file_is_reasonable() -> None:
    """Sanity guard: the cap should be high enough to cover real sessions
    but low enough to prevent runaway probes."""
    assert MAX_LINES_PER_FILE >= 10_000


def test_collect_samples_skips_files_with_mtime_older_than_cutoff(
    tmp_path: Path,
) -> None:
    """Forces a file's mtime back so the mtime-skip branch fires."""
    import os
    import time

    now = datetime.now(timezone.utc)
    log = tmp_path / "stale.jsonl"
    _write_jsonl(
        log,
        [json.dumps({"ts": now.isoformat(), "tokens": 5})],
    )
    # 30 days back; outside the 7-day cutoff used below.
    old_ts = time.time() - 30 * 86400
    os.utime(log, (old_ts, old_ts))

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=now - timedelta(days=7),
    )

    assert samples == []


def test_collect_samples_skips_empty_lines(tmp_path: Path) -> None:
    """The aggregator must ignore blank lines inside a JSONL file."""
    now = datetime.now(timezone.utc)
    log = tmp_path / "with_blanks.jsonl"
    payload = json.dumps({"ts": now.isoformat(), "tokens": 7})
    log.write_text(f"{payload}\n\n   \n{payload}\n", encoding="utf-8")

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=now - timedelta(days=7),
    )

    assert len(samples) == 2


def test_collect_samples_skips_files_that_raise_on_open(
    tmp_path: Path, monkeypatch
) -> None:
    """A file that raises ``OSError`` when opened must be skipped gracefully."""
    now = datetime.now(timezone.utc)
    log = tmp_path / "unreadable.jsonl"
    _write_jsonl(log, [json.dumps({"ts": now.isoformat(), "tokens": 1})])

    original_open = Path.open

    def boom(self, *args, **kwargs):
        if self == log:
            raise OSError("simulated unreadable file")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", boom)

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=now - timedelta(days=7),
    )

    assert samples == []


def test_collect_samples_returns_empty_when_rglob_raises(
    tmp_path: Path, monkeypatch
) -> None:
    """If ``rglob`` raises ``OSError`` (e.g. permissions), return ``[]``."""
    def boom(self, pattern):
        raise OSError("simulated rglob failure")

    monkeypatch.setattr(Path, "rglob", boom)

    samples = collect_samples(
        root=tmp_path,
        pattern="*.jsonl",
        parser=_parser,
        cutoff=datetime.now(timezone.utc) - timedelta(days=7),
    )

    assert samples == []


def test_files_with_mtime_tags_failures_with_zero_and_none(
    tmp_path: Path, monkeypatch
) -> None:
    """A file whose ``stat()`` fails inside ``_files_with_mtime`` must be
    tagged with ``(0.0, None, path)`` so it sorts to the back rather than
    breaking the probe. Calling ``_files_with_mtime`` directly side-steps
    the ``is_file()`` gate (which also calls ``stat``) so the test can
    exercise the exact stat-on-tagged-file branch (lines 137-138).
    """
    from symphony.usage.aggregator import _files_with_mtime

    bad = tmp_path / "bad.jsonl"
    bad.write_text("{}", encoding="utf-8")

    # Make rglob yield our path directly, bypassing the is_file() gate.
    monkeypatch.setattr(Path, "rglob", lambda self, _pattern: iter([bad]))

    original_stat = Path.stat

    def stat_fail(self, *args, **kwargs):
        if self == bad:
            raise OSError("simulated stat failure")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_fail)
    # is_file() also calls stat; bypass it so we reach the inner loop.
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    tagged = _files_with_mtime(tmp_path, "*.jsonl")

    assert len(tagged) == 1
    assert tagged[0][0] == 0.0
    assert tagged[0][1] is None
    assert tagged[0][2] == bad
