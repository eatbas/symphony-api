"""Smoke test for symphony.main entry-point module."""
from __future__ import annotations

import importlib
import sys


def test_main_module_creates_fastapi_app(config_path) -> None:  # noqa: ARG001 - fixture sets env
    # Force re-import so the module-level ``app = create_app()`` runs
    # against the per-test config fixture.
    sys.modules.pop("symphony.main", None)
    main = importlib.import_module("symphony.main")
    from fastapi import FastAPI

    assert isinstance(main.app, FastAPI)
    assert main.app.title == "Symphony"
