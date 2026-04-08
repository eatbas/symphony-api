from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..models import ChatResponse, ScoreSnapshot
from ..models.enums import TERMINAL_STATUSES, ScoreStatus, InstrumentName


def _safe_error_message(exc: BaseException) -> str:
    """Return a non-empty error description for any exception."""

    msg = str(exc)
    if msg:
        return msg
    return repr(exc) or type(exc).__name__ or "unknown error"


_MAX_SUBSCRIBER_QUEUE_SIZE = 256


def now_rfc3339() -> str:
    """Return the current UTC timestamp as an RFC 3339 string."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class ScoreHandle:
    result_future: asyncio.Future[ChatResponse] | None = None
    score_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    status: ScoreStatus = ScoreStatus.QUEUED
    provider: InstrumentName | None = None
    model: str | None = None
    accumulated_text: str = ""
    final_text: str | None = None
    provider_session_ref: str | None = None
    error: str | None = None
    exit_code: int | None = None
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_rfc3339)
    started_at: str | None = None
    updated_at: str = field(default_factory=now_rfc3339)
    finished_at: str | None = None
    _persist_callback: Any = None
    _subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)

    async def publish(self, event: dict[str, Any]) -> None:
        self.apply_event(event)
        await self.persist()
        self.broadcast(event)

    def set_persist_callback(self, callback: Any) -> None:
        self._persist_callback = callback

    async def persist(self) -> None:
        if self._persist_callback is not None:
            await self._persist_callback(self.snapshot())

    def resolve(self, result: ChatResponse) -> None:
        """Resolve the result future if one is attached."""
        if self.result_future and not self.result_future.done():
            self.result_future.set_result(result)

    def reject(self, exc: BaseException) -> None:
        """Reject the result future if one is attached."""
        if self.result_future and not self.result_future.done():
            self.result_future.set_exception(exc)

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        self.updated_at = now_rfc3339()

        if event_type == "run_started":
            self.status = ScoreStatus.RUNNING
            self.started_at = self.started_at or self.updated_at
        elif event_type == "provider_session":
            self.provider_session_ref = event.get("provider_session_ref") or self.provider_session_ref
        elif event_type == "output_delta":
            text = str(event.get("text", ""))
            self.accumulated_text = f"{self.accumulated_text}\n{text}".strip() if self.accumulated_text else text
        elif event_type == "completed":
            self.status = ScoreStatus.COMPLETED
            self.provider_session_ref = event.get("provider_session_ref") or self.provider_session_ref
            self.final_text = event.get("final_text") or self.accumulated_text
            self.exit_code = event.get("exit_code")
            self.warnings = list(event.get("warnings") or [])
            self.finished_at = self.updated_at
        elif event_type == "failed":
            self.status = ScoreStatus.FAILED
            self.provider_session_ref = event.get("provider_session_ref") or self.provider_session_ref
            self.error = event.get("error") or self.error
            self.exit_code = event.get("exit_code")
            self.warnings = list(event.get("warnings") or [])
            self.finished_at = self.updated_at
        elif event_type == "stopped":
            self.status = ScoreStatus.STOPPED
            self.finished_at = self.updated_at

    def snapshot(self) -> ScoreSnapshot:
        return ScoreSnapshot(
            score_id=self.score_id,
            status=self.status,
            provider=self.provider,
            model=self.model,
            accumulated_text=self.accumulated_text,
            final_text=self.final_text,
            provider_session_ref=self.provider_session_ref,
            error=self.error,
            exit_code=self.exit_code,
            warnings=self.warnings,
            created_at=self.created_at,
            started_at=self.started_at,
            updated_at=self.updated_at,
            finished_at=self.finished_at,
        )

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def broadcast(self, event: dict[str, Any]) -> None:
        stale: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(dict(event))
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)

    @classmethod
    def from_snapshot(cls, snapshot: ScoreSnapshot) -> "ScoreHandle":
        return cls(
            score_id=snapshot.score_id,
            status=snapshot.status,
            provider=snapshot.provider,
            model=snapshot.model,
            accumulated_text=snapshot.accumulated_text,
            final_text=snapshot.final_text,
            provider_session_ref=snapshot.provider_session_ref,
            error=snapshot.error,
            exit_code=snapshot.exit_code,
            warnings=list(snapshot.warnings),
            created_at=snapshot.created_at,
            started_at=snapshot.started_at,
            updated_at=snapshot.updated_at,
            finished_at=snapshot.finished_at,
        )


def stopped_event(handle: ScoreHandle) -> dict[str, Any]:
    """Build a terminal stopped event for the given handle."""
    return {
        "type": "stopped",
        "score_id": handle.score_id,
        "provider": handle.provider.value if handle.provider else None,
        "model": handle.model,
    }


__all__ = [
    "ScoreHandle",
    "TERMINAL_STATUSES",
    "now_rfc3339",
    "stopped_event",
    "_safe_error_message",
]
