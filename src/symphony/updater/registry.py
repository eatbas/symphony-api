from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass

from ..models import InstrumentName

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(slots=True)
class CLIPackageInfo:
    provider: InstrumentName
    manager: str          # "npm", "uv", or "native"
    package: str          # npm/PyPI package name
    update_cmd: str = ""  # Native CLI update command (e.g. "claude update")


PACKAGE_REGISTRY: dict[str, CLIPackageInfo] = {
    "claude": CLIPackageInfo(InstrumentName.CLAUDE, "native", "@anthropic-ai/claude-code", "claude update"),
    "codex": CLIPackageInfo(InstrumentName.CODEX, "npm", "@openai/codex"),
    "agy": CLIPackageInfo(InstrumentName.ANTIGRAVITY, "native", "agy"),
    "kimi": CLIPackageInfo(InstrumentName.KIMI, "uv", "kimi-cli"),
    "copilot": CLIPackageInfo(InstrumentName.COPILOT, "native", "@github/copilot", "copilot update"),
    "opencode": CLIPackageInfo(InstrumentName.OPENCODE, "native", "opencode-ai", "opencode upgrade"),
}


def _parse_version(text: str) -> str | None:
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def detect_install_method(executable: str) -> str:
    """Detect how a CLI was installed from its resolved binary path.

    Returns ``"native"`` for standalone installers that live under
    ``~/.local/share/<name>/versions/``, ``"npm"`` when the resolved
    path traverses a ``node_modules`` directory, or the *fallback*
    value (defaults to ``"unknown"``) otherwise.
    """
    full_path = shutil.which(executable)
    if full_path is None:
        return "unknown"
    resolved = os.path.realpath(full_path)
    if "node_modules" in resolved:
        return "npm"
    if os.sep.join((".local", "share")) in resolved and "versions" in resolved:
        return "native"
    return "unknown"


def needs_update(current: str | None, latest: str | None) -> bool:
    """Return True when *current* is older than *latest* (semver comparison)."""
    if not current or not latest:
        return False
    try:
        return _version_tuple(current) < _version_tuple(latest)
    except (ValueError, TypeError):
        return current != latest
