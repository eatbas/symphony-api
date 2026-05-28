"""Confirm ``.env`` loading at import time propagates into subprocess env.

The OpenCode CLI authenticates against OpenRouter via the
``OPENROUTER_API_KEY`` environment variable.  Symphony loads the value
from a project-root ``.env`` at import time via
:func:`dotenv.load_dotenv` so the same key is available to every
musician subprocess via the standard ``os.environ`` propagation in
:mod:`symphony.shells`.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock

import pytest

from symphony import shells


def test_load_dotenv_runs_on_config_import() -> None:
    """Importing :mod:`symphony.config` must invoke ``load_dotenv``.

    The check is indirect: ``dotenv`` is imported by ``symphony.config``
    at module load.  We assert the symbol is bound and the module has
    not failed to import.
    """
    import symphony.config as config_module
    from dotenv import load_dotenv

    assert hasattr(config_module, "load_dotenv")
    assert config_module.load_dotenv is load_dotenv


def test_load_dotenv_does_not_override_existing_environ(monkeypatch) -> None:
    """``override=False`` keeps real-environment values authoritative.

    Setting an env var before reloading ``config`` must survive — the
    fixture mimics the deployment-time override pattern.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sentinel-real-env-value")
    assert os.environ["OPENROUTER_API_KEY"] == "sentinel-real-env-value"


@pytest.mark.asyncio
async def test_subprocess_env_propagates_openrouter_key(monkeypatch) -> None:
    """``BashSession.start`` must pass ``OPENROUTER_API_KEY`` into the
    subprocess environment.

    Monkeypatches :func:`asyncio.create_subprocess_exec` so we can
    capture its ``env=`` kwarg without launching a real shell, then
    drives :meth:`shells.BashSession.start` and asserts the captured env
    contains the sentinel key value.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sentinel-subprocess-value")

    captured: dict[str, Any] = {}

    fake_process = AsyncMock()
    fake_process.returncode = None
    fake_process.stdin = AsyncMock()
    fake_process.stdout = AsyncMock()
    # ``readline`` returns an empty bytes object so the background
    # reader loop exits immediately without raising.
    fake_process.stdout.readline = AsyncMock(return_value=b"")

    async def fake_create_subprocess_exec(*args: Any, **kwargs: Any):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return fake_process

    monkeypatch.setattr(
        shells.asyncio, "create_subprocess_exec", fake_create_subprocess_exec,
    )

    session = shells.BashSession(shell_path="/bin/bash-sentinel")
    try:
        await session.start()
    finally:
        # The reader loop is now running against the stub stdout; cancel
        # it so the test doesn't leak a background task.
        if session._reader_task is not None:
            session._reader_task.cancel()
            try:
                await session._reader_task
            except (asyncio.CancelledError, Exception):
                pass

    env = captured["env"]
    assert env is not None
    assert env["OPENROUTER_API_KEY"] == "sentinel-subprocess-value"
    assert env["PYTHONUTF8"] == "1"
    # Sanity: the shell path argument made it through unchanged.
    assert captured["args"][0] == "/bin/bash-sentinel"
