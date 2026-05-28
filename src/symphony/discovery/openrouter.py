"""Live OpenRouter free-tier discovery for the OpenCode adapter.

Fetches OpenRouter's public catalogue, keeps text-only ``:free`` models,
ranks them by context length (descending) with the model id as a stable
tie-breaker, caps the list at ``MAX_FREE_MODELS``, and returns each id
prefixed with ``openrouter/`` so the OpenCode CLI knows which upstream
sub-provider to use.

The selection rebuilds end-to-end on every refresh — there is no
allowlist file.  Models that disappear from the free tier between
refreshes are dropped automatically; new free models that rank into the
top slot appear automatically.  See ``plan_request_claude.md`` for the
design rationale.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("symphony.discovery.openrouter")

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MAX_FREE_MODELS = 10
_REQUEST_TIMEOUT = 15.0
_CACHE_TTL_SECONDS = 60 * 30  # Thirty minutes; the periodic refresh forces a re-fetch anyway.


class OpenRouterDiscoveryError(RuntimeError):
    """Raised when the OpenRouter catalogue endpoint returns a non-2xx response."""


class _DiscoveryCache:
    """Tiny single-slot TTL cache for the OpenRouter selection.

    Wraps what used to be a module-level dict so the lifetime semantics
    are explicit and easier to reason about.  The cache stores at most
    one ``(stored_at, selection)`` pair under the single sentinel key
    ``"selection"``.
    """

    __slots__ = ("_stored_at", "_selection")

    def __init__(self) -> None:
        self._stored_at: float | None = None
        self._selection: list[str] | None = None

    def get(self, now: float, ttl: float) -> list[str] | None:
        """Return a defensive copy of the cached selection if still fresh."""
        if self._stored_at is None or self._selection is None:
            return None
        if now - self._stored_at >= ttl:
            return None
        return list(self._selection)

    def set(self, now: float, selection: list[str]) -> None:
        """Store the selection with the current timestamp."""
        self._stored_at = now
        self._selection = list(selection)

    def clear(self) -> None:
        """Drop any cached entry so the next ``get`` returns ``None``."""
        self._stored_at = None
        self._selection = None


_cache = _DiscoveryCache()


def invalidate_cache() -> None:
    """Drop any cached selection so the next discover call hits the network."""
    _cache.clear()


async def fetch_openrouter_catalogue(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Return the raw ``data`` array from ``GET /api/v1/models``.

    Calls the unfiltered catalogue endpoint — passing
    ``?category=programming`` was rejected during planning because it
    returned only 20 entries with 2 free models, which is too narrow for
    a top-ten cap.
    """
    response = await client.get(OPENROUTER_MODELS_URL, timeout=_REQUEST_TIMEOUT)
    if response.status_code // 100 != 2:
        raise OpenRouterDiscoveryError(
            f"OpenRouter /api/v1/models returned HTTP {response.status_code}"
        )
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, list):
        raise OpenRouterDiscoveryError("OpenRouter response missing 'data' array")
    return data


def is_free_text_model(model: dict[str, Any]) -> bool:
    """Return True for text-output models whose free-tier price is zero.

    Three conjunctive checks:

    * ``id`` ends in ``:free`` — OpenRouter's published convention for
      free-tier variants.  Primary signal.
    * ``pricing.prompt`` and ``pricing.completion`` are both the literal
      string ``"0"``.  Guards against rebranded variants that become
      metered.
    * ``architecture.output_modalities`` is exactly ``["text"]``.  Drops
      vision-only, audio, embeddings, and other non-text strays without
      maintaining a keyword blocklist.
    """
    model_id = model.get("id", "")
    if not isinstance(model_id, str) or not model_id.endswith(":free"):
        return False

    pricing = model.get("pricing")
    if not isinstance(pricing, dict):
        return False
    if pricing.get("prompt") != "0" or pricing.get("completion") != "0":
        return False

    architecture = model.get("architecture")
    if not isinstance(architecture, dict):
        return False
    if architecture.get("output_modalities") != ["text"]:
        return False

    return True


def rank_and_cap(models: list[dict[str, Any]]) -> list[str]:
    """Sort models by ``(-context_length, id)`` and return the top N prefixed ids."""
    def sort_key(model: dict[str, Any]) -> tuple[int, str]:
        ctx = model.get("context_length")
        ctx_int = int(ctx) if isinstance(ctx, (int, float)) and ctx else 0
        return (-ctx_int, str(model.get("id", "")))

    ordered = sorted(models, key=sort_key)
    return [f"openrouter/{m['id']}" for m in ordered[:MAX_FREE_MODELS]]


async def discover_openrouter_free_models() -> list[str] | None:
    """End-to-end pipeline: fetch → filter → rank → cap → prefix.

    Returns ``None`` on any network or parse failure so the existing
    discovery contract holds (callers keep their current model list when
    discovery cannot run).
    """
    now = time.monotonic()
    cached = _cache.get(now, _CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient() as client:
            catalogue = await fetch_openrouter_catalogue(client)
    except (httpx.HTTPError, OpenRouterDiscoveryError, ValueError) as exc:
        logger.warning("OpenRouter discovery failed: %s", exc)
        return None

    free = [m for m in catalogue if is_free_text_model(m)]
    selection = rank_and_cap(free)
    _cache.set(time.monotonic(), selection)
    return selection
