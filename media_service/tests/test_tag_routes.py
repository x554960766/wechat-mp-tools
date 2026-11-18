"""标签 HTTP 出入口：文件标签与作品标签的端点契约。

存储层语义（按 content_sha256 / work_key 共享）已由 ``test_tags.py`` 覆盖，这里只断言
「HTTP 层把语义原样透出、错误明确」：

1. 打/去标签返回变更后的完整列表，重复打标签幂等；
2. 同内容的其他副本立刻可见（这是标签敢挂在摘要上的理由）；
3. 列表行带 ``thumb`` / ``file`` 地址与 ``version``，可直接喂给现有图片查看器；
4. 未知根 / 未索引文件 / 空标签 / 超长标签 / 非法 mode 各自明确报错，不静默吞掉；
5. 作品标签按 work_key 共享，按标签列作品只出一条且带封面契约。
"""
from __future__ import annotations

import json
from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, contents, media, media_routes, tag_routes
from app.main import app
from app.store import Store

LOCAL = "articles-full"
NAS = "nas-archive"
_EMB = np.eye(1, 512, 0, dtype=np.float32)[0]


def _png(color="white"):
    out = BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def _setup(tmp_path, monkeypatch, roots=(LOCAL, NAS)):
    """两个根指向同一份磁盘目录之外的独立目录；根 id 只用于隔离副本。"""
    dirs = {}
    for root_id in roots:
        path = tmp_path / root_id
        path.mkdir()
        dirs[root_id] = path
    monkeypatch.setattr(config, "MEDIA_ROOTS", tuple(
        {"id": root_id, "type": "local", "path": str(path), "writable": True}
        for root_id, path in dirs.items()))
    # 可达性探活走真实线程，测试里固定为在线并跳过刷新，避免断言依赖竞态
    monkeypatch.setattr(media_routes, "_refresh_roots", lambda: None)
    monkeypatch.setattr(media_routes, "_reachable",
                        {root_id: {"reachable": True, "checked_at": None} for root_id in dirs})
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(media_routes, "STORE", store)
    monkeypatch.setattr(tag_routes, "STORE", store)
    return store, dirs


def _add(store, root_id, path, relpath=None, data=None):
    relpath = relpath or path.name
    if data is None:
        data = _png()
    path.write_bytes(data)
    store.add_image(root_id, relpath, data, embedding=_EMB)
    return store.get(root_id, relpath)["content_sha256"]


def _wechat(root, account, title, url="https://mp.weixin.qq.com/s/x"):
    article = root / account / title
    (article / "media").mkdir(parents=True)
    (article / "metadata.json").write_text(json.dumps({
        "title": title, "url": url, "publish_time": 1762771200,
        "source": account, "digest": "摘要",
    }, ensure_ascii=False), encoding="utf-8")
    (article / "content.txt").write_text("翰墨修心，笃行致远。", encoding="utf-8")
    (article / "media" / "img_001.jpg").write_bytes(_png())
    return article


@pytest.fixture()
def client(monkeypatch):
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_image_tag_round_trip_via_http(tmp_path, monkeypatch, client):
    store, dirs = _setup(tmp_path, monkeypatch)
    _add(store, LOCAL, dirs[LOCAL] / "a.png")

    empty = client.get("/api/tags/image", params={"root": LOCAL, "relpath": "a.png"})
    assert empty.status_code == 200 and empty.json()["tags"] == []

    added = client.post("/api/tags/image", json={"root": LOCAL, "relpath": "a.png", "tag": " 精选 "})
    assert added.status_code == 200
    # 首尾空白被规整，中间内容原样保留
    assert added.json()["tags"] == ["精选"]

    # 重复打同一标签幂等，不会出现两条
    again = client.post("/api/tags/image", json={"root": LOCAL, "relpath": "a.png", "tag": "精选"})
    assert again.json()["tags"] == ["精选"]

    listing = client.get("/api/tags").json()
    assert listing["total"] == 1 and listing["items"] == [{"tag": "精选", "count": 1}]

    removed = client.post("/api/tags/image",
                          json={"root": LOCAL, "relpath": "a.png", "tag": "精选", "mode": "remove"})
    assert removed.json()["tags"] == []
    assert client.get("/api/tags").json()["items"] == []


def test_image_tag_visible_on_other_copy_of_same_content(tmp_path, monkeypatch, client):
    """归档到 NAS 的副本与本地是同一摘要：一处打标签，另一处立刻可见。"""
    store, dirs = _setup(tmp_path, monkeypatch)
    data = _png()
    _add(store, LOCAL, dirs[LOCAL] / "img.png", data=data)
    _add(store, NAS, dirs[NAS] / "img.png", data=data)

    client.post("/api/tags/image", json={"root": LOCAL, "relpath": "img.png", "tag": "精选"})

    other = client.get("/api/tags/image", params={"root": NAS, "relpath": "img.png"})
    assert other.json()["tags"] == ["精选"]

    rows = client.get("/api/tags/images", params={"tag": "精选"}).json()
    assert sorted(row["root"] for row in rows["items"]) == sorted([LOCAL, NAS])


def test_images_by_tag_rows_feed_viewer(tmp_path, monkeypatch, client):
    store, dirs = _setup(tmp_path, monkeypatch)
    _add(store, LOCAL, dirs[LOCAL] / "a.png", data=_png("white"))
    _add(store, LOCAL, dirs[LOCAL] / "b.png", data=_png("black"))
    for relpath in ("a.png", "b.png"):
        client.post("/api/tags/image", json={"root": LOCAL, "relpath": relpath, "tag": "壁纸"})

    body = client.get("/api/tags/images", params={"tag": "壁纸"}).json()
    assert len(body["items"]) == 2 and body["has_more"] is False
    item = body["items"][0]
    # 字段与搜索结果同构：查看器只认 version/file/root/relpath
    assert item["version"] == item["content_version"]
    assert item["file"].startswith("/api/media/file?")
    assert item["thumb"].startswith("/api/media/thumb?")
    assert item["relpath"] in ("a.png", "b.png")
    assert item["nsfw"] is None  # 未做过 NSFW 判定的图片不带伪结论

    # 存满一页时 has_more 为真，前端据此决定是否继续翻页
    paged = client.get("/api/tags/images", params={"tag": "壁纸", "limit": 1}).json()
    assert len(paged["items"]) == 1 and paged["has_more"] is True


@pytest.mark.parametrize("payload, status", [
    ({"root": "nope", "relpath": "a.png", "tag": "精选"}, 422),
    ({"root": LOCAL, "relpath": "missing.png", "tag": "精选"}, 404),
    ({"root": LOCAL, "relpath": "a.png", "tag": "   "}, 422),
    ({"root": LOCAL, "relpath": "a.png", "tag": "x" * 33}, 422),
    ({"root": LOCAL, "relpath": "a.png", "tag": "换\n行"}, 422),
    ({"root": LOCAL, "relpath": "a.png", "tag": "精选", "mode": "toggle"}, 422),
])
def test_tag_image_rejects_bad_input(tmp_path, monkeypatch, client, payload, status):
    store, dirs = _setup(tmp_path, monkeypatch)
    _add(store, LOCAL, dirs[LOCAL] / "a.png")
    assert client.post("/api/tags/image", json=payload).status_code == status


def test_content_tag_round_trip_and_listing(tmp_path, monkeypatch, client):
    store, dirs = _setup(tmp_path, monkeypatch)
    _wechat(dirs[LOCAL], "半夏Floom", "手机壁纸｜翰墨修心")
    _wechat(dirs[NAS], "半夏Floom", "手机壁纸｜翰墨修心")
    for root_id in (LOCAL, NAS):
        files, _ = media.list_files(root_id)
        store.sync_contents(contents.discover(root_id, files))

    local_id = store.search_contents("翰墨修心")[0][0]["content_id"]
    nas_id = next(row["content_id"] for row in store.search_contents("翰墨修心")[0]
                  if row["root"] == NAS)

    added = client.post("/api/tags/content", json={"content_id": local_id, "tag": "精选"})
    assert added.status_code == 200 and added.json()["tags"] == ["精选"]
    # work_key 相同的副本共享标签
    assert client.get("/api/tags/content", params={"content_id": nas_id}).json()["tags"] == ["精选"]

    listed = client.get("/api/tags/contents", params={"tag": "精选"}).json()
    assert listed["has_more"] is False and len(listed["items"]) == 1
    row = listed["items"][0]
    assert row["title"] == "手机壁纸｜翰墨修心"
    # 封面为 null 时前端退回占位，不能因为封面没入库就报错
    assert "cover" in row

    assert client.post("/api/tags/content", json={
        "content_id": local_id, "tag": "精选", "mode": "remove"}).json()["tags"] == []
    assert client.post("/api/tags/content", json={
        "content_id": "content-missing", "tag": "精选"}).status_code == 404
