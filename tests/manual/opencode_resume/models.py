"""Dataclasses, constants, and tomllib loader for the OpenCode harness."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROVIDER = "opencode"
DEFAULT_BASE = "http://127.0.0.1:8080"
DEFAULT_WORKSPACE = "C:/Github/symphony-api"
DEFAULT_NEW_PROMPT = (
    "Remember the phrase Symphony-Pulse-Quartet. "
    "Reply with one short sentence acknowledging it."
)
DEFAULT_RESUME_PROMPT = (
    "What phrase did I ask you to remember? Reply with just the phrase."
)
DEFAULT_MEMORY_TOKEN = "Symphony-Pulse-Quartet"
TERMINAL_STATUSES = {"completed", "failed", "stopped"}
RATE_LIMIT_HINTS: tuple[str, ...] = (
    "429",
    "rate limit",
    "rate-limit",
    "rate_limit",
    "quota",
    "too many requests",
)


@dataclass
class CallResult:
    """Outcome of a single NEW or RESUME call against ``/v1/chat``."""

    status: str = "skipped"
    exit_code: int | None = None
    duration_s: float = 0.0
    final_text: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    score_id: str = ""
    provider_session_ref: str | None = None


@dataclass
class ModelResult:
    """End-to-end NEW + RESUME outcome for one OpenCode model."""

    provider: str
    model: str
    new: CallResult
    resume: CallResult
    keyword_pass: bool
    notes: list[str] = field(default_factory=list)


def load_opencode_models(config_path: Path) -> list[str]:
    """Return the canonical OpenCode model list straight from ``config.toml``."""
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    return list(data["providers"]["opencode"]["models"])


def looks_rate_limited(result: CallResult) -> bool:
    """Heuristic check for OpenRouter free-tier rate-limit symptoms.

    The check is intentionally generous so a soft-limit response that
    still completes (e.g. a 429 surfaced as an error string) is caught
    before the driver burns the rest of the budget on doomed calls.
    """
    haystacks = [
        result.error or "",
        result.final_text or "",
        " ".join(result.warnings or []),
    ]
    blob = " ".join(haystacks).lower()
    return any(hint in blob for hint in RATE_LIMIT_HINTS)
