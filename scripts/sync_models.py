#!/usr/bin/env python3
"""Sync model lists from config.toml to all dependent files.

Reads ``config.toml`` (the single source of truth) and programmatically
updates constants.ts, README.md, conftest.py, and test-assertion counts.

Usage:
    python scripts/sync_models.py              # dry-run
    python scripts/sync_models.py --apply      # write changes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from symphony.discovery.discoverer import parse_config_models as parse_discovery_config_models

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
SYMPHONY = ROOT / "symphony-api"
FRONTEND = ROOT / "frontend" / "desktop"

CONFIG_PATH = SYMPHONY / "config.toml"
CONSTANTS_PATH = FRONTEND / "src" / "components" / "shared" / "constants.ts"
README_PATH = SYMPHONY / "README.md"
CONFTEST_PATH = SYMPHONY / "tests" / "conftest.py"
TEST_API_PATH = SYMPHONY / "tests" / "test_api.py"
TEST_MUSICIANS_PATH = SYMPHONY / "tests" / "test_musicians.py"
TEST_LOAD_PATH = SYMPHONY / "tests" / "ui_e2e" / "test_load.py"
TESTLAB_PATH = SYMPHONY / "src" / "symphony" / "routes" / "testlab.py"

PROVIDER_ORDER = ["gemini", "codex", "claude", "kimi", "copilot", "opencode"]
PROVIDER_CLI = {
    "gemini": "gemini",
    "codex": "codex",
    "claude": "claude",
    "kimi": "kimi",
    "copilot": "copilot",
    "opencode": "opencode",
}

# Max models per provider in the test config (keeps tests fast).
TEST_MAX_MODELS = 2


# ------------------------------------------------------------------
# Label generation
# ------------------------------------------------------------------

def generate_label(model: str) -> str:
    """Generate a human-readable label from a model ID string."""
    # Exact overrides for awkward cases.
    overrides: dict[str, str] = {
        "kimi-code/kimi-for-coding": "Kimi Code",
        "grok-code-fast-1": "Grok Code Fast",
        "glm-4.5v": "GLM 4.5V",
        "glm-4.6v": "GLM 4.6V",
        "glm-4.7-flashx": "GLM 4.7 FlashX",
    }
    if model in overrides:
        return overrides[model]

    clean = model
    # Strip trailing "-preview".
    if clean.endswith("-preview"):
        clean = clean.removesuffix("-preview")

    # Prefix mappings.
    prefix_map = {
        "glm-": "GLM ",
        "gpt-": "GPT-",
        "gemini-": "Gemini ",
        "claude-": "Claude ",
        "grok-": "Grok ",
        "kimi-": "Kimi ",
    }
    for prefix, replacement in prefix_map.items():
        if clean.startswith(prefix):
            rest = clean.removeprefix(prefix)
            # Title-case hyphenated suffixes (e.g. "flash" -> "Flash").
            parts = rest.split("-")
            labelled = []
            for part in parts:
                if not part:
                    continue
                if part[0].isdigit():
                    labelled.append(part)
                else:
                    labelled.append(part.capitalize())
            suffix = " ".join(labelled)
            # GPT uses dash-joined version: "GPT-5.4" not "GPT 5.4".
            if prefix == "gpt-":
                return f"{replacement}{suffix}"
            return f"{replacement}{suffix}"

    # Fallback: title-case with dashes to spaces.
    return model.replace("-", " ").title()


# ------------------------------------------------------------------
# A. Update constants.ts formatModelLabel()
# ------------------------------------------------------------------

def sync_constants(models_by_provider: dict[str, list[str]], *, apply: bool) -> None:
    """Add missing model labels to formatModelLabel() in constants.ts."""
    text = CONSTANTS_PATH.read_text(encoding="utf-8")

    # Extract the existing known record.
    record_pattern = r'(export function formatModelLabel\(model: string\): string \{\s*const known: Record<string, string> = \{)(.*?)(\};\s*return known\[model\] \?\? model;)'
    match = re.search(record_pattern, text, re.DOTALL)
    if not match:
        print("  [constants.ts] WARN: could not locate formatModelLabel known record")
        return

    existing_block = match.group(2)
    # Parse existing entries (handles both quoted and unquoted keys).
    existing: dict[str, str] = {}
    for entry_match in re.finditer(r'(?:"([^"]+)"|(\w+)):\s*"([^"]+)"', existing_block):
        key = entry_match.group(1) or entry_match.group(2)
        existing[key] = entry_match.group(3)

    # Collect all model IDs from config.
    all_models: set[str] = set()
    for models in models_by_provider.values():
        all_models.update(models)

    # Generate entries for missing models.
    # Also check for opencode/-prefixed variants (legacy settings format).
    added: list[str] = []
    for model in sorted(all_models):
        if model not in existing and f"opencode/{model}" not in existing:
            label = generate_label(model)
            existing[model] = label
            added.append(f'    "{model}": "{label}"')

    if not added:
        print("  [constants.ts] no new labels needed")
        return

    # Rebuild the record preserving original entries + adding new ones.
    # Keep original block intact and append new entries before the closing.
    trimmed = existing_block.rstrip()
    if trimmed.endswith(","):
        new_block = trimmed + "\n" + ",\n".join(added) + ",\n  "
    else:
        new_block = trimmed + ",\n" + ",\n".join(added) + ",\n  "

    new_text = text[:match.start(2)] + new_block + text[match.end(2):]

    if apply:
        CONSTANTS_PATH.write_text(new_text, encoding="utf-8")
    for entry in added:
        print(f"  [constants.ts] + {entry.strip()}")


# ------------------------------------------------------------------
# B. Update README.md providers table
# ------------------------------------------------------------------

def sync_readme(models_by_provider: dict[str, list[str]], *, apply: bool) -> None:
    """Regenerate the providers table in README.md."""
    text = README_PATH.read_text(encoding="utf-8")

    # Match the table block.
    table_pattern = r'(\| Provider\s+\| CLI executable \| Default models\s+\| Resume \|\n\| [-| ]+\|\n)((?:\| .+\|\n)*)'
    match = re.search(table_pattern, text)
    if not match:
        print("  [README.md] WARN: could not locate providers table")
        return

    header = match.group(1)
    rows: list[str] = []
    for provider in PROVIDER_ORDER:
        models = models_by_provider.get(provider, [])
        display = provider.capitalize()
        if provider == "opencode":
            display = "OpenCode"
        cli = PROVIDER_CLI[provider]
        model_str = ", ".join(f"`{m}`" for m in models)
        rows.append(f"| **{display}** | `{cli}` | {model_str} | Yes |\n")

    new_table = header + "".join(rows)
    new_text = text[:match.start()] + new_table + text[match.end():]

    if new_text != text:
        if apply:
            README_PATH.write_text(new_text, encoding="utf-8")
        print("  [README.md] updated providers table")
    else:
        print("  [README.md] no changes")


# ------------------------------------------------------------------
# C. Update conftest.py test config
# ------------------------------------------------------------------

def pick_test_models(models: list[str], max_n: int = TEST_MAX_MODELS) -> list[str]:
    """Pick a small subset of models for testing."""
    if len(models) <= max_n:
        return list(models)
    # First + last gives good coverage of the range.
    return [models[0], models[-1]]


def sync_conftest(models_by_provider: dict[str, list[str]], *, apply: bool) -> tuple[int, dict[str, list[str]]]:
    """Update test config model arrays in conftest.py. Returns (total_musicians, test_models_map)."""
    text = CONFTEST_PATH.read_text(encoding="utf-8")
    new_text = text
    test_models_map: dict[str, list[str]] = {}

    for provider in PROVIDER_ORDER:
        prod_models = models_by_provider.get(provider, [])
        test_models = pick_test_models(prod_models)
        test_models_map[provider] = test_models

        # Match the models line within the provider block in the f-string.
        pattern = rf'(\[providers\.{re.escape(provider)}\].*?models = )\["[^"]*"(?:,\s*"[^"]*")*\]'
        toml_array = "[" + ", ".join(f'"{m}"' for m in test_models) + "]"
        new_text = re.sub(pattern, rf'\g<1>{toml_array}', new_text, count=1, flags=re.DOTALL)

    total = sum(len(v) for v in test_models_map.values())

    if new_text != text:
        if apply:
            CONFTEST_PATH.write_text(new_text, encoding="utf-8")
        print(f"  [conftest.py] updated test models (total musicians: {total})")
        for provider, models in test_models_map.items():
            print(f"    {provider}: {models}")
    else:
        print(f"  [conftest.py] no changes (total musicians: {total})")

    return total, test_models_map


# ------------------------------------------------------------------
# D. Update hardcoded musician counts in test files
# ------------------------------------------------------------------

def sync_test_counts(total: int, *, apply: bool) -> None:
    """Update hardcoded musician count assertions across test files."""
    replacements = [
        # test_api.py: musician_count == N
        (TEST_API_PATH, r'(musician_count.*?==\s*)\d+', rf'\g<1>{total}'),
        # test_api.py: len(musicians.json()) == N
        (TEST_API_PATH, r'(len\(musicians\.json\(\)\)\s*==\s*)\d+', rf'\g<1>{total}'),
        # test_api.py: len(models) == N  # comment
        (TEST_API_PATH, r'(len\(models\)\s*==\s*)\d+(\s*#.*)?', rf'\g<1>{total}\2'),
        # test_musicians.py: len(musicians) == N
        (TEST_MUSICIANS_PATH, r'(len\(musicians\)\s*==\s*)\d+', rf'\g<1>{total}'),
        # test_load.py: to_have_text("N")
        (TEST_LOAD_PATH, r'(to_have_text\(")\d+("\))', rf'\g<1>{total}\2'),
        # test_load.py: to_have_count(N)
        (TEST_LOAD_PATH, r'(to_have_count\()\d+(\))', rf'\g<1>{total}\2'),
    ]

    for path, pattern, replacement in replacements:
        text = path.read_text(encoding="utf-8")
        new_text = re.sub(pattern, replacement, text)
        if new_text != text:
            if apply:
                path.write_text(new_text, encoding="utf-8")
            print(f"  [{path.name}] updated count to {total}")


# ------------------------------------------------------------------
# E. Validate testlab cheapest models
# ------------------------------------------------------------------

def validate_testlab(models_by_provider: dict[str, list[str]]) -> None:
    """Warn if _CHEAPEST_MODELS references models not in config."""
    text = TESTLAB_PATH.read_text(encoding="utf-8")
    for match in re.finditer(r'InstrumentName\.(\w+),\s*"([^"]+)"', text):
        provider = match.group(1).lower()
        model = match.group(2)
        config_models = models_by_provider.get(provider, [])
        if model not in config_models:
            print(f"  [testlab.py] WARN: cheapest model '{model}' for {provider} not in config.toml")


# ------------------------------------------------------------------
# F. Update kimi model assertion in UI E2E test
# ------------------------------------------------------------------

def sync_e2e_kimi(test_kimi_models: list[str], *, apply: bool) -> None:
    """Update the kimi model assertion in the E2E test."""
    text = TEST_LOAD_PATH.read_text(encoding="utf-8")
    # Match: assert "..." in models  (inside test_kimi_models method)
    kimi_model = test_kimi_models[0] if test_kimi_models else "default"
    pattern = r'(def test_kimi_models.*?assert\s+")[^"]+(("\s+in\s+models))'
    new_text = re.sub(pattern, rf'\g<1>{kimi_model}\2', text, count=1, flags=re.DOTALL)
    if new_text != text:
        if apply:
            TEST_LOAD_PATH.write_text(new_text, encoding="utf-8")
        print(f'  [test_load.py] updated kimi model assertion to "{kimi_model}"')


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync config.toml models to all dependent files")
    parser.add_argument("--apply", action="store_true", help="Write changes to files")
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        print(f"ERROR: config.toml not found at {CONFIG_PATH}")
        sys.exit(1)

    config_text = CONFIG_PATH.read_text(encoding="utf-8")
    models = parse_discovery_config_models(config_text, PROVIDER_ORDER)

    print(f"Config models ({sum(len(v) for v in models.values())} total):")
    for provider in PROVIDER_ORDER:
        provider_models = models.get(provider, [])
        print(f"  {provider}: {len(provider_models)} models")

    mode = "APPLYING" if args.apply else "DRY RUN"
    print(f"\n--- {mode} ---\n")

    sync_constants(models, apply=args.apply)
    sync_readme(models, apply=args.apply)
    total, test_models = sync_conftest(models, apply=args.apply)
    sync_test_counts(total, apply=args.apply)
    sync_e2e_kimi(test_models.get("kimi", []), apply=args.apply)
    validate_testlab(models)

    if not args.apply:
        print("\nDry run complete — pass --apply to write changes.")
    else:
        print("\nAll files synced.")


if __name__ == "__main__":
    main()
