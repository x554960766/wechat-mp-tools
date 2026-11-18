"""按新阈值重算历史机器结论。

不变量（对应 docs/specs/nsfw-config-management.md）：
1. 只重算 stage='first' 的机器结论：score 是模型给图片的度量，与阈值无关，
   改阈值只是重新比较一次，不重扫图片。
2. 人工复核（stage='second'）永远不动——阈值不是人能改的东西。
3. 边界 score == threshold 判 nsfw（与扫描期同一口径 >=），不能重算完和扫描结论打架。
4. 换过鉴黄方案就不能重算：两个方案的分数量纲不同，旧分数按新方案阈值比较没有意义。
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, media_routes
from app.main import app
from app.store import Store

ROOT = "articles-full"


def _embedding():
    vec = np.ones(512, dtype=np.float32)
    return vec / np.linalg.norm(vec)


def _client():
    return TestClient(app, base_url="http://127.0.0.1:8765")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": ROOT, "type": "local", "path": str(root), "writable": True},))
    db = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(media_routes, "STORE", db)
    return db


def _seed(store, relpath, score, label, *, stage="first", status="ok"):
    data = relpath.encode()  # 每个 relpath 用不同字节，免得内容摘要撞在一起
    store.add_image(ROOT, relpath, data, embedding=_embedding())
    sha = store.get(ROOT, relpath)["content_sha256"]
    store.set_nsfw(ROOT, relpath, sha, score=score, label=label, status=status, stage=stage)


def _label(store, relpath):
    rows = store.conn.execute(
        "SELECT label FROM nsfw_results WHERE relpath=? AND stage='first'", (relpath,)).fetchall()
    return [row["label"] for row in rows]


def test_raising_threshold_flips_borderline_to_safe(store):
    _seed(store, "a.png", 0.55, "nsfw")
    _seed(store, "b.png", 0.75, "nsfw")

    result = store.nsfw_recompute(0.65)

    assert result == {"updated": 1, "skipped": 1}
    assert _label(store, "a.png") == ["safe"]
    assert _label(store, "b.png") == ["nsfw"]


def test_lowering_threshold_flips_safe_to_nsfw(store):
    _seed(store, "a.png", 0.4, "safe")
    _seed(store, "b.png", 0.2, "safe")

    assert store.nsfw_recompute(0.35) == {"updated": 1, "skipped": 1}
    assert _label(store, "a.png") == ["nsfw"]
    assert _label(store, "b.png") == ["safe"]


def test_score_equal_to_threshold_counts_as_nsfw(store):
    """边界必须与扫描期同口径（score >= threshold），否则重算完和重扫结论不一致。"""
    _seed(store, "a.png", 0.5, "safe")

    assert store.nsfw_recompute(0.5) == {"updated": 1, "skipped": 0}
    assert _label(store, "a.png") == ["nsfw"]


def test_manual_review_is_never_touched(store):
    _seed(store, "a.png", 0.55, "nsfw")
    _seed(store, "a.png", 0.55, "safe", stage="second")

    store.nsfw_recompute(0.9)

    rows = dict(store.conn.execute(
        "SELECT stage,label FROM nsfw_results WHERE relpath='a.png'").fetchall())
    assert rows["first"] == "safe"
    assert rows["second"] == "safe"   # 人工判的 safe，没被阈值改成 nsfw


def test_rows_without_score_or_failed_status_are_skipped(store):
    _seed(store, "a.png", 0.55, "nsfw")
    _seed(store, "b.png", None, None, status="failed")

    result = store.nsfw_recompute(0.9)

    assert result == {"updated": 1, "skipped": 0}
    assert _label(store, "b.png") == [None]


def test_recompute_is_idempotent(store):
    _seed(store, "a.png", 0.55, "nsfw")

    assert store.nsfw_recompute(0.65) == {"updated": 1, "skipped": 0}
    assert store.nsfw_recompute(0.65) == {"updated": 0, "skipped": 1}


def test_api_recompute_applies_threshold_and_clears_stale(store):
    _seed(store, "a.png", 0.55, "nsfw")
    client = _client()
    client.put("/api/settings/nsfw", json={"threshold": 0.65})
    assert client.get("/api/settings/nsfw").json()["stale"]["threshold"] is True

    resp = client.post("/api/nsfw/recompute")

    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    body = client.get("/api/settings/nsfw").json()
    assert body["applied_threshold"] == pytest.approx(0.65)
    assert body["stale"]["threshold"] is False
    assert _label(store, "a.png") == ["safe"]


def test_api_rejects_recompute_after_provider_switch(store):
    _seed(store, "a.png", 0.55, "nsfw")
    client = _client()
    client.put("/api/settings/nsfw", json={"provider": "nsfwjs-mobilenet-v2"})

    resp = client.post("/api/nsfw/recompute")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "provider_changed"
    # 拒绝时不能有任何副作用
    assert _label(store, "a.png") == ["nsfw"]
