"""Probe dispatcher that hands an adapter the helpers it needs.

The runner is the only place that knows how to wire an adapter's
:meth:`ProviderAdapter.get_usage` to the rest of Symphony's plumbing
(idle musicians, Git-Bash-wrapped subprocess fallback, wall-clock
timeout). Keeping that wiring here means adapters can stay decoupled
from :class:`Orchestra` and the updater.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..models.enums import InstrumentName
from ..updater.version_checker import run_cmd
from .models import UsageSnapshot

if TYPE_CHECKING:
    from ..orchestra import Orchestra
    from ..providers.base import ProviderAdapter

logger = logging.getLogger("symphony.usage")

# Hard wall-clock cap per provider probe. Keeps a slow or hung CLI from
# wedging the whole monitor; a probe that times out degrades to a
# ``not_supported`` snapshot with the timeout recorded in ``note``.
PROBE_TIMEOUT_SECONDS = 10.0


async def probe_provider(
    *,
    adapter: "ProviderAdapter",
    provider: InstrumentName,
    manager: "Orchestra",
) -> list[UsageSnapshot]:
    """Invoke ``adapter.get_usage`` and capture any error in a snapshot.

    Returns an empty list when the provider is disabled in config. Returns
    a single ``not_supported`` snapshot when the probe times out or raises;
    that keeps the response shape uniform for API consumers.
    """
    provider_config = manager.config.providers.get(provider)
    if provider_config is None or not provider_config.enabled:
        return []

    executable = adapter.resolve_executable(provider_config.executable)
    models = list(provider_config.models or [])
    now = datetime.now(timezone.utc)

    def musician_lookup(p: InstrumentName):
        return manager.get_idle_musician(p)

    try:
        return await asyncio.wait_for(
            adapter.get_usage(
                executable=executable,
                models=models,
                musician_lookup=musician_lookup,
                run_subprocess=run_cmd,
                now=now,
            ),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Usage probe timed out for %s after %.1fs",
            provider.value,
            PROBE_TIMEOUT_SECONDS,
        )
        return [
            UsageSnapshot(
                provider=provider,
                supported=False,
                source="not_supported",
                note=f"probe timed out after {PROBE_TIMEOUT_SECONDS:.0f}s",
                as_of=now.isoformat(),
            )
        ]
    except Exception as exc:
        logger.warning("Usage probe failed for %s: %s", provider.value, exc)
        return [
            UsageSnapshot(
                provider=provider,
                supported=False,
                source="not_supported",
                note=f"probe error: {exc}",
                as_of=now.isoformat(),
            )
        ]
