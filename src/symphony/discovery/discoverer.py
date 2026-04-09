"""Automatic model discovery — runs on every Symphony startup.

For each provider, attempts to discover available models by querying the
installed CLI, reading local caches, or calling the provider's API using
locally-stored credentials.

Discovery functions live in ``providers.py``.  When a provider gains a
new discovery mechanism, update or add a function there and register it
in ``DISCOVERERS``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tomllib
from pathlib import Path
from typing import Iterable

from .providers import DISCOVERERS

logger = logging.getLogger("symphony.discovery")


# ---------------------------------------------------------------------------
# config.toml update helpers
# ---------------------------------------------------------------------------


def _find_matching_bracket(text: str, opening_index: int) -> int | None:
    """Return the closing ``]`` index for an array starting at *opening_index*."""
    depth = 0
    in_string: str | None = None
    in_comment = False
    escaped = False

    for index in range(opening_index, len(text)):
        char = text[index]

        if in_comment:
            if char == "\n":
                in_comment = False
            continue

        if in_string is not None:
            if in_string == '"':
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = None
            elif char == in_string:
                in_string = None
            continue

        if char == "#":
            in_comment = True
            continue
        if char in {'"', "'"}:
            in_string = char
            continue
        if char == "[":
            depth += 1
            continue
        if char == "]":
            depth -= 1
            if depth == 0:
                return index

    return None


def _locate_provider_models_array(text: str, provider: str) -> tuple[int, int] | None:
    """Return the inclusive bounds of a provider's ``models = [...]`` array."""
    section_header = re.search(rf"(?m)^\[providers\.{re.escape(provider)}\]\s*$", text)
    if section_header is None:
        return None

    section_start = section_header.end()
    next_section = re.search(r"(?m)^\[[^\n]+\]\s*$", text[section_start:])
    section_end = section_start + next_section.start() if next_section else len(text)
    section_text = text[section_start:section_end]

    models_line = re.search(r"(?m)^models\s*=\s*", section_text)
    if models_line is None:
        return None

    array_start = section_start + models_line.end()
    while array_start < len(text) and text[array_start].isspace():
        array_start += 1
    if array_start >= len(text) or text[array_start] != "[":
        return None

    array_end = _find_matching_bracket(text, array_start)
    if array_end is None:
        return None
    return array_start, array_end


def parse_models_from_toml(text: str, provider: str) -> list[str]:
    """Extract the models array for *provider* from raw TOML text."""
    bounds = _locate_provider_models_array(text, provider)
    if bounds is None:
        return []

    array_start, array_end = bounds
    snippet = f"models = {text[array_start:array_end + 1]}\n"
    try:
        parsed = tomllib.loads(snippet)
    except tomllib.TOMLDecodeError:
        return []

    models = parsed.get("models", [])
    if not isinstance(models, list):
        return []
    return [str(item) for item in models if str(item).strip()]


def _format_models_toml(models: list[str]) -> str:
    """Format a models list as a TOML array string."""
    encoded = [json.dumps(model) for model in models]
    if len(models) <= 3:
        return "[" + ", ".join(encoded) + "]"
    lines = ["["]
    for model in encoded:
        lines.append(f"  {model},")
    lines.append("]")
    return "\n".join(lines)


def parse_config_models(text: str, providers: Iterable[str]) -> dict[str, list[str]]:
    """Extract model arrays for each provider listed in *providers*."""
    return {provider: parse_models_from_toml(text, provider) for provider in providers}


def replace_models_in_toml(
    text: str, provider: str, new_models: list[str],
) -> str:
    """Replace the models array for *provider* in raw TOML text."""
    bounds = _locate_provider_models_array(text, provider)
    if bounds is None:
        return text

    array_start, array_end = bounds
    replacement = _format_models_toml(new_models)
    return text[:array_start] + replacement + text[array_end + 1:]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_startup_discovery(config_path: Path) -> bool:
    """Discover models for all providers and update ``config.toml``.

    Called once during ``create_app()`` before the Orchestra is built.
    Returns ``True`` if config.toml was modified.

    Providers without a registered discovery function are left unchanged.
    If discovery returns ``None`` (CLI missing or errored), the existing
    config models are preserved.

    Set ``SYMPHONY_SKIP_DISCOVERY=1`` to disable (used in tests).
    """
    if os.environ.get("SYMPHONY_SKIP_DISCOVERY"):
        return False

    if not config_path.exists():
        return False

    text = config_path.read_text(encoding="utf-8")
    updated_text = text
    changed = False

    for provider, discover_fn in DISCOVERERS.items():
        provider_name = provider.value
        try:
            discovered = discover_fn()
        except Exception:
            logger.exception("Discovery failed for %s", provider_name)
            continue

        if discovered is None:
            logger.debug(
                "No discovery result for %s — keeping config as-is",
                provider_name,
            )
            continue

        current = parse_models_from_toml(updated_text, provider_name)
        if set(discovered) != set(current):
            added = set(discovered) - set(current)
            removed = set(current) - set(discovered)
            logger.info(
                "Model update for %s: +%s -%s",
                provider_name,
                list(added) if added else "none",
                list(removed) if removed else "none",
            )
            updated_text = replace_models_in_toml(
                updated_text, provider_name, discovered,
            )
            changed = True
        else:
            logger.debug("Models for %s unchanged", provider_name)

    if changed:
        config_path.write_text(updated_text, encoding="utf-8")
        logger.info("config.toml updated with discovered models")

    return changed


def discover_provider(provider: InstrumentName, config_path: Path) -> bool:
    """Discover models for a single provider and update ``config.toml``.

    Intended to be called after a CLI update so that newly available
    models are picked up without a full restart.

    Returns ``True`` if config.toml was modified.
    """
    discover_fn = DISCOVERERS.get(provider)
    if discover_fn is None:
        return False

    if not config_path.exists():
        return False

    try:
        discovered = discover_fn()
    except Exception:
        logger.exception("Post-update discovery failed for %s", provider.value)
        return False

    if discovered is None:
        logger.debug(
            "No discovery result for %s after update — keeping config as-is",
            provider.value,
        )
        return False

    text = config_path.read_text(encoding="utf-8")
    current = parse_models_from_toml(text, provider.value)

    if set(discovered) == set(current):
        logger.debug("Models for %s unchanged after update", provider.value)
        return False

    added = set(discovered) - set(current)
    removed = set(current) - set(discovered)
    logger.info(
        "Post-update model change for %s: +%s -%s",
        provider.value,
        sorted(added) if added else "none",
        sorted(removed) if removed else "none",
    )

    updated_text = replace_models_in_toml(text, provider.value, discovered)
    config_path.write_text(updated_text, encoding="utf-8")
    return True
