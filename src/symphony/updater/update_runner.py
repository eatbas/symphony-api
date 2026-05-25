from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ..orchestra import Orchestra
from .registry import CLIPackageInfo, detect_install_method

logger = logging.getLogger("symphony.updater")

RunCmd = Callable[..., Awaitable[tuple[int, str]]]


def _resolve_method(pkg_info: CLIPackageInfo, executable: str | None) -> str:
    """Pick the update method for *pkg_info*.

    CLIs that ship their own update command (e.g. ``claude update``)
    always use it — it works regardless of how the CLI was installed
    (npm, standalone, brew, …). Only fall back to package-manager
    detection for CLIs without a native command."""
    if pkg_info.update_cmd:
        return "native"
    method = pkg_info.manager
    if executable:
        detected = detect_install_method(executable)
        if detected != "unknown":
            method = detected
    return method


def _shell_command(pkg_info: CLIPackageInfo, method: str) -> str | None:
    """Build the shell-string form of the update command, used when
    running through a managed musician shell. ``yes`` auto-confirms
    interactive prompts that would otherwise hang the shell."""
    if method == "native":
        return f"yes 2>/dev/null | {pkg_info.update_cmd} 2>&1\n__symphony_exit=$?"
    if method == "npm":
        return f"npm install -g {pkg_info.package}@latest 2>&1\n__symphony_exit=$?"
    if method == "uv":
        return f"uv tool upgrade {pkg_info.package} --no-cache 2>&1\n__symphony_exit=$?"
    return None


async def _run_via_subprocess(
    pkg_info: CLIPackageInfo, method: str, run_cmd: RunCmd
) -> bool:
    if method == "native":
        parts = pkg_info.update_cmd.split()
        code, output = await run_cmd(*parts, timeout=120)
    elif method == "npm":
        code, output = await run_cmd(
            "npm", "install", "-g", f"{pkg_info.package}@latest", timeout=120
        )
    elif method == "uv":
        code, output = await run_cmd(
            "uv", "tool", "upgrade", pkg_info.package, "--no-cache", timeout=120
        )
    else:
        return False

    if code != 0:
        logger.error("Update failed for %s: %s", pkg_info.package, output)
        return False
    logger.info("Successfully updated %s", pkg_info.package)
    return True


async def run_update(
    *,
    manager: Orchestra,
    run_cmd: RunCmd,
    pkg_info: CLIPackageInfo,
    executable: str | None = None,
) -> bool:
    """Execute the update for *pkg_info*.

    Tries the managed musician shell first (preserves the user's
    environment, e.g. ``nvm``), then falls back to a raw subprocess if
    no idle musician is available or the shell command fails."""
    method = _resolve_method(pkg_info, executable)
    logger.info("Updating %s (method=%s) ...", pkg_info.package, method)

    cmd_str = _shell_command(pkg_info, method)
    if cmd_str is None:
        return False

    musician = manager.get_idle_musician(pkg_info.provider)
    if musician is not None and musician.ready:
        try:
            code, output = await musician.run_quick_command(cmd_str, timeout=120)
            if code == 0:
                logger.info("Successfully updated %s", pkg_info.package)
                return True
            logger.error("Update failed for %s (shell): %s", pkg_info.package, output)
            return False
        except asyncio.TimeoutError:
            logger.warning(
                "Shell update timed out for %s, restarting musician shell",
                pkg_info.package,
            )
            await musician.stop()
            await musician.start()
            return False
        except Exception:
            logger.debug(
                "Shell update failed for %s, falling back to subprocess",
                pkg_info.package,
            )

    return await _run_via_subprocess(pkg_info, method, run_cmd)
