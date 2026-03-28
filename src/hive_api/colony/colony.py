from __future__ import annotations

import asyncio
import logging

from ..config import AppConfig, ProviderConfig
from ..models import ModelDetail, ProviderCapability, ProviderName, DroneInfo
from ..models.enums import JobStatus
from ..providers.base import set_bash_path
from ..providers.registry import build_provider_registry
from ..shells import JobCancelledError, detect_bash_path
from .drone import Drone
from .handle import JobHandle, stopped_event

logger = logging.getLogger("hive_api.colony")

_MAX_COMPLETED_JOBS = 1000
_BASH_VERSION_TIMEOUT_SECONDS = 1.0


class Colony:
    def __init__(self, config: AppConfig):
        self.config = config
        self.shell_path = detect_bash_path(config.shell.path)
        # Let the CLI smoke-test use the same Git Bash as the drones.
        set_bash_path(self.shell_path)
        self.registry = build_provider_registry()
        self.drones: dict[tuple[ProviderName, str], list[Drone]] = {}
        self.session_models: dict[tuple[ProviderName, str], str] = {}
        self.available_providers: dict[ProviderName, bool] = {}
        self._jobs: dict[str, JobHandle] = {}
        self._ready = asyncio.Event()

    def _all_drones(self) -> list[Drone]:
        """Return a flat list of every drone across all pools."""
        return [drone for pool in self.drones.values() for drone in pool]

    async def start(self) -> None:
        all_pending: list[tuple[tuple[ProviderName, str], Drone]] = []

        for provider, provider_config in self.config.providers.items():
            if not provider_config.enabled:
                self.available_providers[provider] = False
                logger.info("Provider %s: disabled by configuration", provider.value)
                continue

            adapter = self.registry[provider]
            executable = adapter.resolve_executable(provider_config.executable)

            if not adapter.is_available(provider_config.executable):
                self.available_providers[provider] = False
                logger.warning(
                    "Provider %s: CLI '%s' not found -- skipping drone creation",
                    provider.value,
                    executable,
                )
                continue

            self.available_providers[provider] = True
            logger.info(
                "Provider %s: CLI '%s' found -- starting %d drone(s)",
                provider.value,
                executable,
                len(provider_config.models),
            )
            for model in provider_config.models:
                drone = Drone(
                    provider=provider,
                    model=model,
                    adapter=adapter,
                    executable=executable,
                    shell_path=self.shell_path,
                    default_options=provider_config.default_options,
                    session_models=self.session_models,
                    cli_timeout=provider_config.cli_timeout,
                )
                all_pending.append(((provider, model), drone))

        await asyncio.gather(*(w.start() for _, w in all_pending))
        for key, w in all_pending:
            self.drones.setdefault(key, []).append(w)

        self._ready.set()

    async def stop(self) -> None:
        await asyncio.gather(
            *(drone.stop() for drone in self._all_drones()),
            return_exceptions=True,
        )

    def get_drone(self, provider: ProviderName, model: str) -> Drone | None:
        """Return the primary drone for a (provider, model) pair.

        For backward compatibility this is synchronous and always returns the
        first drone in the pool.  Use :meth:`acquire_drone` in the request
        hot-path to benefit from concurrent drone scaling.
        """
        pool = self.drones.get((provider, model))
        if not pool:
            return None
        return pool[0]

    async def acquire_drone(self, provider: ProviderName, model: str) -> Drone | None:
        """Acquire the least-busy drone, scaling the pool lazily if needed.

        Waits for the colony to finish booting before inspecting the pool.

        1. Return an idle drone from the pool if one exists.
        2. If all drones are busy and the pool is below *concurrency*, spawn a
           new drone and return it.
        3. Otherwise return the drone with the smallest queue.
        """
        key = (provider, model)
        pool = self.drones.get(key)
        if not pool:
            return None

        # Prefer an idle drone.
        for drone in pool:
            if drone.ready and not drone.busy and drone.queue.qsize() == 0:
                return drone

        # All busy — scale up if under the concurrency limit.
        provider_config = self.config.providers.get(provider)
        max_pool = provider_config.concurrency if provider_config else 1
        if len(pool) < max_pool:
            template = pool[0]
            new_drone = Drone(
                provider=provider,
                model=model,
                adapter=template.adapter,
                executable=template.executable,
                shell_path=template.shell_backend,
                default_options=template.default_options,
                session_models=self.session_models,
                cli_timeout=template.cli_timeout,
            )
            await new_drone.start()
            pool.append(new_drone)
            logger.info(
                "Scaled drone pool %s/%s to %d (max %d)",
                provider.value,
                model,
                len(pool),
                max_pool,
            )
            return new_drone

        # Pool full — return the least-loaded drone.
        return min(pool, key=lambda d: d.queue.qsize())

    # ------------------------------------------------------------------
    # Job registry
    # ------------------------------------------------------------------

    def register_job(self, handle: JobHandle) -> None:
        """Store a job handle so it can be looked up for cancellation."""
        self._jobs[handle.job_id] = handle
        self._evict_old_jobs()

    def get_job(self, job_id: str) -> JobHandle | None:
        return self._jobs.get(job_id)

    def stop_job(self, job_id: str) -> JobHandle | None:
        """Cancel a running or queued job. Returns the handle, or None if not found."""
        handle = self._jobs.get(job_id)
        if handle is None:
            return None

        terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STOPPED}
        if handle.status in terminal:
            return handle  # Idempotent

        handle.cancelled.set()

        if handle.status == JobStatus.RUNNING:
            drone = self._find_drone_for_job(handle)
            if drone is not None:
                asyncio.create_task(drone.shell.interrupt())
            handle.status = JobStatus.STOPPED
            handle.publish_nowait(stopped_event(handle))

        elif handle.status == JobStatus.QUEUED:
            handle.status = JobStatus.STOPPED
            handle.publish_nowait(stopped_event(handle))
            if handle.result_future and not handle.result_future.done():
                handle.result_future.set_exception(
                    JobCancelledError(f"Job {job_id} cancelled while queued")
                )

        return handle

    def _find_drone_for_job(self, handle: JobHandle) -> Drone | None:
        """Find the drone currently executing the given job."""
        for drone in self._all_drones():
            if drone._current_handle is handle:
                return drone
        return None

    def _evict_old_jobs(self) -> None:
        """Remove oldest terminal jobs when the registry exceeds the limit."""
        terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STOPPED}
        terminal_ids = [jid for jid, h in self._jobs.items() if h.status in terminal]
        excess = len(terminal_ids) - _MAX_COMPLETED_JOBS
        if excess > 0:
            for jid in terminal_ids[:excess]:
                del self._jobs[jid]

    # ------------------------------------------------------------------
    # Provider capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> list[ProviderCapability]:
        capabilities: list[ProviderCapability] = []
        for provider, adapter in self.registry.items():
            provider_config: ProviderConfig = self.config.providers[provider]
            capabilities.append(
                ProviderCapability(
                    provider=provider,
                    executable=adapter.resolve_executable(provider_config.executable),
                    enabled=provider_config.enabled,
                    available=self.available_providers.get(provider, False),
                    models=provider_config.models,
                    supports_resume=adapter.supports_resume,
                    supports_streaming=adapter.supports_streaming,
                    supports_model_override=adapter.supports_model_override,
                    session_reference_format=adapter.session_reference_format,
                )
            )
        return capabilities

    def model_details(self) -> list[ModelDetail]:
        details: list[ModelDetail] = []
        seen: set[tuple[ProviderName, str]] = set()
        for drone in self._all_drones():
            key = (drone.provider, drone.model)
            if key in seen:
                continue
            seen.add(key)
            adapter = self.registry[drone.provider]
            details.append(
                ModelDetail(
                    provider=drone.provider,
                    model=drone.model,
                    ready=drone.ready,
                    busy=drone.busy,
                    supports_resume=adapter.supports_resume,
                    chat_request_example={
                        "provider": drone.provider.value,
                        "model": drone.model,
                        "workspace_path": "/path/to/your/project",
                        "mode": "new",
                        "prompt": "Your prompt here",
                        "stream": True,
                    },
                )
            )
        return details

    def drone_info(self) -> list[DroneInfo]:
        return [drone.info() for drone in self._all_drones()]

    def drones_for_provider(self, provider: ProviderName) -> list[Drone]:
        return [drone for drone in self._all_drones() if drone.provider == provider]

    async def restart_provider(self, provider: ProviderName) -> None:
        drones = self.drones_for_provider(provider)
        await asyncio.gather(*(w.stop() for w in drones), return_exceptions=True)
        await asyncio.gather(*(w.start() for w in drones))

    async def activate_provider(self, provider: ProviderName) -> bool:
        if self.available_providers.get(provider, False):
            return True

        provider_config = self.config.providers.get(provider)
        if provider_config is None or not provider_config.enabled:
            return False

        adapter = self.registry[provider]
        if not adapter.is_available(provider_config.executable):
            return False

        executable = adapter.resolve_executable(provider_config.executable)
        self.available_providers[provider] = True
        logger.info("Provider %s: CLI now available at '%s' -- creating drones", provider.value, executable)
        for model in provider_config.models:
            if (provider, model) not in self.drones:
                drone = Drone(
                    provider=provider,
                    model=model,
                    adapter=adapter,
                    executable=executable,
                    shell_path=self.shell_path,
                    default_options=provider_config.default_options,
                    session_models=self.session_models,
                    cli_timeout=provider_config.cli_timeout,
                )
                await drone.start()
                self.drones[(provider, model)] = [drone]
        return True

    def get_idle_drone(self, provider: ProviderName) -> Drone | None:
        for drone in self.drones_for_provider(provider):
            if drone.ready and not drone.busy and drone.queue.qsize() == 0:
                return drone
        return None

    async def get_bash_version(self) -> str | None:
        for drone in self._all_drones():
            if drone.ready and not drone.busy and drone.queue.qsize() == 0:
                try:
                    _, output = await drone.run_quick_command(
                        "bash --version | head -1\n__hive_exit=0",
                        timeout=_BASH_VERSION_TIMEOUT_SECONDS,
                    )
                    return output.strip() if output.strip() else None
                except Exception:
                    continue
        return None

    def health_details(self) -> list[str]:
        details: list[str] = []
        for drone in self._all_drones():
            if drone.last_error:
                details.append(f"{drone.provider.value}/{drone.model}: {drone.last_error}")
        return details
