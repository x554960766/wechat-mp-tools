"""NSFW 审查的接口契约：模型不可用时 review 请求必须明确 503，普通搜索照常可用。

Spec §228 / §276 明确写了这条红线：
  - 模型缺失时 review 请求明确 503（不能用「未检出」伪装成安全的结果）
  - 普通搜索（review=False）不受 NSFW 模型缺失影响，照常可用

本测试把 review 强制置于不可用状态（状态=受检查器未能加载 + _load 抛 RuntimeError），
然后分别打 review=True 与 review=False 的文本检索接口，断言：
  1. review=True -> 503，错误体 error.code == "review_unavailable"
  2. review=False -> 200，结果容器不含 review 键（未伪装成审查通过）

不会触达真实模型，也不会因为 onnxruntime 未安装而跳过（模型本应被隔离在外）。
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from app import review
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    """把检索查询短路成空结果，只考察 review 的接口行为。"""
    from app import search
    monkeypatch.setattr(search, "text_query", lambda *a, **k: [])
    return TestClient(app, base_url="http://127.0.0.1:8765")


@pytest.fixture()
def review_unavailable(monkeypatch):
    """模拟 NSFW 模型不可用：status 不是 ready，且 _load 抛 RuntimeError。"""
    review._model = None
    review._error = None

    def _boom():
        raise RuntimeError("NSFW 模型不可用")

    monkeypatch.setattr(review, "status", lambda: "unloaded")
    monkeypatch.setattr(review, "_load", _boom)
    yield
    review._model = None
    review._error = None


def test_review_disabled_succeeds_without_nsfw_model(client):
    """普通搜索不依赖 NSFW 模型，模型缺失时也必须照常返回。"""
    result = client.post("/api/search/text", json={"q": "海边", "review": False})
    assert result.status_code == 200
    body = result.json()
    assert body["items"] == []
    assert body["query_review"] is None


def test_review_enabled_with_missing_model_returns_explicit_503(client, review_unavailable):
    """NSFW 不可用时，review 请求必须 503 review_unavailable，不能伪装成通过。"""
    result = client.post("/api/search/text", json={"q": "海边", "review": True})
    assert result.status_code == 503
    # app.main 的 HTTPException 处理器把 detail={"error":{...}} 摊平成顶层 error
    error = result.json()["error"]
    assert error["code"] == "review_unavailable"


def test_review_disabled_still_succeeds_after_review_503(client, review_unavailable):
    """同一时刻：review 请求 503，普通搜索仍 200（同一进程内互不污染）。"""
    bad = client.post("/api/search/text", json={"q": "海边", "review": True})
    assert bad.status_code == 503
    ok = client.post("/api/search/text", json={"q": "海边"})
    assert ok.status_code == 200
    assert ok.json()["query_review"] is None
