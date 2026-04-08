from __future__ import annotations

import asyncio
import logging

from ..config import AppConfig, InstrumentConfig
from ..models import ModelDetail, ProviderCapability, InstrumentName, MusicianInfo, ScoreSnapshot
from ..models.enums import ScoreStatus, TERMINAL_STATUSES
from ..providers.base import set_bash_path
from ..providers.registry import build_instrument_registry
from ..score_store import ScoreStore
from ..shells import ScoreCancelledError, detect_bash_path
from .musician import Musician
from .score import ScoreHandle, now_rfc3339, stopped_event

logger = logging.getLogger("symphony.orchestra")

_MAX_COMPLETED_SCORES = 1000
_BASH_VERSION_TIMEOUT_SECONDS = 1.0
_RESTART_INTERRUPTION_ERROR = "Symphony restarted before the score finished"


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
        self._ready = asyncio.Event()
        self.score_store = ScoreStore(max_terminal_scores=_MAX_COMPLETED_SCORES)

    def _all_musicians(self) -> list[Musician]:
        """Return a flat list of every musician across all pools."""
        return [musician for pool in self.musicians.values() for musician in pool]

    async def start(self) -> None:
        all_pending: list[tuple[tuple[InstrumentName, str], Musician]] = []

        for instrument, instrument_config in self.config.providers.items():
            if not instrument_config.enabled:
                self.available_providers[instrument] = False
                logger.info("Instrument %s: disabled by configuration", instrument.value)
                continue

            adapter = self.registry[instrument]
            executable = adapter.resolve_executable(instrument_config.executable)

            if not adapter.is_available(instrument_config.executable):
                self.available_providers[instrument] = False
                logger.warning(
                    "Instrument %s: CLI '%s' not found -- skipping musician creation",
                    instrument.value,
                    executable,
                )
                continue

            self.available_providers[instrument] = True
            logger.info(
                "Instrument %s: CLI '%s' found -- starting %d musician(s)",
                instrument.value,
                executable,
                len(instrument_config.models),
            )
            for model in instrument_config.models:
                musician = Musician(
                    provider=instrument,
                    model=model,
                    adapter=adapter,
                    executable=executable,
                    shell_path=self.shell_path,
                    default_options=instrument_config.default_options,
                    session_models=self.session_models,
                    cli_timeout=instrument_config.cli_timeout,
                    idle_timeout=instrument_config.idle_timeout,
                )
                all_pending.append(((instrument, model), musician))

        await asyncio.gather(*(m.start() for _, m in all_pending))
        for key, m in all_pending:
            self.musicians.setdefault(key, []).append(m)

        self._ready.set()

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
        pool = self.musicians.get((provider, model))
        if not pool:
            return None
        return pool[0]

    async def acquire_musician(self, provider: InstrumentName, model: str) -> Musician | None:
        """Acquire the least-busy musician, scaling the pool lazily if needed.

        Waits for the orchestra to finish booting before inspecting the pool.

        1. Return an idle musician from the pool if one exists.
        2. If all musicians are busy and the pool is below *concurrency*, spawn a
           new musician and return it.
        3. Otherwise return the musician with the smallest queue.
        """
        key = (provider, model)
        pool = self.musicians.get(key)
        if not pool:
            return None

        # Prefer an idle musician.
        for musician in pool:
            if musician.is_idle:
                return musician

        # All busy -- scale up if under the concurrency limit.
        instrument_config = self.config.providers.get(provider)
        max_pool = instrument_config.concurrency if instrument_config else 1
        if len(pool) < max_pool:
            template = pool[0]
            new_musician = Musician(
                provider=provider,
                model=model,
                adapter=template.adapter,
                executable=template.executable,
                shell_path=template.shell_backend,
                default_options=template.default_options,
                session_models=self.session_models,
                cli_timeout=template.cli_timeout,
                idle_timeout=template.idle_timeout,
            )
            await new_musician.start()
            pool.append(new_musician)
            logger.info(
                "Scaled musician pool %s/%s to %d (max %d)",
                provider.value,
                model,
                len(pool),
                max_pool,
            )
            return new_musician

        # Pool full -- return the least-loaded musician.
        return min(pool, key=lambda m: m.queue.qsize())

    # ------------------------------------------------------------------
    # Score registry
    # ------------------------------------------------------------------

    def register_score(self, handle: ScoreHandle) -> None:
        """Store a score handle so it can be looked up for cancellation."""
        handle.set_persist_callback(self.persist_snapshot)
        self._scores[handle.score_id] = handle
        self.score_store.save(handle.snapshot())
        self._evict_old_scores()

    def get_score(self, score_id: str) -> ScoreHandle | None:
        return self._scores.get(score_id)

    def get_score_snapshot(self, score_id: str) -> ScoreSnapshot | None:
        handle = self._scores.get(score_id)
        if handle is not None:
            return handle.snapshot()
        return self.score_store.load(score_id)

    async def persist_snapshot(self, snapshot: ScoreSnapshot) -> None:
        await asyncio.to_thread(self.score_store.save, snapshot)

    async def stop_score(self, score_id: str) -> ScoreHandle | None:
        """Cancel a running or queued score. Returns the handle, or None if not found."""
        handle = self._scores.get(score_id)
        if handle is None:
            return None

        if handle.status in TERMINAL_STATUSES:
            return handle  # Idempotent

        handle.cancelled.set()

        if handle.status == ScoreStatus.RUNNING:
            musician = self._find_musician_for_score(handle)
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

    def restore_scores(self) -> None:
        """Load persisted score snapshots into memory and recover interrupted runs."""
        for snapshot in self.score_store.load_all():
            if snapshot.status in {ScoreStatus.QUEUED, ScoreStatus.RUNNING}:
                snapshot.status = ScoreStatus.FAILED
                snapshot.error = _RESTART_INTERRUPTION_ERROR
                snapshot.finished_at = now_rfc3339()
                snapshot.updated_at = snapshot.finished_at
                self.score_store.save(snapshot)

            handle = ScoreHandle.from_snapshot(snapshot)
            handle.set_persist_callback(self.persist_snapshot)
            self._scores[handle.score_id] = handle

        self._evict_old_scores()

    def _find_musician_for_score(self, handle: ScoreHandle) -> Musician | None:
        """Find the musician currently executing the given score."""
        for musician in self._all_musicians():
            if musician._current_handle is handle:
                return musician
        return None

    def _evict_old_scores(self) -> None:
        """Remove oldest terminal scores when the registry exceeds the limit."""
        terminal_ids = [sid for sid, h in self._scores.items() if h.status in TERMINAL_STATUSES]
        excess = len(terminal_ids) - _MAX_COMPLETED_SCORES
        if excess > 0:
            for sid in terminal_ids[:excess]:
                del self._scores[sid]

    # ------------------------------------------------------------------
    # Instrument capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> list[ProviderCapability]:
        capabilities: list[ProviderCapability] = []
        for instrument, adapter in self.registry.items():
            instrument_config: InstrumentConfig = self.config.providers[instrument]
            capabilities.append(
                ProviderCapability(
                    provider=instrument,
                    executable=adapter.resolve_executable(instrument_config.executable),
                    enabled=instrument_config.enabled,
                    available=self.available_providers.get(instrument, False),
                    models=instrument_config.models,
                    supports_resume=adapter.supports_resume,
                    supports_model_override=adapter.supports_model_override,
                    session_reference_format=adapter.session_reference_format,
                )
            )
        return capabilities

    def model_details(self) -> list[ModelDetail]:
        details: list[ModelDetail] = []
        seen: set[tuple[InstrumentName, str]] = set()
        for musician in self._all_musicians():
            key = (musician.provider, musician.model)
            if key in seen:
                continue
            seen.add(key)
            adapter = self.registry[musician.provider]
            details.append(
                ModelDetail(
                    provider=musician.provider,
                    model=musician.model,
                    ready=musician.ready,
                    busy=musician.busy,
                    supports_resume=adapter.supports_resume,
                    chat_request_example={
                        "provider": musician.provider.value,
                        "model": musician.model,
                        "workspace_path": "/path/to/your/project",
                        "mode": "new",
                        "prompt": "Your prompt here",
                    },
                )
            )
        return details

    def musician_info(self) -> list[MusicianInfo]:
        return [musician.info() for musician in self._all_musicians()]

    def musicians_for_provider(self, provider: InstrumentName) -> list[Musician]:
        return [musician for musician in self._all_musicians() if musician.provider == provider]

    async def restart_provider(self, provider: InstrumentName) -> None:
        musicians = self.musicians_for_provider(provider)
        await asyncio.gather(*(m.stop() for m in musicians), return_exceptions=True)
        await asyncio.gather(*(m.start() for m in musicians))

    async def activate_provider(self, provider: InstrumentName) -> bool:
        if self.available_providers.get(provider, False):
            return True

        instrument_config = self.config.providers.get(provider)
        if instrument_config is None or not instrument_config.enabled:
            return False

        adapter = self.registry[provider]
        if not adapter.is_available(instrument_config.executable):
            return False

        executable = adapter.resolve_executable(instrument_config.executable)
        self.available_providers[provider] = True
        logger.info("Instrument %s: CLI now available at '%s' -- creating musicians", provider.value, executable)
        for model in instrument_config.models:
            if (provider, model) not in self.musicians:
                musician = Musician(
                    provider=provider,
                    model=model,
                    adapter=adapter,
                    executable=executable,
                    shell_path=self.shell_path,
                    default_options=instrument_config.default_options,
                    session_models=self.session_models,
                    cli_timeout=instrument_config.cli_timeout,
                    idle_timeout=instrument_config.idle_timeout,
                )
                await musician.start()
                self.musicians[(provider, model)] = [musician]
        return True

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
        details: list[str] = []
        for musician in self._all_musicians():
            if musician.last_error:
                details.append(f"{musician.provider.value}/{musician.model}: {musician.last_error}")
        return details
