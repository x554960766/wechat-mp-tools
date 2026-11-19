"""两级 NSFW：机器初筛（first）与人工复核（second）的落库、优先级与接口契约。

核心不变量（对应 nas-archive-media-library §4.4「两级 NSFW 筛查 + 人工复核」）：
1. 人工结论写 second 行；机器重扫只 upsert first 行，人工结论不能被覆盖。
2. 有效结论（nsfw_summary / 内容角标 / 复核列表）second 优先于 first。
3. 人工标为误报后，内容角标归零、复核列表里消失。
4. 复核接口对未知根 / 未索引图片 / 非法 label 明确报错，不静默吞掉。
"""
from __future__ import annotations

import json
from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, media, media_routes
from app.main import app
from app.store import Store

ROOT = "articles-full"


def _embedding():
    vec = np.ones(512, dtype=np.float32)
    return vec / np.linalg.norm(vec)


def _png(color):
    out = BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": ROOT, "type": "local", "path": str(root), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(media_routes, "STORE", store)
    return store, root


def _add(store, root, relpath, color="white"):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png(color))
    store.add_image(ROOT, relpath, path.read_bytes(), embedding=_embedding())
    return store.get(ROOT, relpath)["content_sha256"]


def _article(root, account, title, image_count=1):
    article = root / account / title
    (article / "media").mkdir(parents=True)
    (article / "metadata.json").write_text(json.dumps({
        "title": title, "url": f"https://mp.weixin.qq.com/s/{title}",
        "publish_time": 1762771200, "source": account, "digest": "摘要",
    }, ensure_ascii=False), encoding="utf-8")
    (article / "content.txt").write_text("海边日落，潮水退去。", encoding="utf-8")
    for i in range(1, image_count + 1):
        (article / "media" / f"img_{i:03d}.jpg").write_bytes(_png("white" if i % 2 else "black"))
    return article


def _index(store, root, account="半夏Floom", title="手机壁纸｜海边日落"):
    from app import contents
    _article(root, account, title)
    files, _ = media.list_files(ROOT)
    items = contents.discover(ROOT, files)
    item = items[0]
    for entry in files:
        if entry.relpath.endswith(".jpg"):
            store.add_image(ROOT, entry.relpath, (root / entry.relpath).read_bytes(),
                            embedding=_embedding())
    store.sync_contents(items)
    return item["content_id"]


@pytest.fixture()
def client(monkeypatch):
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_second_stage_wins_and_survives_first_stage_rewrite(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    rel = "潮汐/浪花.png"
    sha = _add(store, root, rel)

    store.set_nsfw(ROOT, rel, sha, score=0.91, label="nsfw")
    assert store.nsfw_summary(ROOT, rel)["stage"] == "first"

    store.set_nsfw(ROOT, rel, sha, score=0.4, label="safe", stage="second",
                   reviewed_by="tester", reviewed_at="2026-11-18T00:00:00+00:00")
    summary = store.nsfw_summary(ROOT, rel)
    assert summary["stage"] == "second" and summary["label"] == "safe"
    assert summary["reviewed_by"] == "tester"

    # 重扫：机器再写 first 行，人工 second 行不能被改掉
    store.set_nsfw(ROOT, rel, sha, score=0.99, label="nsfw")
    summary = store.nsfw_summary(ROOT, rel)
    assert summary["stage"] == "second" and summary["label"] == "safe"
    assert summary["reviewed_by"] == "tester"


def test_machine_call_does_not_clear_reviewer_on_same_stage(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    rel = "潮汐/浪花.png"
    sha = _add(store, root, rel)
    store.set_nsfw(ROOT, rel, sha, score=0.9, label="nsfw", stage="second",
                   reviewed_by="tester", reviewed_at="2026-11-18T00:00:00+00:00")

    # 同 stage 的机器调用不传 reviewed_*，COALESCE 保留已有复核人
    store.set_nsfw(ROOT, rel, sha, score=0.8, label="nsfw", stage="second")
    summary = store.nsfw_summary(ROOT, rel)
    assert summary["reviewed_by"] == "tester"
    assert summary["score"] == pytest.approx(0.8)


def test_content_flags_follow_human_conclusion(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    content_id = _index(store, root)
    rel = "半夏Floom/手机壁纸｜海边日落/media/img_001.jpg"
    sha = store.get(ROOT, rel)["content_sha256"]

    store.set_nsfw(ROOT, rel, sha, score=0.91, label="nsfw")
    assert store.content_nsfw_flags([content_id]) == {content_id: {"total": 1, "flagged": 1}}

    # 人工标为误报：角标归零（有效结论按 second 优先）
    store.set_nsfw(ROOT, rel, sha, score=0.91, label="safe", stage="second",
                   reviewed_by="tester", reviewed_at="2026-11-18T00:00:00+00:00")
    assert store.content_nsfw_flags([content_id]) == {content_id: {"total": 1, "flagged": 0}}


def test_flagged_list_uses_effective_label_and_hides_false_positives(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    hit = "a/命中.png"
    miss = "a/正常.png"
    sha_hit = _add(store, root, hit)
    sha_miss = _add(store, root, miss, color="black")
    store.set_nsfw(ROOT, hit, sha_hit, score=0.93, label="nsfw")
    store.set_nsfw(ROOT, miss, sha_miss, score=0.05, label="safe")

    result = store.nsfw_flagged(ROOT)
    assert result["total"] == 1
    assert [item["relpath"] for item in result["items"]] == [hit]

    # 人工复核为误报后从列表消失；未复核过滤出的行也不该再出现
    store.set_nsfw(ROOT, hit, sha_hit, score=0.93, label="safe", stage="second",
                   reviewed_by="tester", reviewed_at="2026-11-18T00:00:00+00:00")
    assert store.nsfw_flagged(ROOT)["total"] == 0
    assert store.nsfw_flagged(ROOT, include_reviewed=False)["total"] == 0


def test_review_endpoint_writes_second_stage(tmp_path, monkeypatch, client):
    store, root = _setup(tmp_path, monkeypatch)
    rel = "潮汐/浪花.png"
    sha = _add(store, root, rel)
    store.set_nsfw(ROOT, rel, sha, score=0.88, label="nsfw")

    response = client.post("/api/nsfw/review", json={
        "root": ROOT, "relpath": rel, "label": "safe", "reviewed_by": "tester"})
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "second"
    assert body["nsfw"]["label"] == "safe"
    assert body["nsfw"]["reviewed_by"] == "tester"
    # 复核保留机器分数，便于事后看「被人工推翻的是多高的分」
    assert body["nsfw"]["score"] == pytest.approx(0.88)

    # 人工确认敏感：退出待复核队列，但 include_reviewed 视图里仍可见
    confirmed = client.post("/api/nsfw/review", json={
        "root": ROOT, "relpath": rel, "label": "nsfw", "reviewed_by": "tester"})
    assert confirmed.status_code == 200
    assert confirmed.json()["nsfw"]["label"] == "nsfw"
    assert store.nsfw_flagged(ROOT, include_reviewed=False)["total"] == 0
    visible = store.nsfw_flagged(ROOT, include_reviewed=True)
    assert visible["total"] == 1
    assert visible["items"][0]["stage"] == "second"
    assert visible["items"][0]["reviewed_by"] == "tester"


def test_review_endpoint_rejects_bad_input(tmp_path, monkeypatch, client):
    store, root = _setup(tmp_path, monkeypatch)
    _add(store, root, "潮汐/浪花.png")

    unknown = client.post("/api/nsfw/review", json={
        "root": "nope", "relpath": "潮汐/浪花.png", "label": "safe"})
    assert unknown.status_code == 422

    missing = client.post("/api/nsfw/review", json={
        "root": ROOT, "relpath": "不存在.png", "label": "safe"})
    assert missing.status_code == 404

    bad_label = client.post("/api/nsfw/review", json={
        "root": ROOT, "relpath": "潮汐/浪花.png", "label": "maybe"})
    assert bad_label.status_code == 422

    bad_type = client.post("/api/nsfw/review", json={
        "root": ROOT, "relpath": "潮汐/浪花.png", "label": "nsfw", "nsfw_type": "nude"})
    assert bad_type.status_code == 422


def test_flagged_endpoint_returns_media_urls(tmp_path, monkeypatch, client):
    store, root = _setup(tmp_path, monkeypatch)
    rel = "潮汐/浪花.png"
    sha = _add(store, root, rel)
    store.set_nsfw(ROOT, rel, sha, score=0.93, label="nsfw")

    response = client.get("/api/nsfw/flagged", params={"root": ROOT})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["relpath"] == rel
    assert item["file"].startswith("/api/media/file?")
    assert item["thumb"].startswith("/api/media/thumb?")

    assert client.get("/api/nsfw/flagged", params={"root": "nope"}).status_code == 422
