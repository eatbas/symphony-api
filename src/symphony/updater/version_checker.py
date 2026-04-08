from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
from collections.abc import Awaitable, Callable

import httpx

from ..models import InstrumentName
from ..orchestra import Musician, Orchestra
from ..shells import windows_subprocess_kwargs
from .registry import CLIPackageInfo, _parse_version

logger = logging.getLogger("symphony.updater")
_CMD_TIMEOUT = 60

RunCmd = Callable[..., Awaitable[tuple[int, str]]]

# Set once at startup by CLIUpdater so the subprocess fallback routes
# through Git Bash instead of cmd.exe / PowerShell on Windows.
_bash_path: str | None = None


def set_bash_path(path: str) -> None:
    global _bash_path  # noqa: PLW0603
    _bash_path = path


def _run_cmd_sync(*args: str, timeout: int = _CMD_TIMEOUT) -> tuple[int, str]:
    """Blocking subprocess helper — always routes through Git Bash on Windows."""
    kwargs = windows_subprocess_kwargs()

    # On Windows, wrap the command in a Git Bash invocation so we never
    # fall back to cmd.exe / PowerShell.
    if os.name == "nt" and _bash_path:
        script = " ".join(shlex.quote(a) for a in args)
        cmd: tuple[str, ...] | tuple[str, ...] = (_bash_path, "-c", script)
    else:
        cmd = args

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            **kwargs,
        )
        return result.returncode, result.stdout.decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out: %s", " ".join(args))
        return -1, ""
    except FileNotFoundError:
        return -1, ""


async def run_cmd(*args: str, timeout: int = _CMD_TIMEOUT) -> tuple[int, str]:
    return await asyncio.to_thread(_run_cmd_sync, *args, timeout=timeout)


_PYPI_URL = "https://pypi.org/pypi/{}/json"


async def _get_latest_pypi_version(package: str) -> str | None:
    """Fetch the latest version of a package from PyPI."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_PYPI_URL.format(package))
            resp.raise_for_status()
            data = resp.json()
            return data.get("info", {}).get("version")
    except Exception:
        logger.debug("PyPI lookup failed for %s", package)
        return None


async def get_current_version(
    *,
    manager: Orchestra,
    runner: RunCmd,
    executable: str,
    provider: InstrumentName | None = None,
) -> str | None:
    if provider is not None:
        musician = manager.get_idle_musician(provider)
        if musician is not None and musician.ready:
            try:
                code, output = await musician.run_quick_command(f"{executable} --version 2>&1\n__symphony_exit=$?")
                if code == 0 and output:
                    version = _parse_version(output)
                    if version:
                        return version
            except Exception:
                logger.debug("Shell version check failed for %s, falling back", executable)

    code, output = await runner(executable, "--version")
    if code != 0:
        logger.warning("Failed to get version for %s (exit %d)", executable, code)
        return None
    return _parse_version(output)


async def get_latest_version(*, manager: Orchestra, runner: RunCmd, pkg_info: CLIPackageInfo) -> str | None:
    musician = manager.get_idle_musician(pkg_info.provider)
    if musician is not None and musician.ready:
        try:
            result = await get_latest_version_via_shell(musician=musician, pkg_info=pkg_info)
            if result:
                return result
        except Exception:
            logger.debug("Shell latest-version check failed for %s, falling back", pkg_info.package)
    return await get_latest_version_subprocess(runner=runner, pkg_info=pkg_info)


async def get_latest_version_via_shell(*, musician: Musician, pkg_info: CLIPackageInfo) -> str | None:
    # Native CLIs still have npm packages — check the registry to
    # compare versions even though the actual update uses the CLI's
    # own command.
    if pkg_info.manager in ("npm", "native"):
        code, output = await musician.run_quick_command(f"npm view {pkg_info.package} version 2>&1\n__symphony_exit=$?")
        if code == 0 and output:
            return _parse_version(output)
    elif pkg_info.manager == "uv":
        # Query PyPI for the latest published version (uv tool list
        # only reports the locally installed version, not the latest).
        pypi_version = await _get_latest_pypi_version(pkg_info.package)
        if pypi_version:
            return pypi_version
        # Fallback: parse local install list (will match current version).
        code, output = await musician.run_quick_command("uv tool list 2>&1\n__symphony_exit=$?")
        if code == 0 and output:
            for line in output.splitlines():
                if pkg_info.package in line:
                    return _parse_version(line)
    return None


async def get_latest_version_subprocess(*, runner: RunCmd, pkg_info: CLIPackageInfo) -> str | None:
    if pkg_info.manager in ("npm", "native"):
        code, output = await runner("npm", "view", pkg_info.package, "version")
        if code != 0:
            logger.warning("npm view failed for %s (exit %d)", pkg_info.package, code)
            return None
        return _parse_version(output)

    if pkg_info.manager == "uv":
        # Query PyPI for the latest published version.
        pypi_version = await _get_latest_pypi_version(pkg_info.package)
        if pypi_version:
            return pypi_version
        # Fallback: parse local install list.
        code, output = await runner("uv", "tool", "list")
        if code != 0:
            logger.warning("uv tool list failed (exit %d)", code)
            return None
        for line in output.splitlines():
            if pkg_info.package in line:
                return _parse_version(line)
        logger.warning("Package %s not found in uv tool list", pkg_info.package)
    return None
