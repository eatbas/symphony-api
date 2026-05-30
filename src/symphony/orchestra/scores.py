"""Score handle registry and lifecycle helpers.

Extracted from :mod:`orchestra` to keep that file under the project's
300-line cap.  Each public function takes the orchestra instance as the
first argument and reads/writes its ``_scores`` registry and
``score_store``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..models import ScoreSnapshot
from ..models.enums import ScoreStatus, TERMINAL_STATUSES
from ..shells import ScoreCancelledError
from .musician import Musician
from .score import ScoreHandle, now_rfc3339, stopped_event

if TYPE_CHECKING:
    from .orchestra import Orchestra

logger = logging.getLogger("symphony.orchestra.scores")

RESTART_INTERRUPTION_ERROR = "Symphony restarted before the score finished"


async def persist_snapshot(orchestra: "Orchestra", snapshot: ScoreSnapshot) -> None:
    """Persist a score snapshot to disk off the event loop."""
    await asyncio.to_thread(orchestra.score_store.save, snapshot)


def register_score(
    orchestra: "Orchestra",
    handle: ScoreHandle,
    *,
    max_scores: int,
) -> None:
    """Register a score handle in memory so it can be looked up and cancelled.

    The initial durable snapshot is persisted by the caller via
    :meth:`Orchestra.persist_snapshot` (off the event loop), keeping this
    hot-path function free of blocking disk I/O.
    """
    handle.set_persist_callback(orchestra.persist_snapshot)
    orchestra._scores[handle.score_id] = handle
    evict_old_scores(orchestra, max_scores=max_scores)


def get_score_snapshot(orchestra: "Orchestra", score_id: str) -> ScoreSnapshot | None:
    """Return the in-memory snapshot if alive, else the on-disk one."""
    handle = orchestra._scores.get(score_id)
    if handle is not None:
        return handle.snapshot()
    return orchestra.score_store.load(score_id)


async def stop_score(orchestra: "Orchestra", score_id: str) -> ScoreHandle | None:
    """Cancel a running or queued score. Returns the handle, or None if not found."""
    handle = orchestra._scores.get(score_id)
    if handle is None:
        return None

    if handle.status in TERMINAL_STATUSES:
        return handle  # Idempotent

    handle.cancelled.set()

    if handle.status == ScoreStatus.RUNNING:
        musician = find_musician_for_score(orchestra, handle)
        handle.reject(ScoreCancelledError(f"Score {score_id} was stopped"))
        if musician is not None:
            try:
                await musician.shell.interrupt()
            except Exception as exc:
                logger.warning("Failed to interrupt score %s cleanly: %s", score_id, exc)
        handle.status = ScoreStatus.STOPPED
        await handle.publish(stopped_event(handle))

    elif handle.status == ScoreStatus.QUEUED:
        handle.status = ScoreStatus.STOPPED
        await handle.publish(stopped_event(handle))
        handle.reject(ScoreCancelledError(f"Score {score_id} cancelled while queued"))

    return handle


def restore_scores(orchestra: "Orchestra", *, max_scores: int) -> None:
    """Load persisted score snapshots into memory and recover interrupted runs."""
    for snapshot in orchestra.score_store.load_all():
        if snapshot.status in {ScoreStatus.QUEUED, ScoreStatus.RUNNING}:
            snapshot.status = ScoreStatus.FAILED
            snapshot.error = RESTART_INTERRUPTION_ERROR
            snapshot.finished_at = now_rfc3339()
            snapshot.updated_at = snapshot.finished_at
            orchestra.score_store.save(snapshot)

        handle = ScoreHandle.from_snapshot(snapshot)
        handle.set_persist_callback(orchestra.persist_snapshot)
        orchestra._scores[handle.score_id] = handle

    evict_old_scores(orchestra, max_scores=max_scores)
    # Enforce the on-disk cap deterministically at boot, since per-save
    # pruning is now throttled.
    orchestra.score_store.prune()


def find_musician_for_score(orchestra: "Orchestra", handle: ScoreHandle) -> Musician | None:
    """Find the musician currently executing the given score."""
    for musician in orchestra._all_musicians():
        if musician._current_handle is handle:
            return musician
    return None


def evict_old_scores(orchestra: "Orchestra", *, max_scores: int) -> None:
    """Remove oldest terminal scores when the registry exceeds *max_scores*."""
    terminal_ids = [sid for sid, h in orchestra._scores.items() if h.status in TERMINAL_STATUSES]
    excess = len(terminal_ids) - max_scores
    if excess > 0:
        for sid in terminal_ids[:excess]:
            del orchestra._scores[sid]
