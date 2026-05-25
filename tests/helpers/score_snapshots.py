"""Factory helpers for ScoreSnapshot fixtures."""
from __future__ import annotations

from datetime import datetime, timezone

from symphony.models import InstrumentName, ScoreSnapshot
from symphony.models.enums import ScoreStatus


def make_snapshot(
    *,
    score_id: str = "score-test-1",
    status: ScoreStatus = ScoreStatus.COMPLETED,
    provider: InstrumentName = InstrumentName.CLAUDE,
    model: str = "opus",
    accumulated_text: str = "",
    error: str | None = None,
    exit_code: int | None = 0,
    updated_at: str | None = None,
) -> ScoreSnapshot:
    """Build a ScoreSnapshot with sensible defaults for tests."""
    now = updated_at or datetime.now(timezone.utc).isoformat()
    return ScoreSnapshot(
        score_id=score_id,
        status=status,
        provider=provider,
        model=model,
        created_at=now,
        started_at=now,
        finished_at=now,
        updated_at=now,
        accumulated_text=accumulated_text,
        error=error,
        exit_code=exit_code,
        warnings=[],
    )
