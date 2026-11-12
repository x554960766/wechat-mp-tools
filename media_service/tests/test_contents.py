"""内容条目索引：六类下载目录的发现、全文检索、图片挂接与清理。"""
import json
from io import BytesIO
import numpy as np
from PIL import Image

from app import config, contents, media, scan
from app.store import Store


def _store(tmp_path, monkeypatch, roots):
    monkeypatch.setattr(config, "MEDIA_ROOTS", tuple(roots))
    return Store(tmp_path / "index.sqlite")


def _wechat(root, account, title, body="翰墨修心，笃行致远。"):
    article = root / account / title
    (article / "media").mkdir(parents=True)
    (article / "metadata.json").write_text(json.dumps({
        "title": title, "url": "https://mp.weixin.qq.com/s/x",
        "publish_time": 1762771200, "source": account, "digest": "摘要",
    }, ensure_ascii=False), encoding="utf-8")
    (article / "content.txt").write_text(body, encoding="utf-8")
    out = BytesIO()
    Image.new("RGB", (8, 8), "white").save(out, "PNG")
    (article / "media" / "img_001.jpg").write_bytes(out.getvalue())
    return article


def test_tokenize_keeps_chinese_unigrams_bigrams_and_ascii_words():
    tokens = contents.tokenize("翰墨修心 4K Wallpaper")
    assert "翰" in tokens and "翰墨" in tokens and "墨修" in tokens
    assert "4k" in tokens and "wallpaper" in tokens
    assert len(tokens) == len(set(tokens))  # 去重后保持稳定输出


def test_wechat_article_uses_content_and_directory(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    _wechat(root, "半夏Floom", "手机壁纸｜翰墨修心")
    store = _store(tmp_path, monkeypatch, [{"id": "articles-full", "type": "local", "path": str(root), "writable": True}])
    files, complete = media.list_files("articles-full")
    assert complete
    items = contents.discover("articles-full", files)
    assert len(items) == 1
    item = items[0]
    assert (item["platform"], item["kind"], item["account"]) == ("wechat", "article", "半夏Floom")
    assert item["title"] == "手机壁纸｜翰墨修心"
    assert item["body"].startswith("翰墨修心")
    assert item["cover_relpath"].endswith("media/img_001.jpg")
    assert item["media_count"] == 1
    assert item["published_at"].startswith("2025-")


def test_channels_video_is_a_top_level_entry(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    (root / "channels").mkdir(parents=True)
    (root / "channels" / "海边日落.mp4").write_bytes(b"\x00")
    _store(tmp_path, monkeypatch, [{"id": "articles-full", "type": "local", "path": str(root), "writable": True}])
    files, _ = media.list_files("articles-full")
    items = contents.discover("articles-full", files)
    assert len(items) == 1 and items[0]["platform"] == "channels"
    assert items[0]["kind"] == "video" and items[0]["title"] == "海边日落"


def test_douyin_gallery_account_is_the_author(tmp_path, monkeypatch):
    root = tmp_path / "dy"
    gallery = root / "小鲸鱼" / "7412345678901234568_手写便签"
    gallery.mkdir(parents=True)
    (gallery / "1.jpeg").write_bytes(b"\xff\xd8\xff")
    (gallery / "2.jpeg").write_bytes(b"\xff\xd8\xff")
    (root / "小鲸鱼" / "7412345678901234567_雪山日落.mp4").write_bytes(b"\x00")
    _store(tmp_path, monkeypatch, [{"id": "douyin", "type": "local", "path": str(root), "writable": True}])
    files, _ = media.list_files("douyin")
    items = sorted(contents.discover("douyin", files), key=lambda i: i["title"])
    assert [(i["kind"], i["title"], i["account"]) for i in items] == [
        ("gallery", "手写便签", "小鲸鱼"),
        ("video", "雪山日落", "小鲸鱼"),
    ]
    assert items[0]["media_count"] == 2


def test_bilibili_uses_bv_directory_and_poster(tmp_path, monkeypatch):
    root = tmp_path / "bili"
    bv = root / "影视飓风" / "一次看懂摄影_BV1Ab411c7De"
    bv.mkdir(parents=True)
    (bv / "一次看懂摄影.mp4").write_bytes(b"\x00")
    (bv / "一次看懂摄影-poster.jpg").write_bytes(b"\xff\xd8\xff")
    (bv / "一次看懂摄影.srt").write_text("1\n", encoding="utf-8")
    _store(tmp_path, monkeypatch, [{"id": "bilibili", "type": "local", "path": str(root), "writable": True}])
    files, _ = media.list_files("bilibili")
    items = contents.discover("bilibili", files)
    assert len(items) == 1
    item = items[0]
    assert (item["platform"], item["kind"], item["title"], item["account"]) == (
        "bilibili", "video", "一次看懂摄影", "影视飓风")
    assert item["cover_relpath"].endswith("-poster.jpg")


def test_xhs_note_reads_header_and_body(tmp_path, monkeypatch):
    root = tmp_path / "xhs"
    note = root / "黄油相机" / "2026-11-10_周末咖啡馆"
    note.mkdir(parents=True)
    (note / "文案.txt").write_text(
        "标题: 周末咖啡馆\n作者: 黄油相机\n发布时间: 2026-11-10 12:30\n类型: 图文\n"
        "链接: https://www.xiaohongshu.com/explore/abc\n点赞: 120 收藏: 30 评论: 8 分享: 2\n\n"
        "今天去了一家很安静的咖啡馆。\n", encoding="utf-8")
    (note / "01.jpg").write_bytes(b"\xff\xd8\xff")
    _store(tmp_path, monkeypatch, [{"id": "xhs", "type": "local", "path": str(root), "writable": True}])
    files, _ = media.list_files("xhs")
    items = contents.discover("xhs", files)
    assert len(items) == 1
    item = items[0]
    assert (item["platform"], item["kind"], item["account"]) == ("xhs", "note", "黄油相机")
    assert item["title"] == "周末咖啡馆"
    assert item["url"].endswith("/explore/abc")
    assert "安静的咖啡馆" in item["body"]


def test_sync_search_prune_and_image_binding(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    _wechat(root, "半夏Floom", "手机壁纸｜翰墨修心")
    store = _store(tmp_path, monkeypatch, [{"id": "articles-full", "type": "local", "path": str(root), "writable": True}])
    vector = np.eye(1, 512, 0, dtype=np.float32)[0]
    store.add_image("articles-full", "半夏Floom/手机壁纸｜翰墨修心/media/img_001.jpg", b"x", embedding=vector)

    files, _ = media.list_files("articles-full")
    items = contents.discover("articles-full", files)
    assert store.sync_contents(items, prune_root="articles-full") == {
        "new": 1, "updated": 0, "unchanged": 0, "removed": 0}

    image = store.conn.execute("SELECT content_id FROM images").fetchone()
    assert image["content_id"] == items[0]["content_id"]

    hits, total, terms = store.search_contents("翰墨")
    assert total == 1 and hits[0]["title"] == "手机壁纸｜翰墨修心" and "翰墨" in terms
    assert store.search_contents("翰墨", account="半夏Floom")[1] == 1
    assert store.search_contents("翰墨", account="别的号")[1] == 0
    assert store.search_contents("", platform="wechat")[1] == 1

    detail = store.content_detail(items[0]["content_id"])
    assert detail["body"].startswith("翰墨修心") and len(detail["images"]) == 1

    assert store.sync_contents([], prune_root="articles-full")["removed"] == 1
    assert store.content_facets()["total"] == 0
    assert store.conn.execute("SELECT content_id FROM images").fetchone()["content_id"] is None


def test_scan_indexes_contents_without_touching_images(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    _wechat(root, "半夏Floom", "手机壁纸｜翰墨修心")
    (root / "channels").mkdir()
    (root / "channels" / "海边日落.mp4").write_bytes(b"\x00")
    store = _store(tmp_path, monkeypatch, [{"id": "articles-full", "type": "local", "path": str(root), "writable": True}])
    monkeypatch.setattr(scan, "STORE", store)
    monkeypatch.setattr(scan.encoder, "encode_image", lambda data: np.eye(1, 512, 0, dtype=np.float32)[0])

    result = scan.scan_root("articles-full")
    assert result["contents"]["new"] == 2
    assert store.content_facets()["total"] == 2
    assert result["indexed_new"] >= 1  # media/ 下的图仍走图片索引


def test_detail_route_exposes_contract_image_keys(tmp_path, monkeypatch):
    """详情接口 images[] 必须带契约键（content_version/size/shot_at），同时保留前端用的 thumb/file。"""
    from app import media_routes
    root = tmp_path / "wx"
    _wechat(root, "半夏Floom", "手机壁纸｜翰墨修心")
    store = _store(tmp_path, monkeypatch, [{"id": "articles-full", "type": "local", "path": str(root), "writable": True}])
    monkeypatch.setattr(scan, "STORE", store)
    monkeypatch.setattr(scan.encoder, "encode_image", lambda data: np.eye(1, 512, 0, dtype=np.float32)[0])
    scan.scan_root("articles-full")
    content_id = store.conn.execute("SELECT content_id FROM contents").fetchone()[0]
    monkeypatch.setattr(media_routes, "STORE", store)

    data = media_routes.contents_detail(content_id)
    assert len(data["images"]) == 1
    image = data["images"][0]
    assert {"root", "relpath", "content_version", "size", "shot_at"} <= set(image)
    assert image["content_version"] and image["size"] > 0
    assert image["thumb"].startswith("/api/media/thumb?")
    assert image["file"].startswith("/api/media/file?")
