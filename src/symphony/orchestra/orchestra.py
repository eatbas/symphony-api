from __future__ import annotations

import asyncio
import logging

from ..config import AppConfig
from ..models import ModelDetail, ProviderCapability, InstrumentName, MusicianInfo, ScoreSnapshot
from ..providers.base import set_bash_path
from ..providers.registry import build_instrument_registry
from ..score_store import ScoreStore
from ..shells import detect_bash_path
from . import pool, scores
from .capabilities import (
    build_capabilities,
    build_health_details,
    build_model_details,
    build_musician_info,
)
from .musician import Musician
from .provider_runtime import activate_provider as activate_provider_runtime
from .score import ScoreHandle

logger = logging.getLogger("symphony.orchestra")

_MAX_COMPLETED_SCORES = 1000
_BASH_VERSION_TIMEOUT_SECONDS = 1.0


class Orchestra:
    def __init__(self, config: AppConfig):
        self.config = config
        self.shell_path = detect_bash_path(config.shell.path)
        # Let the CLI smoke-test use the same Git Bash as the musicians.
        set_bash_path(self.shell_path)
        self.registry = build_instrument_registry()
        self.musicians: dict[tuple[InstrumentName, str], list[Musician]] = {}
        self.session_models: dict[tuple[InstrumentName, str], str] = {}
        self.available_providers: dict[InstrumentName, bool] = {}
        self._scores: dict[str, ScoreHandle] = {}
        self._pool_locks: dict[tuple[InstrumentName, str], asyncio.Lock] = {}
        self._ready = asyncio.Event()
        self.score_store = ScoreStore(
            root=config.storage.score_dir,
            max_terminal_scores=_MAX_COMPLETED_SCORES,
        )

    def _all_musicians(self) -> list[Musician]:
        """Return a flat list of every musician across all pools."""
        return [musician for pool_ in self.musicians.values() for musician in pool_]

    # ------------------------------------------------------------------
    # Pool lifecycle (delegated to :mod:`pool`)
    # ------------------------------------------------------------------

    def _build_musician(self, provider: InstrumentName, model: str) -> Musician | None:
        return pool.build_musician(self, provider, model)

    def _pool_lock(self, key: tuple[InstrumentName, str]) -> asyncio.Lock:
        return pool.get_pool_lock(self, key)

    async def start(self) -> None:
        await pool.start_pools(self)

    async def stop(self) -> None:
        await asyncio.gather(
            *(musician.stop() for musician in self._all_musicians()),
            return_exceptions=True,
        )

    def get_musician(self, provider: InstrumentName, model: str) -> Musician | None:
        """Return the primary musician for a (instrument, model) pair.

        For backward compatibility this is synchronous and always returns the
        first musician in the pool.  Use :meth:`acquire_musician` in the request
        hot-path to benefit from concurrent musician scaling.
        """
        bucket = self.musicians.get((provider, model))
        if not bucket:
            return None
        return bucket[0]

    async def acquire_musician(self, provider: InstrumentName, model: str) -> Musician | None:
        return await pool.acquire_musician(self, provider, model)

    # ------------------------------------------------------------------
    # Score lifecycle (delegated to :mod:`scores`)
    # ------------------------------------------------------------------

    def register_score(self, handle: ScoreHandle) -> None:
        scores.register_score(self, handle, max_scores=_MAX_COMPLETED_SCORES)

    def get_score(self, score_id: str) -> ScoreHandle | None:
        return self._scores.get(score_id)

    def get_score_snapshot(self, score_id: str) -> ScoreSnapshot | None:
        return scores.get_score_snapshot(self, score_id)

    async def persist_snapshot(self, snapshot: ScoreSnapshot) -> None:
        await scores.persist_snapshot(self, snapshot)

    async def stop_score(self, score_id: str) -> ScoreHandle | None:
        return await scores.stop_score(self, score_id)

    def restore_scores(self) -> None:
        scores.restore_scores(self, max_scores=_MAX_COMPLETED_SCORES)

    def _evict_old_scores(self) -> None:
        scores.evict_old_scores(self, max_scores=_MAX_COMPLETED_SCORES)

    def _find_musician_for_score(self, handle: ScoreHandle) -> Musician | None:
        return scores.find_musician_for_score(self, handle)

    # ------------------------------------------------------------------
    # Capabilities, health, and provider-level actions
    # ------------------------------------------------------------------

    def capabilities(self) -> list[ProviderCapability]:
        return build_capabilities(
            config=self.config,
            registry=self.registry,
            available_providers=self.available_providers,
        )

    def model_details(self) -> list[ModelDetail]:
        return build_model_details(pool=self.musicians, registry=self.registry)

    def musician_info(self) -> list[MusicianInfo]:
        return build_musician_info(self.musicians, shell_backend=self.shell_path)

    def musicians_for_provider(self, provider: InstrumentName) -> list[Musician]:
        return [musician for musician in self._all_musicians() if musician.provider == provider]

    async def restart_provider(self, provider: InstrumentName) -> None:
        musicians = self.musicians_for_provider(provider)
        await asyncio.gather(*(m.stop() for m in musicians), return_exceptions=True)
        await asyncio.gather(*(m.start() for m in musicians))

    async def activate_provider(self, provider: InstrumentName) -> bool:
        return await activate_provider_runtime(self, provider)

    def get_idle_musician(self, provider: InstrumentName) -> Musician | None:
        for musician in self.musicians_for_provider(provider):
            if musician.is_idle:
                return musician
        return None

    async def get_bash_version(self) -> str | None:
        for musician in self._all_musicians():
            if musician.is_idle:
                try:
                    _, output = await musician.run_quick_command(
                        "bash --version | head -1\n__symphony_exit=0",
                        timeout=_BASH_VERSION_TIMEOUT_SECONDS,
                    )
                    return output.strip() if output.strip() else None
                except Exception:
                    continue
        return None

    def health_details(self) -> list[str]:
        return build_health_details(self._all_musicians())
