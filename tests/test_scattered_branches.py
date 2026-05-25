"""Scattered single-line branch tests across small modules."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from symphony.config import load_config
from symphony.models import ChatMode, ChatRequest


# ---------------------------------------------------------------------------
# config.py:86 — FileNotFoundError when config path missing
# ---------------------------------------------------------------------------


def test_load_config_raises_when_path_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "no-such.toml")


# ---------------------------------------------------------------------------
# models/chat.py:73, 77 — workspace_path validator branches
# ---------------------------------------------------------------------------


def test_chat_request_rejects_empty_workspace_path() -> None:
    with pytest.raises(Exception):
        ChatRequest(
            provider="claude",
            model="opus",
            workspace_path="   ",  # blank after strip → ValueError
            mode=ChatMode.NEW,
            prompt="hi",
        )


def test_chat_request_accepts_windows_drive_workspace_path() -> None:
    req = ChatRequest(
        provider="claude",
        model="opus",
        workspace_path=r"C:\Users\x",
        mode=ChatMode.NEW,
        prompt="hi",
    )
    assert req.workspace_path == r"C:\Users\x"


def test_chat_request_accepts_forward_slash_drive_workspace_path() -> None:
    req = ChatRequest(
        provider="claude",
        model="opus",
        workspace_path="D:/Projects",
        mode=ChatMode.NEW,
        prompt="hi",
    )
    assert req.workspace_path == "D:/Projects"


def test_chat_request_rejects_relative_path() -> None:
    with pytest.raises(Exception):
        ChatRequest(
            provider="claude",
            model="opus",
            workspace_path="relative/path",
            mode=ChatMode.NEW,
            prompt="hi",
        )


# ---------------------------------------------------------------------------
# routes/docs.py:52 — fallback to embedded LLMS_TEXT when llms.txt missing
# ---------------------------------------------------------------------------


def test_llms_txt_falls_back_to_embedded_when_file_missing(
    config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.service import create_app
    from symphony.routes import docs as docs_mod

    # Force the path lookup to a non-existent location.
    monkeypatch.setattr(docs_mod, "repository_llms_path", lambda: Path("/no/such/llms.txt"))
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/llms.txt")
        assert response.status_code == 200
        assert "Symphony API" in response.text


# ---------------------------------------------------------------------------
# routes/chat.py:100 — WebSocketDisconnect path
# ---------------------------------------------------------------------------


def test_websocket_disconnect_during_streaming_unsubscribes(config_path, tmp_path) -> None:
    """Closing the WS mid-stream must trigger the WebSocketDisconnect branch."""
    from symphony.service import create_app

    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "claude",
            "model": "opus",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        resp = client.post("/v1/chat", json=body)
        assert resp.status_code == 202
        score_id = resp.json()["score_id"]
        # Open a WS, read the initial snapshot, then close before the
        # terminal event — the server-side WebSocketDisconnect branch fires.
        with client.websocket_connect(f"/v1/chat/{score_id}/ws") as ws:
            ws.receive_json()  # initial snapshot
            ws.close()


# ---------------------------------------------------------------------------
# version_checker.py:111-112 — get_latest_version returns shell result on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_latest_version_returns_shell_result_when_available(loaded_config) -> None:
    from symphony.models import InstrumentName
    from symphony.updater import CLIPackageInfo
    from symphony.updater.version_checker import get_latest_version
    from tests.helpers.orchestra import started_orchestra

    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_idle_musician(InstrumentName.CODEX)
        assert musician is not None
        musician.run_quick_command = AsyncMock(return_value=(0, "9.9.9"))  # type: ignore[method-assign]

        async def fake_runner(*args, timeout=60):
            raise AssertionError("should not fall back to subprocess")

        version = await get_latest_version(
            manager=manager,
            runner=fake_runner,
            pkg_info=CLIPackageInfo(
                provider=InstrumentName.CODEX, manager="npm", package="some-pkg"
            ),
        )
        assert version == "9.9.9"


# ---------------------------------------------------------------------------
# update_runner.py:96-97 — shell update fails with nonzero exit code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_update_shell_nonzero_returns_false(
    loaded_config, caplog: pytest.LogCaptureFixture
) -> None:
    from symphony.models import InstrumentName
    from symphony.updater import CLIPackageInfo
    from symphony.updater.update_runner import run_update
    from tests.helpers.orchestra import started_orchestra

    caplog.set_level("ERROR", logger="symphony.updater")
    async with started_orchestra(loaded_config) as manager:
        musician = manager.get_idle_musician(InstrumentName.CODEX)
        assert musician is not None
        musician.run_quick_command = AsyncMock(return_value=(1, "error output"))  # type: ignore[method-assign]

        async def fake_runner(*args, timeout=60):
            raise AssertionError("subprocess path must not be reached")

        result = await run_update(
            manager=manager,
            run_cmd=fake_runner,
            pkg_info=CLIPackageInfo(
                provider=InstrumentName.CODEX, manager="npm", package="some-pkg"
            ),
            executable=None,
        )
        assert result is False
        assert "shell" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Discoverer line 241 — `discover_provider` unchanged-models branch logs debug
# ---------------------------------------------------------------------------


def test_discover_provider_returns_false_when_models_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.discovery.discoverer import discover_provider
    from symphony.models import InstrumentName

    cfg = tmp_path / "config.toml"
    cfg.write_text('[providers.claude]\nmodels = ["opus", "haiku"]\n')
    with patch(
        "symphony.discovery.discoverer.DISCOVERERS",
        {InstrumentName.CLAUDE: lambda: ["opus", "haiku"]},
    ):
        changed = discover_provider(InstrumentName.CLAUDE, cfg)
    assert changed is False


def test_discover_provider_returns_false_when_config_path_missing(tmp_path: Path) -> None:
    from symphony.discovery.discoverer import discover_provider
    from symphony.models import InstrumentName

    with patch(
        "symphony.discovery.discoverer.DISCOVERERS",
        {InstrumentName.CLAUDE: lambda: ["opus"]},
    ):
        assert discover_provider(InstrumentName.CLAUDE, tmp_path / "nope.toml") is False


# ---------------------------------------------------------------------------
# updater/lifecycle.py:39 — the loop continues after errors (already-tested
# behaviour but ensure the sleep path runs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_periodic_loop_completes_one_iteration_and_sleeps(loaded_config) -> None:
    from symphony.config import UpdaterConfig
    from symphony.orchestra import Orchestra
    from symphony.updater import CLIUpdater
    from symphony.updater.lifecycle import periodic_loop

    manager = Orchestra(loaded_config)
    updater = CLIUpdater(
        manager=manager,
        config=UpdaterConfig(enabled=True, interval_hours=24, auto_update=False),
    )
    updater.check_and_update_all = AsyncMock(return_value=[])  # type: ignore[method-assign]

    task = asyncio.create_task(periodic_loop(updater))
    # Yield enough for one full iteration (check + start of sleep).
    await asyncio.sleep(0.05)
    updater.check_and_update_all.assert_awaited()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
