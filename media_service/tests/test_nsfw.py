"""NSFW 初筛：入库绑定内容版本、扫描期失败不影响扫描、人工复核不被覆盖。"""
import pathlib
from io import BytesIO

import numpy as np
from PIL import Image
import pytest

from app import config, encoder, media, review, scan
from app.store import Store


def _png(color):
    out = BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def _vector():
    return np.eye(1, 512, 0, dtype=np.float32)[0]


def _store(tmp_path, monkeypatch):
    """add_image 会校验媒体根已注册，纯库测试也要先挂一个本地根。"""
    root = tmp_path / "media"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": "r", "type": "local", "path": str(root), "writable": True},))
    return Store(tmp_path / "index.sqlite")


def _setup(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": "r", "type": "local", "path": str(root), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(scan, "STORE", store)
    monkeypatch.setattr(encoder, "encode_image", lambda data: _vector())
    return store


def test_set_nsfw_roundtrip(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.add_image("r", "a.png", b"data", embedding=_vector())
    sha = store.get("r", "a.png")["content_sha256"]
    store.set_nsfw("r", "a.png", sha, score=0.93, label="nsfw", model_version="v1")
    summary = store.nsfw_summary("r", "a.png")
    assert summary["score"] == pytest.approx(0.93)
    assert summary["label"] == "nsfw" and summary["status"] == "ok"
    assert summary["stage"] == "first"


def test_nsfw_upsert_is_single_row_per_content(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.add_image("r", "a.png", b"data", embedding=_vector())
    sha = store.get("r", "a.png")["content_sha256"]
    store.set_nsfw("r", "a.png", sha, score=0.9, label="nsfw")
    store.set_nsfw("r", "a.png", sha, score=0.1, label="safe")
    rows = store.conn.execute("SELECT COUNT(*) FROM nsfw_results").fetchone()[0]
    assert rows == 1
    assert store.nsfw_summary("r", "a.png")["label"] == "safe"


def test_nsfw_summary_ignores_stale_content(tmp_path, monkeypatch):
    """同一 relpath 换了内容后，旧判定不能再挂在文件上。"""
    store = _store(tmp_path, monkeypatch)
    store.add_image("r", "a.png", b"v1", embedding=_vector())
    old_sha = store.get("r", "a.png")["content_sha256"]
    store.set_nsfw("r", "a.png", old_sha, score=0.97, label="nsfw")
    store.add_image("r", "a.png", b"v2", embedding=_vector())
    assert store.get("r", "a.png")["content_sha256"] != old_sha
    assert store.nsfw_summary("r", "a.png") is None


def test_rerun_first_stage_keeps_human_review(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.add_image("r", "a.png", b"data", embedding=_vector())
    sha = store.get("r", "a.png")["content_sha256"]
    store.set_nsfw("r", "a.png", sha, score=0.91, label="nsfw")
    with store.lock:
        store.conn.execute("UPDATE nsfw_results SET reviewed_by=?, reviewed_at=? WHERE relpath='a.png'",
                           ("ops", "2026-11-16T00:00:00+00:00"))
        store.conn.commit()
    store.set_nsfw("r", "a.png", sha, score=0.95, label="nsfw")
    row = store.conn.execute("SELECT reviewed_by, reviewed_at, score FROM nsfw_results").fetchone()
    assert row["reviewed_by"] == "ops"
    assert row["reviewed_at"] == "2026-11-16T00:00:00+00:00"
    assert row["score"] == pytest.approx(0.95)


def test_scan_persists_first_stage(monkeypatch, tmp_path):
    store = _setup(monkeypatch, tmp_path)
    data = _png("red")
    monkeypatch.setattr(media, "read_bytes", lambda root_id, path: data)
    monkeypatch.setattr(media, "stat_entry", lambda root_id, path: media.MediaEntry(root_id, path, len(data), 2))
    monkeypatch.setattr(media, "list_entries", lambda root: ([media.MediaEntry(root, "a.png", len(data), 2)], True))
    monkeypatch.setattr(review, "score_image", lambda raw: {"score": 0.88, "label": "nsfw", "status": "ok"})
    counts = scan.scan_root("r")
    assert counts["status"] == "completed"
    assert counts["nsfw_first"] == 1 and counts["nsfw_flagged"] == 1
    summary = store.nsfw_summary("r", "a.png")
    assert summary["label"] == "nsfw" and summary["score"] == pytest.approx(0.88)


def test_scan_without_model_still_completes(monkeypatch, tmp_path):
    store = _setup(monkeypatch, tmp_path)
    data = _png("red")
    monkeypatch.setattr(media, "read_bytes", lambda root_id, path: data)
    monkeypatch.setattr(media, "stat_entry", lambda root_id, path: media.MediaEntry(root_id, path, len(data), 2))
    monkeypatch.setattr(media, "list_entries", lambda root: ([media.MediaEntry(root, "a.png", len(data), 2)], True))
    monkeypatch.setattr(review, "score_image", lambda raw: None)
    counts = scan.scan_root("r")
    assert counts["status"] == "completed"
    assert "nsfw_first" not in counts
    assert store.nsfw_summary("r", "a.png") is None


def test_review_exception_does_not_break_scan(monkeypatch, tmp_path):
    store = _setup(monkeypatch, tmp_path)
    data = _png("red")
    monkeypatch.setattr(media, "read_bytes", lambda root_id, path: data)
    monkeypatch.setattr(media, "stat_entry", lambda root_id, path: media.MediaEntry(root_id, path, len(data), 2))
    monkeypatch.setattr(media, "list_entries", lambda root: ([media.MediaEntry(root, "a.png", len(data), 2)], True))

    def boom(raw):
        raise RuntimeError("onnx 崩了")

    monkeypatch.setattr(review, "score_image", boom)
    counts = scan.scan_root("r")
    assert counts["status"] == "completed"
    assert counts["indexed_new"] == 1


def test_score_image_disabled_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "NSFW_SCAN", False)
    assert review.score_image(b"whatever") is None



def test_decorate_items_marks_nsfw_and_keeps_order(monkeypatch):
    """初筛标记只挂在结果上，不打乱排序。"""
    from app import media_routes

    monkeypatch.setattr(media_routes, "_refresh_roots", lambda: None)
    monkeypatch.setattr(media_routes, "_reachable", {"r": {"reachable": True, "checked_at": None}})
    monkeypatch.setattr(media_routes.thumbs, "cache_path", lambda root, relpath, version: pathlib.Path("/nonexistent"))
    monkeypatch.setattr(
        media_routes.STORE, "nsfw_summary",
        lambda root, relpath: {"score": 0.9, "label": "nsfw", "status": "ok",
                               "stage": "first", "nsfw_type": None, "reviewed_by": None},
    )
    items = [{"root": "r", "relpath": "a.png", "version": "v1"},
             {"root": "r", "relpath": "b.png", "version": "v1"}]
    out = media_routes._decorate_items(items)
    assert [i["relpath"] for i in out] == ["a.png", "b.png"]
    assert out[0]["nsfw"]["label"] == "nsfw" and out[1]["nsfw"]["score"] == 0.9


def test_checked_search_reuses_persisted_score(monkeypatch):
    """库里已有同内容版本的初筛结论时不再逐图推理。"""
    from app import media_routes

    monkeypatch.setattr(media_routes, "_decorate_items", lambda items: items)
    monkeypatch.setattr(media_routes.review, "status", lambda: "ready")

    def boom(data):
        raise AssertionError("命中缓存时不应重新推理")

    monkeypatch.setattr(media_routes.review, "review_bytes", boom)
    items = [{"root": "r", "relpath": "a.png", "version": "v1",
              "nsfw": {"score": 0.8, "label": "nsfw", "status": "ok"}}]
    out = media_routes._checked_search(items, True)
    assert out["items"][0]["review"] == {"score": 0.8, "label": "nsfw", "status": "ok", "cached": True}


def test_decorate_items_survives_store_error(monkeypatch):
    """单个文件查不到初筛结论时给 null，不让整个检索报错。"""
    from app import media_routes

    monkeypatch.setattr(media_routes, "_refresh_roots", lambda: None)
    monkeypatch.setattr(media_routes, "_reachable", {"r": {"reachable": True, "checked_at": None}})
    monkeypatch.setattr(media_routes.thumbs, "cache_path", lambda root, relpath, version: pathlib.Path("/nonexistent"))

    def broken(root, relpath):
        raise RuntimeError("db gone")

    monkeypatch.setattr(media_routes.STORE, "nsfw_summary", broken)
    out = media_routes._decorate_items([{"root": "r", "relpath": "a.png", "version": "v1"}])
    assert out[0]["nsfw"] is None
