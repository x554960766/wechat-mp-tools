"""视频画面检索：帧向量排序、按视频归并、过滤与帧/视频端点边界。"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, encoder, frames, media_routes, search
from app.main import app
from app.store import Store


def _vector(slot=0, scale=1.0):
    vec = np.zeros(512, dtype=np.float32)
    vec[slot % 512] = scale
    return vec


def _setup(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": "r", "type": "local", "path": str(root), "writable": False},
                         {"id": "nas", "type": "zspace", "path": "/sata11/my/data", "writable": True}))
    monkeypatch.setattr(config, "FRAMES_DIR", tmp_path / "frames")
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(search, "STORE", store)
    monkeypatch.setattr(media_routes, "STORE", store)
    monkeypatch.setattr(frames, "STORE", store)
    return root, store


def _seed(store, relpath, vectors, root="r", digest="a" * 64, sampled_at=None):
    """按给定向量逐帧入库；返回帧时间点列表。"""
    items = [(float(i), f"{i:064d}"[:64], vec.tobytes()) for i, vec in enumerate(vectors)]
    store.replace_frames(root, relpath, digest, 30.0, items, sampled_at=sampled_at)
    return [ts for ts, _, _ in items]


def test_rank_frames_groups_by_video_and_sorts(monkeypatch, tmp_path):
    _root, store = _setup(monkeypatch, tmp_path)
    _seed(store, "作者甲/作品一.mp4", [_vector(0, 0.9), _vector(0, 0.5)])
    _seed(store, "作者乙/作品二.mp4", [_vector(0, 0.7)])
    out = search._rank_frames(_vector(0), [], None, None, k=10, per_video=3)
    assert [g["relpath"] for g in out] == ["作者甲/作品一.mp4", "作者乙/作品二.mp4"]
    assert out[0]["score"] == pytest.approx(0.9)
    assert [h["ts"] for h in out[0]["hits"]] == [0.0, 1.0]
    assert out[0]["hits"][0]["thumb"].startswith("/api/media/frame?") and "ts=0" in out[0]["hits"][0]["thumb"]
    assert out[0]["file"].startswith("/api/media/video?") and "version=" in out[0]["file"]


def test_rank_frames_caps_hits_per_video(monkeypatch, tmp_path):
    _root, store = _setup(monkeypatch, tmp_path)
    _seed(store, "作者甲/作品一.mp4", [_vector(0, 0.9), _vector(0, 0.8), _vector(0, 0.7)])
    out = search._rank_frames(_vector(0), [], None, None, k=5, per_video=2)
    assert len(out) == 1 and len(out[0]["hits"]) == 2


def test_rank_frames_filters_roots(monkeypatch, tmp_path):
    _root, store = _setup(monkeypatch, tmp_path)
    _seed(store, "作者甲/作品一.mp4", [_vector(0, 0.9)])
    assert search._rank_frames(_vector(0), ["nas"], None, None, k=5) == []


def test_rank_frames_filters_by_sample_date(monkeypatch, tmp_path):
    _root, store = _setup(monkeypatch, tmp_path)
    _seed(store, "旧.mp4", [_vector(0, 0.9)], sampled_at="2020-01-01T00:00:00+00:00")
    _seed(store, "新.mp4", [_vector(0, 0.8)], sampled_at="2026-01-01T00:00:00+00:00")
    out = search._rank_frames(_vector(0), [], "2024-01-01", None, k=5)
    assert [g["relpath"] for g in out] == ["新.mp4"]


def test_text_query_with_videos_shares_one_vector(monkeypatch, tmp_path):
    _root, store = _setup(monkeypatch, tmp_path)
    _seed(store, "作品一.mp4", [_vector(0, 0.9)])
    calls = {"text": 0}

    def fake_texts(words):
        calls["text"] += 1
        return np.stack([np.full(512, 1 / np.sqrt(512), dtype=np.float32)] * len(words))

    monkeypatch.setattr(encoder, "encode_texts", fake_texts)
    monkeypatch.setattr(search, "_rank", lambda *a, **k: [])
    items, videos = search.text_query_with_videos("海边日落", k=5, videos_k=5)
    assert items == [] and len(videos) == 1
    assert calls["text"] == 1  # 图文与视频共用同一次编码


def test_frame_endpoint_reports_missing(monkeypatch, tmp_path):
    _root, store = _setup(monkeypatch, tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    response = client.get("/api/media/frame", params={"root": "r", "relpath": "没有.mp4", "ts": 0})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_video_endpoint_rejects_version_mismatch(monkeypatch, tmp_path):
    root, store = _setup(monkeypatch, tmp_path)
    (root / "作品一.mp4").write_bytes(b"fake-video")
    _seed(store, "作品一.mp4", [_vector(0, 0.9)], digest="b" * 64)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    response = client.get("/api/media/video", params={"root": "r", "relpath": "作品一.mp4", "version": "c" * 64})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_changed"
