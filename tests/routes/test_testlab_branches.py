"""Branch coverage for routes/testlab.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from symphony.routes.testlab import _parse_generate_response
from symphony.service import create_app


# ---------------------------------------------------------------------------
# _parse_generate_response branches (pure-function)
# ---------------------------------------------------------------------------


class TestParseGenerateResponse:
    def test_parses_bare_json(self) -> None:
        raw = '{"story": "Alice", "qa_pairs": [{"question": "q?", "expected": "a"}]}'
        resp = _parse_generate_response(raw, "memory")
        assert resp.story == "Alice"
        assert len(resp.qa_pairs) == 1
        assert resp.qa_pairs[0].question == "q?"

    def test_parses_fenced_json(self) -> None:
        raw = '```json\n{"story": "Bob"}\n```'
        resp = _parse_generate_response(raw, "memory")
        assert resp.story == "Bob"

    def test_parses_unfenced_json_after_text(self) -> None:
        raw = 'Here you go: {"story": "Carol"}'
        resp = _parse_generate_response(raw, "memory")
        assert resp.story == "Carol"

    def test_returns_story_with_raw_when_unparseable(self) -> None:
        raw = "just plain text"
        resp = _parse_generate_response(raw, "memory")
        assert resp.story == "just plain text"

    def test_filters_invalid_qa_entries(self) -> None:
        raw = (
            '{"story": "x", "qa_pairs": ['
            '{"question": "q", "expected": "e"},'  # valid
            '"not a dict",'  # invalid type
            '{"only_question": "q"}'  # missing expected
            "]}"
        )
        resp = _parse_generate_response(raw, "memory")
        assert len(resp.qa_pairs) == 1

    def test_handles_qa_pairs_not_a_list(self) -> None:
        raw = '{"story": "x", "qa_pairs": "not-a-list"}'
        resp = _parse_generate_response(raw, "memory")
        assert resp.qa_pairs == []

    def test_falls_back_to_brace_match_when_fence_invalid(self) -> None:
        raw = '```\nnot-json\n```\n{"story": "matched"}'
        resp = _parse_generate_response(raw, "memory")
        assert resp.story == "matched"

    def test_returns_raw_when_fence_and_brace_both_unparseable(self) -> None:
        raw = '```json\n{not}\n``` {also not}'
        resp = _parse_generate_response(raw, "memory")
        # Falls back to raw story.
        assert resp.story == raw


# ---------------------------------------------------------------------------
# /v1/test/generate-scenario route branches
# ---------------------------------------------------------------------------


def test_generate_scenario_503_when_no_cheap_model_available(config_path, tmp_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(app.state.orchestra, "acquire_musician", AsyncMock(return_value=None)):
            response = client.post(
                "/v1/test/generate-scenario",
                json={
                    "workspace_path": str(tmp_path.resolve()),
                    "field": "memory",
                },
            )
        assert response.status_code == 503
        assert "No cheap model" in response.json()["detail"]


def test_generate_scenario_500_when_handle_raises(config_path, tmp_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        # Patch musician.submit so handle.result_future raises.
        from symphony.orchestra.score import ScoreHandle

        orchestra = app.state.orchestra
        real_acquire = orchestra.acquire_musician

        async def fake_acquire(provider, model):
            musician = await real_acquire(provider, model)
            if musician is None:
                return None

            async def fake_submit(req, handle=None):
                import asyncio
                h = ScoreHandle(provider=req.provider, model=req.model)
                loop = asyncio.get_event_loop()
                h.result_future = loop.create_future()
                h.result_future.set_exception(RuntimeError("boom"))
                return h

            musician.submit = fake_submit  # type: ignore[method-assign]
            return musician

        with patch.object(orchestra, "acquire_musician", side_effect=fake_acquire):
            response = client.post(
                "/v1/test/generate-scenario",
                json={
                    "workspace_path": str(tmp_path.resolve()),
                    "field": "memory",
                },
            )
        assert response.status_code == 500
        assert "Generation failed" in response.json()["detail"]


def test_generate_scenario_uses_explicit_provider_and_model(config_path, tmp_path) -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/test/generate-scenario",
            json={
                "provider": "claude",
                "model": "opus",
                "workspace_path": str(tmp_path.resolve()),
                "field": "memory",
            },
        )
        # The fake CLI returns "claude:..." which is not JSON, so parser
        # falls back to story=raw.
        assert response.status_code == 200
        body = response.json()
        assert "story" in body
