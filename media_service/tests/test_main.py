"""本机入口的外部请求边界。"""
from fastapi.testclient import TestClient

from app.main import app


def test_home_and_health():
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    assert client.get("/").status_code == 200
    body = client.get("/api/health").json()
    assert "model" in body and "index" in body and "roots" in body


def test_rejects_external_host_and_origin():
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
    result = client.post("/api/search/text", headers={"Origin": "https://evil.example"}, json={"q": "test"})
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "origin_forbidden"


def test_rejects_oversized_body_before_route():
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    result = client.post("/api/search/text", headers={"Content-Length": str(33 * 1024 * 1024)}, content=b"x")
    assert result.status_code == 413


def test_rejects_streamed_oversized_body():
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    def chunks():
        yield b"x" * (16 * 1024 * 1024)
        yield b"x" * (17 * 1024 * 1024)
    result = client.post("/api/archive/jobs", content=chunks(), headers={"Content-Type": "application/json"})
    assert result.status_code == 413
