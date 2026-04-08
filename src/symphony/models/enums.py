from __future__ import annotations

from enum import StrEnum


class InstrumentName(StrEnum):
    """Supported AI CLI instrument identifiers."""

    GEMINI = "gemini"
    CODEX = "codex"
    CLAUDE = "claude"
    KIMI = "kimi"
    COPILOT = "copilot"
    OPENCODE = "opencode"


class ChatMode(StrEnum):
    """Whether to start a new session or resume an existing one."""

    NEW = "new"
    RESUME = "resume"


class ScoreStatus(StrEnum):
    """Lifecycle state of a submitted score."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


TERMINAL_STATUSES = {
    ScoreStatus.COMPLETED,
    ScoreStatus.FAILED,
    ScoreStatus.STOPPED,
}
