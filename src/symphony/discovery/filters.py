"""Post-discovery model filters.

Each filter trims a raw discovered model list down to the
current-generation models that are useful for coding tasks.
Older generations, dated snapshots and non-coding variants
are dropped so the UI stays uncluttered.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DATED_SNAPSHOT_RE = re.compile(r"-\d{4}-\d{2}-\d{2}")
_NON_CODING_KEYWORDS = frozenset({"audio", "realtime", "search", "vision"})

_CLAUDE_TIER_RE = re.compile(r"claude-(sonnet|haiku|opus)-(\d+(?:\.\d+)?)")


def _latest_per_tier(
    models: list[str],
    pattern: re.Pattern[str],
    *,
    tier_group: int = 1,
    version_group: int = 2,
) -> list[str]:
    """Keep only the highest version per tier.

    *pattern* must capture a tier name at *tier_group* and a numeric
    version at *version_group*.  Models whose version equals the max
    for their tier are kept; others are discarded.
    """
    best: dict[str, float] = {}
    for m in models:
        match = pattern.match(m)
        if match:
            tier = match.group(tier_group)
            ver = float(match.group(version_group))
            best[tier] = max(best.get(tier, 0.0), ver)

    result: list[str] = []
    for m in models:
        match = pattern.match(m)
        if match:
            tier = match.group(tier_group)
            ver = float(match.group(version_group))
            if ver == best.get(tier):
                result.append(m)
    return result


def _top_minor_versions(
    models: list[str],
    prefix: str,
    *,
    keep: int = 2,
) -> list[str]:
    """From ``prefix-Major.Minor[-suffix]`` models, keep the top *keep*
    ``(major, minor)`` groups.  Models that don't match the prefix are
    passed through untouched.
    """
    ver_re = re.compile(rf"^{re.escape(prefix)}-(\d+)(?:\.(\d+))?")
    versions: set[tuple[int, int]] = set()
    for m in models:
        match = ver_re.match(m)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2)) if match.group(2) else 0
            versions.add((major, minor))

    if not versions:
        return models

    cutoff = sorted(versions, reverse=True)[:keep]
    min_ver = cutoff[-1]

    result: list[str] = []
    for m in models:
        match = ver_re.match(m)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2)) if match.group(2) else 0
            if (major, minor) >= min_ver:
                result.append(m)
        else:
            result.append(m)
    return result


# ---------------------------------------------------------------------------
# Per-provider filters
# ---------------------------------------------------------------------------


def filter_copilot(models: list[str]) -> list[str]:
    """Keep only current-generation coding models from Copilot.

    * Drops dated snapshot variants (``-YYYY-MM-DD`` suffix).
    * Drops non-coding variants (audio, realtime, search, vision).
    * Drops the entire legacy GPT-4 / 4o / 4.1 / 4.5 family.
    * For Claude models, keeps only the latest version per tier.
    * For GPT-5 models, keeps the top two minor-version groups.
    """
    claude: list[str] = []
    rest: list[str] = []

    for m in models:
        if _DATED_SNAPSHOT_RE.search(m):
            continue
        if any(kw in m for kw in _NON_CODING_KEYWORDS):
            continue
        if m.startswith("claude-"):
            claude.append(m)
        elif m.startswith("gpt-4"):
            continue
        else:
            rest.append(m)

    latest_claude = _latest_per_tier(claude, _CLAUDE_TIER_RE)
    combined = latest_claude + rest
    combined = _top_minor_versions(combined, "gpt", keep=2)
    return sorted(combined)


def filter_codex(models: list[str]) -> list[str]:
    """Keep only the two latest minor-version generations of Codex models."""
    return sorted(_top_minor_versions(models, "gpt", keep=2))


def filter_opencode(models: list[str]) -> list[str]:
    """Keep only the latest major GLM generation."""
    max_major = 0
    for m in models:
        match = re.match(r"glm-(\d+)", m)
        if match:
            max_major = max(max_major, int(match.group(1)))

    if max_major == 0:
        return models

    return sorted(
        m for m in models if re.match(rf"glm-{max_major}", m)
    )
