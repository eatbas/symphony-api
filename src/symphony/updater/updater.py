from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..config import UpdaterConfig
from ..discovery import discover_provider
from ..models import CLIVersionStatus, InstrumentName
from ..orchestra import Orchestra, refresh_provider_models
from .registry import CLIPackageInfo, PACKAGE_REGISTRY, detect_install_method, needs_update as _needs_update
from .single_provider import update_single_provider_impl
from .version_checker import (
    get_current_version,
    get_latest_version,
    run_cmd,
    set_bash_path,
)

logger = logging.getLogger("symphony.updater")


class CLIUpdater:
    """Periodically checks CLI versions and auto-updates when musicians are idle."""

    def __init__(self, manager: Orchestra, config: UpdaterConfig) -> None:
        self.manager = manager
        self.config = config
        self._last_results: list[CLIVersionStatus] = []
        self._task: asyncio.Task[None] | None = None
        self._discovery_lock = asyncio.Lock()
        # Ensure the subprocess fallback also uses Git Bash.
        set_bash_path(manager.shell_path)

    async def _run_cmd(self, *args: str, timeout: int = 60) -> tuple[int, str]:
        return await run_cmd(*args, timeout=timeout)

    async def get_current_version(self, executable: str, provider: InstrumentName | None = None) -> str | None:
        return await get_current_version(
            manager=self.manager,
            runner=self._run_cmd,
            executable=executable,
            provider=provider,
        )

    async def get_latest_version(self, pkg_info: CLIPackageInfo) -> str | None:
        return await get_latest_version(manager=self.manager, runner=self._run_cmd, pkg_info=pkg_info)

    def is_provider_idle(self, provider: InstrumentName) -> bool:
        musicians = self.manager.musicians_for_provider(provider)
        if not musicians:
            return True
        return all(not m.busy and m.queue.qsize() == 0 for m in musicians)

    async def update_cli(self, pkg_info: CLIPackageInfo, *, executable: str | None = None) -> bool:
        # CLIs with a native update command always use that — it works
        # regardless of how the CLI was installed (npm, standalone, etc.).
        # Only fall back to package-manager detection for CLIs without one.
        if pkg_info.update_cmd:
            method = "native"
        else:
            method = pkg_info.manager
            if executable:
                detected = detect_install_method(executable)
                if detected != "unknown":
                    method = detected

        logger.info("Updating %s (method=%s) ...", pkg_info.package, method)

        if method == "native":
            # Pipe ``yes`` to auto-confirm interactive prompts (e.g.
            # ``opencode upgrade``) that would otherwise hang the shell.
            cmd_str = f"yes 2>/dev/null | {pkg_info.update_cmd} 2>&1\n__symphony_exit=$?"
        elif method == "npm":
            cmd_str = f"npm install -g {pkg_info.package}@latest 2>&1\n__symphony_exit=$?"
        elif method == "uv":
            cmd_str = f"uv tool upgrade {pkg_info.package} --no-cache 2>&1\n__symphony_exit=$?"
        else:
            return False

        musician = self.manager.get_idle_musician(pkg_info.provider)
        if musician is not None and musician.ready:
            try:
                code, output = await musician.run_quick_command(cmd_str, timeout=120)
                if code == 0:
                    logger.info("Successfully updated %s", pkg_info.package)
                    return True
                logger.error("Update failed for %s (shell): %s", pkg_info.package, output)
                return False
            except asyncio.TimeoutError:
                logger.warning("Shell update timed out for %s, restarting musician shell", pkg_info.package)
                await musician.stop()
                await musician.start()
                return False
            except Exception:
                logger.debug("Shell update failed for %s, falling back to subprocess", pkg_info.package)

        if method == "native":
            # Split the update_cmd into executable and args.
            parts = pkg_info.update_cmd.split()
            code, output = await self._run_cmd(*parts, timeout=120)
        elif method == "npm":
            code, output = await self._run_cmd("npm", "install", "-g", f"{pkg_info.package}@latest", timeout=120)
        elif method == "uv":
            code, output = await self._run_cmd("uv", "tool", "upgrade", pkg_info.package, "--no-cache", timeout=120)
        else:
            return False

        if code != 0:
            logger.error("Update failed for %s: %s", pkg_info.package, output)
            return False

        logger.info("Successfully updated %s", pkg_info.package)
        return True

    async def _rediscover_models(self, provider: InstrumentName) -> None:
        """Run model discovery for *provider* after a successful CLI update.

        Serialised via ``_discovery_lock`` to prevent concurrent
        config.toml writes when multiple providers update in parallel.
        """
        async with self._discovery_lock:
            config_path = self.manager.config.config_path
            changed = await asyncio.to_thread(discover_provider, provider, config_path)
        if changed:
            refreshed = await refresh_provider_models(self.manager, provider)
            if refreshed:
                logger.info("Models refreshed for %s after CLI update", provider.value)

    def _next_check_at(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(hours=self.config.interval_hours)).isoformat()

    def _build_status(
        self,
        *,
        provider: InstrumentName,
        executable: str | None,
        current_version: str | None,
        latest_version: str | None,
        needs_update: bool,
        now: str,
        next_check: str,
        last_updated: str | None = None,
        skip_reason: str | None = None,
    ) -> CLIVersionStatus:
        return CLIVersionStatus(
            provider=provider,
            executable=executable,
            current_version=current_version,
            latest_version=latest_version,
            needs_update=needs_update,
            last_checked=now,
            next_check_at=next_check,
            auto_update=self.config.auto_update,
            last_updated=last_updated,
            update_skipped_reason=skip_reason,
        )

    async def _check_single_provider(self, provider: InstrumentName, now: str, next_check: str) -> CLIVersionStatus | None:
        provider_config = self.manager.config.providers.get(provider)
        if provider_config is None or not provider_config.enabled:
            return None

        adapter = self.manager.registry.get(provider)
        if adapter is None:
            return None

        executable = adapter.resolve_executable(provider_config.executable)
        pkg_info = PACKAGE_REGISTRY.get(adapter.default_executable)
        if pkg_info is None:
            return None

        current, latest = await asyncio.gather(
            self.get_current_version(executable or adapter.default_executable, provider),
            self.get_latest_version(pkg_info),
        )

        update_needed = _needs_update(current, latest)
        skip_reason: str | None = None
        last_updated: str | None = None

        if update_needed and self.config.auto_update:
            if self.is_provider_idle(provider):
                success = await self.update_cli(pkg_info, executable=executable or adapter.default_executable)
                if success:
                    await self.manager.restart_provider(provider)
                    await self.manager.activate_provider(provider)
                    await self._rediscover_models(provider)
                    last_updated = datetime.now(timezone.utc).isoformat()
                    current = await self.get_current_version(executable or adapter.default_executable, provider)
                    update_needed = _needs_update(current, latest)
                else:
                    skip_reason = "update command failed"
            else:
                skip_reason = "musicians busy"
                logger.warning("Skipping update for %s: musicians are busy", provider.value)
        elif update_needed and not self.config.auto_update:
            skip_reason = "auto_update disabled"

        return self._build_status(
            provider=provider,
            executable=executable,
            current_version=current,
            latest_version=latest,
            needs_update=update_needed,
            now=now,
            next_check=next_check,
            last_updated=last_updated,
            skip_reason=skip_reason,
        )

    async def check_single_provider(self, provider: InstrumentName) -> CLIVersionStatus | None:
        now = datetime.now(timezone.utc).isoformat()
        next_check = self._next_check_at()
        result = await self._check_single_provider(provider, now, next_check)
        if result is not None:
            self._cache_single(result)
        return result

    async def check_and_update_all(self) -> list[CLIVersionStatus]:
        now = datetime.now(timezone.utc).isoformat()
        next_check = self._next_check_at()

        providers = [
            p
            for p in self.manager.config.providers
            if self.manager.config.providers[p].enabled
            and self.manager.available_providers.get(p, False)
        ]
        check_results = await asyncio.gather(*(self._check_single_provider(p, now, next_check) for p in providers))
        results = [r for r in check_results if r is not None]
        self._last_results = results
        return results

    async def update_single_provider(self, provider: InstrumentName) -> CLIVersionStatus:
        return await update_single_provider_impl(self, provider)

    def _cache_single(self, result: CLIVersionStatus) -> None:
        self._last_results = [result if r.provider == result.provider else r for r in self._last_results]
        if not any(r.provider == result.provider for r in self._last_results):
            self._last_results.append(result)

    async def _periodic_loop(self) -> None:
        while True:
            try:
                results = await self.check_and_update_all()
                for status in results:
                    if status.needs_update:
                        logger.info(
                            "%s: %s -> %s (update %s)",
                            status.provider.value,
                            status.current_version,
                            status.latest_version,
                            status.update_skipped_reason or "applied",
                        )
                    else:
                        logger.info("%s: %s (up to date)", status.provider.value, status.current_version)
            except Exception:
                logger.exception("Error during periodic CLI version check")
            await asyncio.sleep(self.config.interval_hours * 3600)

    def start(self) -> None:
        if not self.config.enabled:
            logger.info("CLI updater is disabled")
            return
        if self._task is None:
            logger.info(
                "Starting CLI updater (interval=%.1fh, auto_update=%s)",
                self.config.interval_hours,
                self.config.auto_update,
            )
            self._task = asyncio.create_task(self._periodic_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def last_results(self) -> list[CLIVersionStatus]:
        return list(self._last_results)
