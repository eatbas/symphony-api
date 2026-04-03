import os
import time
from fastapi.testclient import TestClient

from symphony.models import InstrumentName
from symphony.service import create_app


def submit_score(client: TestClient, body: dict) -> dict:
    response = client.post("/v1/chat", json=body)
    assert response.status_code == 202
    payload = response.json()
    assert payload["score_id"]
    return payload


def wait_for_terminal_score(client: TestClient, score_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/v1/chat/{score_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed", "stopped"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Score {score_id} did not reach a terminal state within {timeout_seconds}s")


def test_health_and_provider_endpoints(config_path):
    app = create_app()
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "Symphony Console" in index.text

        health = client.get("/health")
        assert health.status_code == 200
        payload = health.json()
        assert payload["musician_count"] == 9

        providers = client.get("/v1/providers")
        assert providers.status_code == 200
        assert len(providers.json()) == 6

        all_providers = client.get("/v1/providers?all=true")
        assert all_providers.status_code == 200
        assert len(all_providers.json()) == 6

        musicians = client.get("/v1/musicians")
        assert musicians.status_code == 200
        assert len(musicians.json()) == 9


def test_chat_json_and_streaming(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "claude",
            "model": "sonnet",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        accepted = submit_score(client, body)
        payload = wait_for_terminal_score(client, accepted["score_id"])
        assert payload["final_text"] == "claude:hello"
        assert payload["provider_session_ref"]

        ws_body = {
            "provider": "codex",
            "model": "gpt-5.3-codex",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        accepted = submit_score(client, ws_body)
        score_id = accepted["score_id"]
        with client.websocket_connect(f"/v1/chat/{score_id}/ws") as websocket:
            initial = websocket.receive_json()
            assert initial["type"] == "score_snapshot"
            assert initial["score"]["score_id"] == score_id
        terminal = wait_for_terminal_score(client, score_id)
        assert terminal["final_text"] == "codex:hello"

        assert not list(tmp_path.rglob("*.sqlite"))


def test_chat_copilot_json(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "copilot",
            "model": "claude-sonnet-4.6",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        accepted = submit_score(client, body)
        payload = wait_for_terminal_score(client, accepted["score_id"])
        assert payload["final_text"] == "copilot:hello"
        assert payload["provider_session_ref"]


def test_chat_opencode_json(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "opencode",
            "model": "glm-4.7-flash",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        accepted = submit_score(client, body)
        payload = wait_for_terminal_score(client, accepted["score_id"])
        assert payload["final_text"] == "opencode:hello"
        assert payload["provider_session_ref"]


def test_chat_opencode_glm51_json(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "opencode",
            "model": "glm-5.1",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        accepted = submit_score(client, body)
        payload = wait_for_terminal_score(client, accepted["score_id"])
        assert payload["final_text"] == "opencode:hello"
        assert payload["provider_session_ref"]


def test_chat_returns_400_for_unavailable_provider(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        # Manually mark a provider as unavailable
        orchestra = app.state.orchestra
        orchestra.available_providers[InstrumentName.CLAUDE] = False
        body = {
            "provider": "claude",
            "model": "sonnet",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        response = client.post("/v1/chat", json=body)
        assert response.status_code == 400
        assert "not available" in response.json()["detail"]


def test_chat_returns_404_for_unknown_musician(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "claude",
            "model": "nonexistent-model",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        response = client.post("/v1/chat", json=body)
        assert response.status_code == 404


def test_chat_resume_requires_session_ref(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "claude",
            "model": "sonnet",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "resume",
            "prompt": "hello",
        }
        response = client.post("/v1/chat", json=body)
        assert response.status_code == 422


def test_chat_rejects_relative_workspace_path(config_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "claude",
            "model": "sonnet",
            "workspace_path": "relative/path",
            "mode": "new",
            "prompt": "hello",
        }
        response = client.post("/v1/chat", json=body)
        assert response.status_code == 422


def test_musicians_endpoint_reflects_musician_state(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        musicians = client.get("/v1/musicians").json()
        providers_seen = {m["provider"] for m in musicians}
        assert "claude" in providers_seen
        assert "gemini" in providers_seen
        assert "codex" in providers_seen
        assert "kimi" in providers_seen
        assert "copilot" in providers_seen
        assert "opencode" in providers_seen
        assert all(m["ready"] for m in musicians)
        assert all(not m["busy"] for m in musicians)


def test_providers_endpoint_shows_capabilities(config_path):
    app = create_app()
    with TestClient(app) as client:
        providers = client.get("/v1/providers?all=true").json()
        for p in providers:
            assert "supports_resume" in p
            assert "supports_model_override" in p
            assert "session_reference_format" in p
            assert "available" in p
            assert "models" in p
            assert isinstance(p["models"], list)
            assert len(p["models"]) >= 1


def test_models_endpoint_returns_all_models(config_path):
    app = create_app()
    with TestClient(app) as client:
        models = client.get("/v1/models").json()
        assert len(models) == 9  # 1 gemini + 2 codex + 2 claude + 1 kimi + 1 copilot + 2 opencode
        providers_seen = {m["provider"] for m in models}
        assert "claude" in providers_seen
        assert "copilot" in providers_seen
        for m in models:
            assert "model" in m
            assert "ready" in m
            assert "busy" in m
            assert "supports_resume" in m
            assert "chat_request_example" in m
            example = m["chat_request_example"]
            assert example["provider"] == m["provider"]
            assert example["model"] == m["model"]
            assert example["mode"] == "new"
            assert "prompt" in example
            assert "workspace_path" in example


def test_cors_headers_present(config_path):
    app = create_app()
    with TestClient(app) as client:
        response = client.options(
            "/v1/chat",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "*"


def test_no_persistent_state_files_created(config_path, tmp_path):
    """The wrapper must not create any database or session files."""
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "kimi",
            "model": "default",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        client.post("/v1/chat", json=body)

        for ext in ("*.sqlite", "*.db", "*.json"):
            assert not list(tmp_path.rglob(ext)), f"Found unexpected {ext} files"


def test_stop_returns_404_for_unknown_score(config_path):
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/chat/nonexistent-id/stop")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


def test_stop_completed_score_is_idempotent(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "claude",
            "model": "sonnet",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        accepted = submit_score(client, body)
        score_id = accepted["score_id"]
        assert score_id

        stop_response = client.post(f"/v1/chat/{score_id}/stop")
        assert stop_response.status_code == 200
        payload = stop_response.json()
        assert payload["score_id"] == score_id
        assert payload["status"] in ("completed", "failed", "stopped")


def test_chat_response_includes_score_id(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "codex",
            "model": "gpt-5.3-codex",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        response = client.post("/v1/chat", json=body)
        assert response.status_code == 202
        payload = response.json()
        assert "score_id" in payload
        assert payload["score_id"]


def test_websocket_snapshot_includes_score_id(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        body = {
            "provider": "claude",
            "model": "sonnet",
            "workspace_path": str(tmp_path.resolve()),
            "mode": "new",
            "prompt": "hello",
        }
        accepted = submit_score(client, body)
        with client.websocket_connect(f"/v1/chat/{accepted['score_id']}/ws") as websocket:
            payload = websocket.receive_json()
            assert payload["type"] == "score_snapshot"
            assert payload["score"]["score_id"] == accepted["score_id"]
