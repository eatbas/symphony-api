"""Per-provider model discovery functions.

Each function returns a list of model identifiers or ``None`` when
discovery is not possible (CLI not installed, cache missing, etc.).
Returning ``None`` signals the caller to keep the existing config.toml
models.

Discovery is fully local — no API keys or tokens required.  CLIs
installed via npm are parsed for their embedded model catalogues;
CLIs that expose a ``models`` subcommand are queried directly; and
CLIs that cache model info locally have those caches read.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from ..models import InstrumentName
from .filters import filter_codex

logger = logging.getLogger("symphony.discovery")

_DISCOVERY_CACHE_FILE = Path.home() / ".maestro" / "symphony" / ".discovery_cache.json"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read_discovery_cache() -> dict:
    """Read the discovery cache JSON.  Returns empty dict on any error."""
    try:
        return json.loads(_DISCOVERY_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_discovery_cache(cache: dict) -> None:
    """Write the discovery cache JSON."""
    try:
        _DISCOVERY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DISCOVERY_CACHE_FILE.write_text(
            json.dumps(cache, indent=2), encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("Failed to write discovery cache: %s", exc)


def _dir_mtime(path: Path) -> float:
    """Return the modification time of a directory, or 0.0 if unavailable."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_json_file(path: Path) -> dict | None:
    """Read and parse a JSON file.  Returns ``None`` on failure."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to read %s: %s", path, exc)
        return None

def _npm_package_dir(cli_name: str, package_name: str) -> Path | None:
    """Locate the npm ``node_modules/<package_name>`` dir for *cli_name*.

    Reads the CLI's launcher script (installed by ``npm i -g``) to find
    the ``node_modules`` root, then resolves the package subdirectory.
    Returns ``None`` if the CLI is not installed or is not npm-based.
    """
    exe = shutil.which(cli_name)
    if not exe:
        return None
    # npm global bins sit next to node_modules/
    npm_root = Path(exe).resolve().parent / "node_modules" / package_name
    if npm_root.is_dir():
        return npm_root
    # Windows: shutil.which may return the .CMD wrapper — try the parent.
    npm_root = Path(exe).parent / "node_modules" / package_name
    if npm_root.is_dir():
        return npm_root
    return None


def _grep_file(path: Path, pattern: str) -> list[str]:
    """Return all regex matches of *pattern* in *path*."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return re.findall(pattern, text)


# ---------------------------------------------------------------------------
# Claude — parse @anthropic-ai/claude-code for model aliases
# ---------------------------------------------------------------------------


def _discover_claude() -> list[str] | None:
    """Extract model aliases from the locally installed Claude CLI.

    The Claude CLI npm package embeds a list of short aliases
    (e.g. ``["sonnet","opus","haiku"]``) that the ``--model`` flag
    accepts.  We parse those from the installed bundle so the list
    stays in sync whenever the user updates the CLI.
    """
    pkg = _npm_package_dir("claude", "@anthropic-ai/claude-code")
    if not pkg:
        return None

    # Fast path: skip expensive rglob if the package hasn't changed.
    cache = _read_discovery_cache()
    pkg_mtime = _dir_mtime(pkg)
    claude_cache = cache.get("claude", {})
    if claude_cache.get("mtime") == pkg_mtime and claude_cache.get("models"):
        return claude_cache["models"]

    # The alias array appears as: mR9=["sonnet","opus","haiku","best","sonnet[1m]",...]
    # or similar minified variable assignment in the bundle.
    for js_file in pkg.rglob("*.js"):
        matches = _grep_file(
            js_file,
            r'=\["((?:sonnet|opus|haiku)(?:","[a-z\[\]0-9]*)*)"',
        )
        for m in matches:
            aliases = [a.strip('"') for a in m.split('","')]
            # Validate by checking we found at least 2 base aliases.
            core = [a for a in aliases if re.fullmatch(r"[a-z]+", a)]
            if len(core) >= 2:
                # Return only the core model tiers and 1M context
                # variants.  Meta-aliases like "best" (auto-select)
                # and "opusplan" (planning mode) add clutter.
                allowed = {"haiku", "opus", "opus[1m]", "sonnet"}
                all_aliases = [
                    a for a in aliases
                    if re.fullmatch(r"[a-z]+(?:\[\w+\])?", a)
                ]
                result = sorted(a for a in all_aliases if a in allowed)
                cache["claude"] = {"mtime": pkg_mtime, "models": result}
                _write_discovery_cache(cache)
                return result

    return None


# ---------------------------------------------------------------------------
# Antigravity — no programmatic discovery yet
# ---------------------------------------------------------------------------


def _discover_antigravity() -> list[str] | None:
    """Antigravity does not expose a queryable model catalogue yet.

    Models are configured in ``~/.gemini/antigravity-cli/settings.json``
    (undocumented format) and there is no ``--model`` CLI flag. Return
    ``None`` so the caller keeps the static models from config.toml.
    """
    return None


# ---------------------------------------------------------------------------
# Codex — local cache written by the Codex CLI
# ---------------------------------------------------------------------------


def _discover_codex() -> list[str] | None:
    """Read ``~/.codex/models_cache.json`` for available Codex models.

    The Codex CLI fetches its model list from the OpenAI API and caches
    it locally.  We read that cache and return models with
    ``visibility == "list"`` (the ones shown in the CLI's model picker).
    """
    data = _read_json_file(Path.home() / ".codex" / "models_cache.json")
    if data is None:
        return None

    models: list[str] = []
    for entry in data.get("models", []):
        if entry.get("visibility") == "list":
            slug = entry.get("slug", "")
            if slug:
                models.append(slug)

    return filter_codex(models) if models else None


# ---------------------------------------------------------------------------
# Kimi — parse ~/.kimi/config.toml for configured models
# ---------------------------------------------------------------------------


def _discover_kimi() -> list[str] | None:
    """Read model keys from ``~/.kimi/config.toml``.

    The Kimi CLI stores configured models as TOML table headers of the
    form ``[models."<provider>/<model>"]``.
    """
    config_path = Path.home() / ".kimi" / "config.toml"
    if not config_path.exists():
        return None
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None

    models = [m.group(1) for m in re.finditer(r'\[models\."([^"]+)"\]', text)]
    return sorted(models) if models else None


# ---------------------------------------------------------------------------
# Registry — provider → discovery function
# ---------------------------------------------------------------------------

DISCOVERERS: dict[InstrumentName, callable] = {
    InstrumentName.CLAUDE: _discover_claude,
    InstrumentName.ANTIGRAVITY: _discover_antigravity,
    InstrumentName.CODEX: _discover_codex,
    InstrumentName.KIMI: _discover_kimi,
}
