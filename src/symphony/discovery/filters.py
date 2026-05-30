"""Post-discovery model filters.

Each filter trims a raw discovered model list down to the
current-generation models that are useful for coding tasks.
Older generations, dated snapshots and non-coding variants
are dropped so the UI stays uncluttered.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Per-provider filters
# ---------------------------------------------------------------------------


def filter_codex(models: list[str]) -> list[str]:
    """Keep only GPT models at version 5.4 or above."""
    result: list[str] = []
    for m in models:
        match = re.match(r"gpt-(\d+)(?:\.(\d+))?", m)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2)) if match.group(2) else 0
            if (major, minor) >= (5, 4):
                result.append(m)
        else:
            result.append(m)
    return sorted(result)
