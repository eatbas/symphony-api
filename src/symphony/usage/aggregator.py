"""Bounded JSONL aggregator for session-log-derived usage snapshots.

Walks a tree of ``*.jsonl`` files, parses each line into a
:class:`TurnSample` via a provider-specific callable, and aggregates the
counters that fall inside a sliding window.

Hard caps protect Symphony from a runaway probe when the user has a very
large session history: at most ``MAX_FILES_PER_PROBE`` files are visited
(newest first), at most ``MAX_LINES_PER_FILE`` lines per file, and at
most ``MAX_TOTAL_LINES`` lines overall.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import UsageCounters

logger = logging.getLogger("symphony.usage")

# Hard caps. Tuned to keep a probe well under the 10s timeout enforced in
# :mod:`symphony.usage.runner` even on a slow disk with a deep history.
MAX_FILES_PER_PROBE = 64
MAX_LINES_PER_FILE = 50_000
MAX_TOTAL_LINES = 250_000


@dataclass(frozen=True)
class TurnSample:
    """A single turn observation extracted from a session log."""

    timestamp: datetime
    counters: UsageCounters


LineParser = Callable[[dict[str, Any]], TurnSample | None]


def collect_samples(
    *,
    root: Path,
    pattern: str,
    parser: LineParser,
    cutoff: datetime,
) -> list[TurnSample]:
    """Walk JSONL files under *root* newer than *cutoff* and parse turns.

    Files are visited newest-first (by ``mtime``). Files whose ``mtime``
    is already older than *cutoff* are skipped without being opened.
    Lines that fail to parse as JSON, lines that yield ``None`` from
    *parser*, and lines whose timestamp is older than *cutoff* are all
    quietly skipped. ``OSError`` while opening a file is logged at debug
    and the file is skipped -- a misbehaving log must not break the probe.
    """
    if not root.exists():
        return []

    files = _files_with_mtime(root, pattern)

    samples: list[TurnSample] = []
    total_lines = 0
    for _mtime_float, mtime_dt, path in files:
        if mtime_dt is not None and mtime_dt < cutoff:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line_no, raw_line in enumerate(fh):
                    if line_no >= MAX_LINES_PER_FILE:
                        break
                    if total_lines >= MAX_TOTAL_LINES:
                        return samples
                    line = raw_line.strip()
                    total_lines += 1
                    if not line:
                        continue
                    sample = _parse_line(line, parser)
                    if sample is not None and sample.timestamp >= cutoff:
                        samples.append(sample)
        except OSError:
            logger.debug("Skipping unreadable session log: %s", path)
            continue
    return samples


def aggregate_window(
    samples: list[TurnSample],
    *,
    now: datetime,
    window: timedelta,
) -> UsageCounters:
    """Sum counters whose timestamp falls in ``[now - window, now]``.

    Returns a :class:`UsageCounters` with all ``None`` fields when no
    samples fall in the window. ``requests`` is the count of samples in
    the window; the per-counter sums skip ``None`` entries (so a sample
    that only carries ``input_tokens`` still contributes that field).
    """
    cutoff = now - window
    in_window = [s for s in samples if s.timestamp >= cutoff]
    if not in_window:
        return UsageCounters()

    return UsageCounters(
        requests=len(in_window) or None,
        input_tokens=_sum_field(in_window, "input_tokens"),
        output_tokens=_sum_field(in_window, "output_tokens"),
        total_tokens=_sum_field(in_window, "total_tokens"),
        cost_usd=_sum_field(in_window, "cost_usd"),
    )


def _files_with_mtime(
    root: Path, pattern: str
) -> list[tuple[float, datetime | None, Path]]:
    """Return matching files tagged with their mtime, newest first.

    Single ``stat()`` per file -- the result is used both as the sort key
    and as the cutoff gate inside :func:`collect_samples`. Files whose
    ``stat()`` fails (e.g. removed mid-walk) are kept with
    ``mtime_dt=None`` and a sort key of ``0.0`` so they sort to the back.
    """
    try:
        candidates = [p for p in root.rglob(pattern) if p.is_file()]
    except OSError:
        return []

    tagged: list[tuple[float, datetime | None, Path]] = []
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
            tagged.append((mtime, datetime.fromtimestamp(mtime, tz=timezone.utc), path))
        except OSError:
            tagged.append((0.0, None, path))
    tagged.sort(key=lambda entry: entry[0], reverse=True)
    return tagged[:MAX_FILES_PER_PROBE]


def _parse_line(line: str, parser: LineParser) -> TurnSample | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        return parser(obj)
    except Exception:
        # A misbehaving parser must not crash the whole probe.
        logger.debug("Parser raised on session-log line; skipping", exc_info=True)
        return None


def _sum_field(samples: list[TurnSample], field: str) -> float | int | None:
    values = [
        getattr(s.counters, field)
        for s in samples
        if getattr(s.counters, field) is not None
    ]
    if not values:
        return None
    total = sum(values)
    return total
