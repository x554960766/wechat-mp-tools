"""扫描结果关联到文章：侧车齐全时写 article_id，缺失时照常入索引。"""
from io import BytesIO
import json

import numpy as np
from PIL import Image
import pytest

from app import article_link, config, encoder, media, scan
from app.store import Store


def _png(color):
    out = BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def _setup(monkeypatch, tmp_path, files, entries=None):
    root = tmp_path / "articles"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "r", "type": "local", "path": str(root), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(scan, "STORE", store)
    monkeypatch.setattr(media, "read_bytes", lambda root_id, path: files[path])
    monkeypatch.setattr(
        media,
        "stat_entry",
        lambda root_id, path: media.MediaEntry(root_id, path, len(files[path]), 2),
    )
    monkeypatch.setattr(encoder, "encode_image", lambda data: np.eye(1, 512, 0, dtype=np.float32)[0])
    relpaths = entries if entries is not None else sorted(
        p for p in files if p.rsplit(".", 1)[-1].lower() in ("png", "jpg", "jpeg", "webp")
    )
    monkeypatch.setattr(
        media,
        "list_entries",
        lambda root_id: ([media.MediaEntry(root_id, p, len(files[p]), 2) for p in relpaths], True),
    )
    return store


def _metadata(**over):
    meta = {
        "title": "标题",
        "url": "https://mp.weixin.qq.com/s?__biz=MzA5&mid=265&idx=1&sn=abc",
        "publish_time": "2026-11-01 08:00",
        "time": "2026-11-01T00:00:00+00:00",
        "source": "某账号",
    }
    meta.update(over)
    return json.dumps(meta, ensure_ascii=False).encode("utf-8")


def test_scan_links_image_to_article(monkeypatch, tmp_path):
    files = {
        "acct/title/media/img_001.png": _png("red"),
        "acct/title/metadata.json": _metadata(),
    }
    store = _setup(monkeypatch, tmp_path, files)

    result = scan.scan_root("r")

    assert result["status"] == "completed"
    row = store.get("r", "acct/title/media/img_001.png")
    assert row["article_id"] == article_link.article_id_for_key("MzA5:265:1")
    assert row["origin"] == "scan"
    article = store.conn.execute(
        "SELECT title, source_url FROM articles WHERE article_id=?", (row["article_id"],)
    ).fetchone()
    assert article["title"] == "标题"
    assert "mid=265" in article["source_url"]


def test_metadata_article_key_wins_over_url(monkeypatch, tmp_path):
    files = {
        "acct/title/media/img_001.png": _png("red"),
        # 显式 article_key 与 url 推出来的不同，以显式值为准
        "acct/title/metadata.json": _metadata(article_key="custom-key"),
    }
    store = _setup(monkeypatch, tmp_path, files)

    scan.scan_root("r")

    row = store.get("r", "acct/title/media/img_001.png")
    assert row["article_id"] == article_link.article_id_for_key("custom-key")


def test_missing_metadata_keeps_image_indexed(monkeypatch, tmp_path):
    files = {"acct/title/media/img_001.png": _png("red")}
    store = _setup(monkeypatch, tmp_path, files)

    result = scan.scan_root("r")

    assert result["status"] == "completed"
    row = store.get("r", "acct/title/media/img_001.png")
    assert row["state"] == "ready"
    assert row["article_id"] is None


def test_broken_metadata_does_not_fail_scan(monkeypatch, tmp_path):
    files = {
        "acct/title/media/img_001.png": _png("red"),
        "acct/title/metadata.json": b"{not json",
    }
    store = _setup(monkeypatch, tmp_path, files)

    result = scan.scan_root("r")

    assert result["status"] == "completed"
    assert store.get("r", "acct/title/media/img_001.png")["article_id"] is None


def test_metadata_read_once_per_article_directory(monkeypatch, tmp_path):
    files = {
        "acct/title/media/img_001.png": _png("red"),
        "acct/title/media/img_002.png": _png("blue"),
        "acct/title/metadata.json": _metadata(),
    }
    store = _setup(monkeypatch, tmp_path, files)
    reads = []
    original = media.read_bytes
    monkeypatch.setattr(
        media,
        "read_bytes",
        lambda root_id, path: (reads.append(path), original(root_id, path))[1],
    )

    scan.scan_root("r")

    assert reads.count("acct/title/metadata.json") == 1
    ids = {
        store.get("r", p)["article_id"]
        for p in ("acct/title/media/img_001.png", "acct/title/media/img_002.png")
    }
    assert ids == {article_link.article_id_for_key("MzA5:265:1")}


def test_rebuild_keeps_article_link(monkeypatch, tmp_path):
    files = {
        "acct/title/media/img_001.png": _png("red"),
        "acct/title/metadata.json": _metadata(),
    }
    store = _setup(monkeypatch, tmp_path, files)
    scan.scan_root("r")
    first = store.get("r", "acct/title/media/img_001.png")["article_id"]

    files["acct/title/media/img_001.png"] = _png("green")
    scan.scan_root("r", rebuild=True)

    assert store.get("r", "acct/title/media/img_001.png")["article_id"] == first


def test_same_article_across_directories_shares_one_row(monkeypatch, tmp_path):
    files = {
        "acct/篇目A/media/img_001.png": _png("red"),
        "acct/篇目A/metadata.json": _metadata(),
        "acct/篇目A-转载/media/img_001.png": _png("blue"),
        "acct/篇目A-转载/metadata.json": _metadata(),
    }
    _setup(monkeypatch, tmp_path, files)

    scan.scan_root("r")

    rows = scan.STORE.conn.execute("SELECT * FROM articles").fetchall()
    assert len(rows) == 1


def test_article_dir_requires_media_sibling():
    assert article_link.article_dir_of("a/b/media/cover.png") == "a/b"
    assert article_link.article_dir_of("a/b/cover.png") is None
    assert article_link.article_dir_of("cover.png") is None
    assert article_link.article_dir_of("a/media/x/y.png") is None
