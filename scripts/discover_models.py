#!/usr/bin/env python3
"""Discover available CLI models and update config.toml.

This script wraps the ``symphony.discovery`` module for manual use.
The same discovery runs automatically on every Symphony startup.

All six providers (Claude, Gemini, Codex, Copilot, Kimi, OpenCode)
have programmatic discovery — via CLI subcommands, local caches,
or provider API calls using locally-stored credentials.

Usage:
    python scripts/discover_models.py              # dry-run, print diff
    python scripts/discover_models.py --apply      # update config.toml
    python scripts/discover_models.py --apply --sync  # update config + sync dependents
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Add the src directory to the path so we can import symphony modules.
_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from symphony.discovery.discoverer import (  # noqa: E402
    parse_models_from_toml,
    replace_models_in_toml,
)
from symphony.discovery.providers import DISCOVERERS  # noqa: E402
from symphony.models import InstrumentName  # noqa: E402

PROVIDER_ORDER = ["gemini", "codex", "claude", "kimi", "copilot", "opencode"]
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover CLI models and update config.toml")
    parser.add_argument("--apply", action="store_true", help="Write changes to config.toml")
    parser.add_argument("--sync", action="store_true", help="Run sync_models.py after updating config")
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        print(f"ERROR: config.toml not found at {CONFIG_PATH}")
        sys.exit(1)

    config_text = CONFIG_PATH.read_text(encoding="utf-8")
    updated_text = config_text
    any_changes = False

    print("Model discovery results:\n")
    for provider_name in PROVIDER_ORDER:
        try:
            provider = InstrumentName(provider_name)
        except ValueError:
            continue

        current = parse_models_from_toml(config_text, provider_name)
        discover_fn = DISCOVERERS.get(provider)

        if discover_fn is None:
            print(f"  {provider_name}: no discovery function registered — keeping config ({len(current)} models)")
            continue

        discovered = discover_fn()
        if discovered is None:
            print(f"  {provider_name}: discovery failed — keeping config ({len(current)} models)")
            continue

        added = [m for m in discovered if m not in current]
        removed = [m for m in current if m not in discovered]

        if added or removed:
            any_changes = True
            print(f"  {provider_name}:")
            for m in added:
                print(f"    + {m}")
            for m in removed:
                print(f"    - {m}")
            updated_text = replace_models_in_toml(updated_text, provider_name, discovered)
        else:
            print(f"  {provider_name}: up to date ({len(current)} models)")

    if not any_changes:
        print("\nAll providers up to date.")
        return

    if args.apply:
        CONFIG_PATH.write_text(updated_text, encoding="utf-8")
        print(f"\nconfig.toml updated at {CONFIG_PATH}")

        if args.sync:
            sync_script = Path(__file__).resolve().parent / "sync_models.py"
            print("\nRunning sync_models.py --apply ...")
            subprocess.run([sys.executable, str(sync_script), "--apply"], check=False)
    else:
        print("\nDry run — pass --apply to write changes.")


if __name__ == "__main__":
    main()
