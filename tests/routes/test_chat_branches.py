"""Branch coverage for routes/chat.py."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from symphony.models import InstrumentName
from symphony.models.enums import ScoreStatus
from symphony.orchestra.score import ScoreHandle
from symphony.service import create_app


def test_get_score_returns_404_for_unknown_id(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/v1/chat/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


def test_websocket_returns_error_and_closes_for_unknown_score(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/v1/chat/missing/ws") as ws:
                msg = ws.receive_json()
                assert msg["type"] == "error"
                # Read further to trigger disconnect.
                ws.receive_json()
        assert exc_info.value.code == 1008


def test_websocket_emits_terminal_snapshot_when_handle_dropped(
    config_path, tmp_path: Path
) -> None:
    app = create_app()
    with TestClient(app) as client:
        # Submit a score and wait for it to complete.
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

        # Wait for completion via polling.
        for _ in range(50):
            snap = client.get(f"/v1/chat/{score_id}").json()
            if snap["status"] in {"completed", "failed", "stopped"}:
                break
        else:
            raise AssertionError("score did not terminate")

        # Drop the in-memory handle so the snapshot path fires in the WS.
        orchestra = app.state.orchestra
        orchestra._scores.pop(score_id, None)

        with client.websocket_connect(f"/v1/chat/{score_id}/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "score_snapshot"
            assert msg["score"]["score_id"] == score_id
            # WS closes immediately after the snapshot.
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_websocket_closes_immediately_when_handle_already_terminal(config_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        # Register a terminal handle directly.
        orchestra = app.state.orchestra
        handle = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
        handle.status = ScoreStatus.COMPLETED
        orchestra.register_score(handle)
        with client.websocket_connect(f"/v1/chat/{handle.score_id}/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "score_snapshot"
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_websocket_disconnect_during_streaming_unsubscribes(config_path) -> None:
    """Register a non-terminal handle, drop the client mid-stream, then
    publish an event so the server's send_json detects the disconnect
    and runs the WebSocketDisconnect branch."""
    import asyncio
    import threading
    import time

    app = create_app()
    with TestClient(app) as client:
        orchestra = app.state.orchestra
        handle = ScoreHandle(provider=InstrumentName.CLAUDE, model="opus")
        handle.status = ScoreStatus.RUNNING  # non-terminal
        orchestra.register_score(handle)

        def publish_after_delay() -> None:
            time.sleep(0.2)
            loop = app.state.orchestra.score_store.__dict__.get("_loop")
            # Publish a non-terminal event so the server tries to send_json
            # after the client has closed -- triggers WebSocketDisconnect.
            for q in list(handle._subscribers):
                try:
                    q.put_nowait({"type": "output_delta", "text": "ping"})
                except Exception:
                    pass

        with client.websocket_connect(f"/v1/chat/{handle.score_id}/ws") as ws:
            ws.receive_json()  # initial snapshot
            # Schedule a publish AFTER the client closes (in a thread that
            # publishes shortly after the with-block exits).
            t = threading.Thread(target=publish_after_delay, daemon=True)
            t.start()
        # ws is now closed; wait for the publish thread to fire.
        t.join(timeout=2.0)

        # Subscribers should eventually be cleaned up by the server's finally.
        for _ in range(40):
            if not handle._subscribers:
                break
            time.sleep(0.05)
        # The subscriber set should be empty after WebSocketDisconnect cleanup.
        assert not handle._subscribers
