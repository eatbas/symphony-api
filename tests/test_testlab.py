"""Tests for the Test Lab endpoints: /v1/test/verify and /v1/test/generate-scenario."""

from fastapi.testclient import TestClient

from symphony.service import create_app, _parse_generate_response


# ------------------------------------------------------------------
# /v1/test/verify
# ------------------------------------------------------------------


def test_verify_all_pass(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/v1/test/verify", json={
            "items": [
                {
                    "provider": "claude",
                    "model": "sonnet",
                    "new_exit_code": 0,
                    "resume_text": "You manage PF, ATM, and Transit systems.",
                    "resume_exit_code": 0,
                    "keywords": ["PF", "ATM", "Transit"],
                },
            ],
        })
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        r = results[0]
        assert r["new_status"] == "OK"
        assert r["resume_status"] == "OK"
        assert r["keyword_results"] == {"PF": True, "ATM": True, "Transit": True}
        assert r["grade"] == "PASS"


def test_verify_keyword_miss(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/v1/test/verify", json={
            "items": [
                {
                    "provider": "antigravity",
                    "model": "gemini-3.5-flash",
                    "new_exit_code": 0,
                    "resume_text": "You manage PF and ATM.",
                    "resume_exit_code": 0,
                    "keywords": ["PF", "ATM", "Transit"],
                },
            ],
        })
        assert resp.status_code == 200
        r = resp.json()["results"][0]
        assert r["keyword_results"]["PF"] is True
        assert r["keyword_results"]["ATM"] is True
        assert r["keyword_results"]["Transit"] is False
        assert r["grade"] == "FAIL"


def test_verify_new_fail(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/v1/test/verify", json={
            "items": [
                {
                    "provider": "codex",
                    "model": "gpt-5.4",
                    "new_exit_code": 1,
                    "resume_text": "PF ATM Transit",
                    "resume_exit_code": 0,
                    "keywords": ["PF"],
                },
            ],
        })
        r = resp.json()["results"][0]
        assert r["new_status"] == "FAIL"
        assert r["resume_status"] == "OK"
        assert r["grade"] == "FAIL"


def test_verify_resume_fail(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/v1/test/verify", json={
            "items": [
                {
                    "provider": "claude",
                    "model": "opus",
                    "new_exit_code": 0,
                    "resume_text": "PF ATM Transit",
                    "resume_exit_code": 3,
                    "keywords": ["PF"],
                },
            ],
        })
        r = resp.json()["results"][0]
        assert r["new_status"] == "OK"
        assert r["resume_status"] == "FAIL"
        assert r["grade"] == "FAIL"


def test_verify_case_insensitive(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/v1/test/verify", json={
            "items": [
                {
                    "provider": "kimi",
                    "model": "default",
                    "new_exit_code": 0,
                    "resume_text": "manages pf, atm, and transit",
                    "resume_exit_code": 0,
                    "keywords": ["PF", "ATM", "Transit"],
                },
            ],
        })
        r = resp.json()["results"][0]
        assert all(r["keyword_results"].values())
        assert r["grade"] == "PASS"


def test_verify_empty_keywords(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/v1/test/verify", json={
            "items": [
                {
                    "provider": "claude",
                    "model": "sonnet",
                    "new_exit_code": 0,
                    "resume_text": "any text here",
                    "resume_exit_code": 0,
                    "keywords": [],
                },
            ],
        })
        r = resp.json()["results"][0]
        assert r["grade"] == "PASS"


def test_verify_multiple_models(config_path, tmp_path):
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/v1/test/verify", json={
            "items": [
                {
                    "provider": "claude",
                    "model": "sonnet",
                    "new_exit_code": 0,
                    "resume_text": "PF ATM Transit",
                    "resume_exit_code": 0,
                    "keywords": ["PF", "ATM", "Transit"],
                },
                {
                    "provider": "codex",
                    "model": "gpt-5.4",
                    "new_exit_code": 0,
                    "resume_text": "PF only",
                    "resume_exit_code": 0,
                    "keywords": ["PF", "ATM"],
                },
            ],
        })
        results = resp.json()["results"]
        assert len(results) == 2
        assert results[0]["grade"] == "PASS"
        assert results[1]["grade"] == "FAIL"


# ------------------------------------------------------------------
# /v1/test/generate-scenario
# ------------------------------------------------------------------


def test_generate_scenario_returns_200(config_path, tmp_path):
    """The generate endpoint should return 200 via the cheapest model."""
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/v1/test/generate-scenario", json={
            "field": "all",
            "workspace_path": str(tmp_path.resolve()),
        })
        # The fake CLI echoes the prompt back; the complex prompt may not parse
        # cleanly through the fake CLI's simple JSON escaping, but the endpoint
        # should still return 200 with a fallback response.
        assert resp.status_code == 200
        data = resp.json()
        # At minimum, the response should have the expected keys
        assert "story" in data
        assert "qa_pairs" in data


# ------------------------------------------------------------------
# _parse_generate_response unit tests
# ------------------------------------------------------------------


def test_parse_json_response():
    raw = '{"story": "Hello", "qa_pairs": [{"question": "What?", "expected": "world"}]}'
    result = _parse_generate_response(raw, "all")
    assert result.story == "Hello"
    assert len(result.qa_pairs) == 1
    assert result.qa_pairs[0].question == "What?"
    assert result.qa_pairs[0].expected == "world"


def test_parse_fenced_json():
    raw = 'Sure, here is the JSON:\n```json\n{"story": "Test story"}\n```'
    result = _parse_generate_response(raw, "story")
    assert result.story == "Test story"


def test_parse_json_with_qa_pairs():
    raw = '{"story": "Hi I am Bob", "qa_pairs": [{"question": "Who am I?", "expected": "Bob"}, {"question": "What?", "expected": "nothing"}]}'
    result = _parse_generate_response(raw, "all")
    assert result.story == "Hi I am Bob"
    assert len(result.qa_pairs) == 2
    assert result.qa_pairs[0].expected == "Bob"


def test_parse_fallback_all():
    raw = "Plain text without JSON"
    result = _parse_generate_response(raw, "all")
    assert result.story == "Plain text without JSON"
