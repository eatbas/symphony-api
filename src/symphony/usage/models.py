"""Pydantic models for usage / quota snapshots returned by the API.

The shape is intentionally permissive: any of ``limit``, ``used``,
``remaining``, ``window`` and ``resets_at`` may be ``None`` per provider.
That lets the API communicate partial knowledge honestly when a CLI
does not surface a particular figure, rather than fabricating numbers.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import InstrumentName

# ``5h_rolling`` and ``weekly`` are the primary windows Symphony reports,
# matching the way Claude Code natively communicates its quota. The
# remaining values exist as fallbacks for providers that publish a
# different cadence (e.g. ``session`` for one-shot CLIs).
UsageWindow = Literal[
    "5h_rolling",
    "weekly",
    "session",
    "day",
    "month",
    "rolling",
    "unknown",
]

UsageSource = Literal[
    "cli_command",
    "session_log",
    "stream",
    "api",
    "not_supported",
]


class UsageCounters(BaseModel):
    """Optional counters describing usage within a window.

    Every field is nullable so providers can populate only the figures
    they can actually measure -- some surface tokens but not requests,
    others requests but not cost.
    """

    requests: int | None = Field(
        default=None, description="Number of requests counted in the window."
    )
    input_tokens: int | None = Field(
        default=None, description="Total input/prompt tokens consumed."
    )
    output_tokens: int | None = Field(
        default=None, description="Total output/completion tokens generated."
    )
    total_tokens: int | None = Field(
        default=None,
        description="Sum of input and output tokens (includes cache tokens where applicable).",
    )
    cost_usd: float | None = Field(
        default=None, description="Estimated cost in USD over the window."
    )


class UsageSnapshot(BaseModel):
    """A single usage observation for one ``(provider, window, [model])``."""

    provider: InstrumentName = Field(description="Instrument this snapshot applies to.")
    model: str | None = Field(
        default=None,
        description="Model this snapshot covers, or null when the source aggregates plan-wide.",
    )
    supported: bool = Field(
        description="True when the underlying CLI exposes usage data Symphony can read."
    )
    window: UsageWindow | None = Field(
        default=None,
        description="Time window the counters apply to. Null when the provider is unsupported.",
    )
    used: UsageCounters | None = Field(
        default=None, description="Observed consumption within the window."
    )
    limit: UsageCounters | None = Field(
        default=None,
        description="Cap for the window where the provider publishes one. Null when unknown.",
    )
    remaining: UsageCounters | None = Field(
        default=None,
        description="Remaining budget within the window. Null when the cap is unknown.",
    )
    percent_remaining: float | None = Field(
        default=None,
        description="Remaining quota as a 0-100 percentage. Null when the cap is unknown.",
    )
    resets_at: str | None = Field(
        default=None,
        description="ISO-8601 timestamp when the window resets, if the provider reports it.",
    )
    as_of: str | None = Field(
        default=None,
        description="ISO-8601 timestamp when this snapshot was produced.",
    )
    source: UsageSource = Field(
        description="How the data was obtained: CLI command, session log, live stream, or not supported.",
    )
    note: str | None = Field(
        default=None,
        description="Human-readable detail about the snapshot -- fallback reasons, partial data, errors.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "provider": "claude",
                    "model": None,
                    "supported": True,
                    "window": "5h_rolling",
                    "used": {
                        "requests": 17,
                        "input_tokens": 142000,
                        "output_tokens": 39000,
                        "total_tokens": 181000,
                        "cost_usd": None,
                    },
                    "limit": None,
                    "remaining": None,
                    "percent_remaining": None,
                    "resets_at": None,
                    "as_of": "2026-05-26T14:32:00+00:00",
                    "source": "session_log",
                    "note": "Aggregated from ~/.claude/projects/**/*.jsonl over the last 5 hours.",
                }
            ]
        }
    )
