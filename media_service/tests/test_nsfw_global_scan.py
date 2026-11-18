"""全局 NSFW 检测：候选筛选、覆盖率、落库与接口错误口径。

不变量（对应 docs/specs/图片鉴黄与VR全景预览.md「全局重检」）：
1. 默认只处理「当前内容还没有 first=ok 结论」的图片，force 时全部重跑。
2. 源文件摘要与索引不一致时跳过，不写结论——否则结论会挂到别的版本上。
3. 模型不可用/检测关闭时发起接口返回 503 带明确 code，不假装成功。
4. 机器重检只写 first 行，人工 second 结论不被覆盖。
"""
from __future__ import annotations

import hashlib
from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, media, media_routes, nsfw_scan, review
from app.main import app
from app.store import Store

ROOT = "articles-full"


def _embedding():
    vec = np.ones(512, dtype=np.float32)
    return vec / np.linalg.norm(vec)


def _png(color="white"):
    out = BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": ROOT, "type": "local", "path": str(root), "writable": True},))
    monkeypatch.setattr(config, "NSFW_SCAN", True)
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(media_routes, "STORE", store)
    monkeypatch.setattr(nsfw_scan, "STORE", store)
    return store, root


def _add(store, root, relpath, data=None):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _png() if data is None else data
    path.write_bytes(data)
    store.add_image(ROOT, relpath, data, embedding=_embedding())
    return path


def _client():
    # 中间件只放行本机端口与同源写请求，与既有 two_stage 测试同一口径。
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _score_nsfw(monkeypatch):
    """把模型推理替换成固定分数，避免测试依赖权重文件。"""
    monkeypatch.setattr(review, "available", lambda: True)
    monkeypatch.setattr(review, "review_bytes",
                        lambda data: {"score": 0.91, "label": "nsfw", "status": "ok"})


def test_targets_default_skips_scored_and_force_includes_all(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    _add(store, root, "a.png")
    _add(store, root, "b.png")
    digest = store.get(ROOT, "a.png")["content_sha256"]
    store.set_nsfw(ROOT, "a.png", digest, score=0.1, label="normal",
                   model_version=config.NSFW_MODEL_SOURCE)

    assert [row["relpath"] for row in store.nsfw_targets()] == ["b.png"]
    assert [row["relpath"] for row in store.nsfw_targets(force=True)] == ["a.png", "b.png"]
    assert store.nsfw_coverage() == {"total": 2, "scored": 1, "pending": 1}


def test_scan_writes_first_result_and_counts_flags(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    _add(store, root, "a.png")
    _add(store, root, "b.png")
    _score_nsfw(monkeypatch)

    counts = nsfw_scan.scan()

    assert counts["status"] == "completed"
    assert (counts["scored"], counts["flagged"], counts["failed"]) == (2, 2, 0)
    assert store.nsfw_coverage()["pending"] == 0
    assert store.nsfw_summary(ROOT, "a.png")["label"] == "nsfw"
    assert store.nsfw_flagged(include_reviewed=False)["total"] == 2


def test_scan_skips_changed_source_instead_of_mismatching(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    path = _add(store, root, "a.png")
    _score_nsfw(monkeypatch)
    # 磁盘内容变了但索引还没重扫：摘要不一致时不能写结论。
    path.write_bytes(_png("black"))

    counts = nsfw_scan.scan()

    assert (counts["scored"], counts["skipped"], counts["failed"]) == (0, 1, 0)
    assert store.nsfw_coverage()["pending"] == 1


def test_scan_keeps_manual_review_authoritative(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    _add(store, root, "a.png")
    digest = store.get(ROOT, "a.png")["content_sha256"]
    store.set_nsfw(ROOT, "a.png", digest, score=0.9, label="nsfw",
                   model_version=config.NSFW_MODEL_SOURCE)
    store.set_nsfw(ROOT, "a.png", digest, score=0.9, label="normal", stage="second",
                   model_version="human", reviewed_by="tester")
    _score_nsfw(monkeypatch)

    nsfw_scan.scan(force=True)

    assert store.nsfw_summary(ROOT, "a.png")["label"] == "normal"   # 人工误报仍然生效
    assert store.nsfw_flagged(include_reviewed=False)["total"] == 0


def test_scan_fails_loudly_without_model(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(review, "available", lambda: False)
    with pytest.raises(RuntimeError, match="nsfw_model_missing"):
        nsfw_scan.scan()


def test_scan_records_per_file_failures_without_aborting(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    _add(store, root, "bad.png")
    _add(store, root, "good.png")
    _score_nsfw(monkeypatch)
    real_read = nsfw_scan.media.read_bytes

    def _read(root_id, relpath):
        if relpath == "bad.png":
            raise OSError("远端不可达")
        return real_read(root_id, relpath)

    monkeypatch.setattr(nsfw_scan.media, "read_bytes", _read)
    counts = nsfw_scan.scan()

    assert counts["status"] == "partial"
    assert (counts["scored"], counts["failed"]) == (1, 1)
    assert counts["errors"][0]["path"] == "bad.png"


def test_api_rejects_when_detection_disabled(tmp_path, monkeypatch):
    store, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "NSFW_SCAN", False)

    resp = _client().post("/api/nsfw/scan", json={})

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "nsfw_scan_disabled"
    assert store.stats()["total"] == 0


def test_api_status_reports_coverage_and_model(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    _add(store, root, "a.png")
    monkeypatch.setattr(review, "available", lambda: True)

    data = _client().get("/api/nsfw/status").json()

    assert data["model"] is True
    assert (data["total"], data["scored"], data["pending"]) == (1, 0, 1)


def test_api_enqueues_only_known_roots(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(review, "available", lambda: True)
    seen = {}

    class _Jobs:
        def enqueue_nsfw(self, roots, force):
            seen.update(roots=roots, force=force)
            return {"id": "job-1", "status": "queued"}

    monkeypatch.setattr(media_routes, "JOBS", _Jobs())
    client = _client()

    ok = client.post("/api/nsfw/scan", json={"force": True})
    assert ok.status_code == 202
    assert ok.json() == {"id": "job-1", "status": "queued"}
    assert seen == {"roots": [], "force": True}

    bad = client.post("/api/nsfw/scan", json={"roots": ["nope"]})
    assert bad.status_code == 422
