"""Snapshot pair builder for session-log-derived usage probes.

Every provider that reports usage by parsing local session logs returns
two :class:`UsageSnapshot` entries -- one for the 5-hour rolling window
and one for the weekly window. This module owns the boilerplate so each
adapter only needs to supply its log root, glob pattern, and parser.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from ..models.enums import InstrumentName
from .aggregator import LineParser, aggregate_window, collect_samples
from .models import UsageCounters, UsageSnapshot, UsageWindow

WINDOW_5H = timedelta(hours=5)
WINDOW_WEEKLY = timedelta(days=7)


def build_session_log_snapshots(
    *,
    provider: InstrumentName,
    root: Path,
    pattern: str,
    parser: LineParser,
    now: datetime,
    not_found_note: str,
    model: str | None = None,
) -> list[UsageSnapshot]:
    """Synchronous snapshot pair builder.

    Call via :func:`asyncio.to_thread` from adapter code so filesystem
    work does not block the event loop.
    """
    if not root.exists():
        return [
            UsageSnapshot(
                provider=provider,
                supported=False,
                source="not_supported",
                note=not_found_note,
                as_of=now.isoformat(),
            )
        ]

    cutoff = now - WINDOW_WEEKLY
    samples = collect_samples(
        root=root, pattern=pattern, parser=parser, cutoff=cutoff
    )

    return [
        _snapshot(provider, samples, now, "5h_rolling", WINDOW_5H, model=model),
        _snapshot(provider, samples, now, "weekly", WINDOW_WEEKLY, model=model),
    ]


async def build_session_log_snapshots_async(
    *,
    provider: InstrumentName,
    root: Path,
    pattern: str,
    parser: LineParser,
    now: datetime,
    not_found_note: str,
    model: str | None = None,
) -> list[UsageSnapshot]:
    """Async wrapper that runs the bounded filesystem scan in a thread."""
    return await asyncio.to_thread(
        build_session_log_snapshots,
        provider=provider,
        root=root,
        pattern=pattern,
        parser=parser,
        now=now,
        not_found_note=not_found_note,
        model=model,
    )


def _snapshot(
    provider: InstrumentName,
    samples: list,
    now: datetime,
    window_name: UsageWindow,
    window: timedelta,
    *,
    model: str | None,
) -> UsageSnapshot:
    used = aggregate_window(samples, now=now, window=window)
    has_data = _has_any_counter(used)
    return UsageSnapshot(
        provider=provider,
        model=model,
        supported=True,
        window=window_name,
        used=used,
        source="session_log",
        as_of=now.isoformat(),
        note=None if has_data else "No usage recorded in this window.",
    )


def _has_any_counter(counters: UsageCounters) -> bool:
    """True when at least one counter field is populated.

    Iterates the Pydantic model dump so the check stays in sync with
    :class:`UsageCounters` if a new field is added.
    """
    return any(value is not None for value in counters.model_dump().values())
