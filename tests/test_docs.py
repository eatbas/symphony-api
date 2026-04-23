from fastapi.testclient import TestClient

from symphony.routes.docs import repository_llms_path
from symphony.service import create_app


def test_llms_txt_matches_repository_file(config_path):
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/llms.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == repository_llms_path().read_text(encoding="utf-8")
    assert "/openapi.json" in response.text
    assert "POST /v1/chat" in response.text
