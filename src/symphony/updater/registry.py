from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

from ..models import InstrumentName

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(slots=True)
class CLIPackageInfo:
    provider: InstrumentName
    manager: str           # "npm", "uv", or "native"
    package: str           # npm/PyPI package name
    update_cmd: str = ""   # Native CLI update command (e.g. "claude update")
    manifest_url: str = "" # Release-manifest URL template for CLIs not on
    #                        npm/PyPI; ``{platform}`` is resolved at runtime.


# Antigravity is distributed as a flat native binary (not on npm/PyPI). Its
# install script and `agy update` both pull from this auto-updater service,
# which serves a per-platform JSON manifest exposing the latest ``version``.
_ANTIGRAVITY_MANIFEST_URL = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app"
    "/manifests/{platform}.json"
)


PACKAGE_REGISTRY: dict[str, CLIPackageInfo] = {
    "claude": CLIPackageInfo(InstrumentName.CLAUDE, "native", "@anthropic-ai/claude-code", "claude update"),
    "codex": CLIPackageInfo(InstrumentName.CODEX, "npm", "@openai/codex"),
    "agy": CLIPackageInfo(
        InstrumentName.ANTIGRAVITY,
        "native",
        "agy",
        # Antigravity ships a native ``agy update`` subcommand — the curl
        # install script is not an upgrade path (it no-ops when the binary
        # already exists, and its `uname -s` check rejects Windows outright).
        "agy update",
        # ``agy`` is not published to npm (``npm view agy`` resolves to an
        # unrelated 0.0.0 placeholder), so the latest version is read from
        # the auto-updater platform manifest instead.
        manifest_url=_ANTIGRAVITY_MANIFEST_URL,
    ),
    "kimi": CLIPackageInfo(InstrumentName.KIMI, "uv", "kimi-cli"),
    "opencode": CLIPackageInfo(InstrumentName.OPENCODE, "native", "opencode-ai", "opencode upgrade"),
}


def _parse_version(text: str) -> str | None:
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def _coerce_version(value: str) -> Version | None:
    """Parse a version string into a comparable :class:`Version`.

    Tolerates a leading ``v`` (release manifests often write ``v1.2.3`` while
    the CLI prints ``1.2.3``) and returns ``None`` for anything PEP 440 cannot
    parse, so callers can fall back to a conservative comparison.
    """
    try:
        return Version(value.strip().lstrip("vV"))
    except InvalidVersion:
        return None


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
    """Return True when *current* is older than *latest*.

    Uses :mod:`packaging.version` so pre-release, build-metadata, and
    N-segment versions compare correctly.  When either value is not a
    recognisable version (e.g. a build hash) it falls back to a conservative
    string inequality rather than risking a spurious update.
    """
    if not current or not latest:
        return False
    current_version = _coerce_version(current)
    latest_version = _coerce_version(latest)
    if current_version is None or latest_version is None:
        return current != latest
    return current_version < latest_version
