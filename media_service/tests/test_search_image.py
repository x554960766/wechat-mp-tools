"""以图搜图路由契约：/api/search/image 的上传/网址两条来源、校验与异常状态码。"""
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import config, media_routes, search
from app.main import app


def _client():
    # 本机 Host 白名单会拒 testserver，必须显式给 127.0.0.1:8765。
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _png_bytes():
    # 路由只校验长度、不解码，非空字节即可覆盖成功与边界路径。
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _upload(name="a.png"):
    return {"file": (name, _png_bytes(), "image/png")}


def test_search_image_rejects_empty_upload():
    result = _client().post("/api/search/image", files={"file": ("empty.png", b"", "image/png")})
    assert result.status_code == 422


def test_search_image_rejects_oversized_upload(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 16)
    result = _client().post("/api/search/image", files=_upload("big.png"))
    assert result.status_code == 413


def test_search_image_rejects_out_of_range_k():
    # k=0 能过 FastAPI 的 Form(int)，由检索层按 1..200 边界拒绝。
    result = _client().post("/api/search/image", files=_upload(), data={"k": "0"})
    assert result.status_code == 422


def test_search_image_rejects_unknown_root():
    # _check_roots 在编码前执行，非法 root 应直接 422，不触碰模型。
    result = _client().post("/api/search/image", files=_upload(), data={"roots": "nope"})
    assert result.status_code == 422


def test_search_image_returns_items_and_videos(monkeypatch):
    items = [{"root": "r", "relpath": "a.png", "version": "v1"}]
    videos = [{"root": "r", "relpath": "v.mp4", "score": 0.9}]
    monkeypatch.setattr(search, "image_query_with_videos", lambda *a, **k: (items, videos))
    result = _client().post("/api/search/image", files=_upload(), data={"k": "8"})
    assert result.status_code == 200
    body = result.json()
    assert body["items"] == items
    assert body["videos"] == videos
    assert body["query_review"] is None


def test_search_image_videos_flag_can_disable_video_hits(monkeypatch):
    # videos=False 时路由传 videos_k=0，检索层应返回空视频命中。
    seen = {}

    def _stub(data, roots, after, before, k, videos_k=0, **_):
        seen["videos_k"] = videos_k
        return [], []

    monkeypatch.setattr(search, "image_query_with_videos", _stub)
    result = _client().post("/api/search/image", files=_upload(), data={"videos": "false"})
    assert result.status_code == 200
    assert seen["videos_k"] == 0
    assert result.json()["videos"] == []


def test_search_image_model_failure_returns_503(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("model not loaded")

    monkeypatch.setattr(search, "image_query_with_videos", _boom)
    result = _client().post("/api/search/image", files=_upload())
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "model_unavailable"


# ── 以图搜图：图片网址来源 ─────────────────────────────

def test_search_image_downloads_image_from_url(monkeypatch):
    items = [{"root": "r", "relpath": "a.png", "version": "v1"}]
    monkeypatch.setattr(search, "image_query_with_videos", lambda *a, **k: (items, []))

    async def _fake_fetch(url):
        assert url == "https://example.com/a.png"
        return _png_bytes()

    monkeypatch.setattr(media_routes, "_fetch_url_image", _fake_fetch)
    result = _client().post("/api/search/image", data={"url": "https://example.com/a.png", "k": "8"})
    assert result.status_code == 200
    assert result.json()["items"] == items


def test_search_image_url_rejects_non_http_scheme():
    result = _client().post("/api/search/image", data={"url": "ftp://example.com/a.png"})
    assert result.status_code == 422


def test_search_image_url_rejects_empty():
    result = _client().post("/api/search/image", data={"url": ""})
    assert result.status_code == 422


def test_search_image_url_download_failure_returns_502(monkeypatch):
    async def _boom(url):
        raise HTTPException(502, "image download failed: HTTP 403")

    monkeypatch.setattr(media_routes, "_fetch_url_image", _boom)
    result = _client().post("/api/search/image", data={"url": "https://example.com/a.png"})
    assert result.status_code == 502
