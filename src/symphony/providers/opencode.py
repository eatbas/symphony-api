from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx

from .base import CommandSpec, ParseState, ProviderAdapter
from .options import boolean_thinking_schema, thinking_enabled
from ..models import InstrumentName
from ..usage.models import UsageCounters, UsageSnapshot

logger = logging.getLogger("symphony.providers.opencode")

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
_KEY_REQUEST_TIMEOUT = 10.0

_MISSING_KEY_NOTE = (
    "OPENROUTER_API_KEY is not set; OpenCode usage cannot be probed. "
    "Add the key to .env at the repository root."
)


def _not_supported(note: str, as_of: str) -> UsageSnapshot:
    """Build the canonical ``not_supported`` snapshot for OpenCode usage."""
    return UsageSnapshot(
        provider=InstrumentName.OPENCODE,
        supported=False,
        source="not_supported",
        note=note,
        as_of=as_of,
    )


async def _fetch_key_payload(api_key: str, as_of: str) -> dict[str, Any] | UsageSnapshot:
    """Call OpenRouter's ``/api/v1/key`` endpoint.

    Returns the ``data`` object on success or a populated
    ``not_supported`` :class:`UsageSnapshot` describing the failure
    (network error, non-2xx response, or invalid JSON).
    """
    try:
        async with httpx.AsyncClient(timeout=_KEY_REQUEST_TIMEOUT) as client:
            response = await client.get(
                OPENROUTER_KEY_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("OpenRouter /api/v1/key request failed: %s", exc)
        return _not_supported(f"OpenRouter usage probe failed: {exc}", as_of)

    if response.status_code // 100 != 2:
        return _not_supported(
            f"OpenRouter /api/v1/key returned HTTP {response.status_code}",
            as_of,
        )

    try:
        return response.json().get("data", {})
    except ValueError:
        return _not_supported(
            "OpenRouter /api/v1/key response was not valid JSON", as_of,
        )


def _percent_remaining(limit: Any, remaining: Any) -> float | None:
    """Compute remaining-quota percentage when both numbers are present."""
    if not (isinstance(limit, (int, float)) and limit > 0):
        return None
    if not isinstance(remaining, (int, float)):
        return None
    return max(0.0, min(100.0, (remaining / limit) * 100.0))


def _cost_counters(value: Any) -> UsageCounters | None:
    """Wrap a numeric USD value in a :class:`UsageCounters` shell."""
    if not isinstance(value, (int, float)):
        return None
    return UsageCounters(cost_usd=float(value))


def _build_usage_note(label: Any, is_free_tier: Any) -> str | None:
    parts = [
        f"label={label}" if label else None,
        "free tier (no credits purchased)" if is_free_tier else "paid tier",
    ]
    joined = "; ".join(part for part in parts if part)
    return joined or None


def _supported_snapshot(payload: dict[str, Any], as_of: str) -> UsageSnapshot:
    """Render the OpenRouter ``data`` object as a populated UsageSnapshot."""
    return UsageSnapshot(
        provider=InstrumentName.OPENCODE,
        supported=True,
        window="rolling",
        used=_cost_counters(payload.get("usage")),
        limit=_cost_counters(payload.get("limit")),
        remaining=_cost_counters(payload.get("limit_remaining")),
        percent_remaining=_percent_remaining(
            payload.get("limit"), payload.get("limit_remaining"),
        ),
        source="api",
        note=_build_usage_note(payload.get("label"), payload.get("is_free_tier")),
        as_of=as_of,
    )


class OpenCodeAdapter(ProviderAdapter):
    """Adapter for the OpenCode CLI (opencode-ai)."""

    name = InstrumentName.OPENCODE
    default_executable = "opencode"
    session_reference_format = "opaque-string"

    @staticmethod
    def _require_subprovider_prefix(model: str) -> None:
        """Reject model identifiers that lack an explicit sub-provider.

        OpenCode routes to multiple upstream providers (OpenRouter,
        Anthropic, OpenAI, …) and requires the model identifier to carry
        a ``<sub-provider>/<model>`` prefix.  Symphony refuses unprefixed
        identifiers up-front so a misconfigured musician fails fast
        rather than letting the CLI return a cryptic upstream error.
        ``"default"`` is the lone exception; it tells the CLI to use the
        provider's own default model.
        """
        if model == "default" or "/" in model:
            return
        raise ValueError(
            "OpenCode model identifiers must include a sub-provider "
            "prefix, e.g. 'openrouter/qwen/qwen3-coder:free'."
        )

    def build_new_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        provider_options: dict[str, Any],
    ) -> CommandSpec:
        self._require_subprovider_prefix(model)
        argv = [executable, "run", "--format", "json"]
        if thinking_enabled(provider_options):
            argv.append("--thinking")
        self._apply_model_override(argv, model)
        argv.extend(self._extra_args(provider_options))
        argv.append(prompt)
        return CommandSpec(argv=argv)

    def build_resume_command(
        self,
        *,
        executable: str,
        prompt: str,
        model: str,
        session_ref: str,
        provider_options: dict[str, Any],
    ) -> CommandSpec:
        self._require_subprovider_prefix(model)
        argv = [executable, "run", "--format", "json"]
        if thinking_enabled(provider_options):
            argv.append("--thinking")
        argv.extend(["--session", session_ref])
        self._apply_model_override(argv, model)
        argv.extend(self._extra_args(provider_options))
        argv.append(prompt)
        return CommandSpec(argv=argv, preset_session_ref=session_ref)

    def model_option_schema(self, model: str) -> list[dict[str, Any]]:
        return boolean_thinking_schema(default="enabled")

    def parse_output_line(self, line: str, state: ParseState) -> list[dict[str, Any]]:
        obj = self._parse_json_or_warn(line, state)
        if obj is None:
            return []

        events: list[dict[str, Any]] = []
        event_type = obj.get("type", "")

        # Session ID is at the top level of every JSON event.
        session_id = obj.get("sessionID") or obj.get("sessionId") or obj.get("session_id")
        if not session_id:
            part = obj.get("part", {})
            if isinstance(part, dict):
                session_id = part.get("sessionID")

        if session_id and state.session_ref != str(session_id):
            state.session_ref = str(session_id)
            events.append({"type": "provider_session", "provider_session_ref": state.session_ref})

        # Text content: type "text" with part.text
        if event_type == "text":
            part = obj.get("part", {})
            if isinstance(part, dict):
                text = part.get("text", "")
                if isinstance(text, str) and text:
                    events.extend(self._append_chunk(state, text))

        # Error handling
        if event_type == "error" or obj.get("error"):
            error_data = obj.get("error") or obj.get("part", {}).get("error")
            state.error_message = str(error_data or obj.get("message", str(obj)))

        return events

    async def get_usage(
        self,
        *,
        executable: str,
        models: list[str],
        musician_lookup: Callable[[InstrumentName], Any | None],
        run_subprocess: Callable[..., Awaitable[tuple[int, str]]],
        now: datetime,
    ) -> list[UsageSnapshot]:
        """Return OpenRouter free-tier usage for the configured API key.

        Calls ``GET https://openrouter.ai/api/v1/key`` with the bearer
        token loaded from ``OPENROUTER_API_KEY``.  When the key is
        unset, or the call fails, falls back to the ``not_supported``
        snapshot so the response shape never breaks.
        """
        as_of = now.isoformat()
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return [_not_supported(_MISSING_KEY_NOTE, as_of)]

        payload_or_snapshot = await _fetch_key_payload(api_key, as_of)
        if isinstance(payload_or_snapshot, UsageSnapshot):
            return [payload_or_snapshot]

        return [_supported_snapshot(payload_or_snapshot, as_of)]
