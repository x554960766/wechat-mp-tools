"""内容条目的 NSFW 聚合：卡片角标与详情图片标记的数据来源。

条目本身没有风险属性，风险是图片级判定；这里只验证聚合口径——
按 images.content_id 汇总、只认与图片当前内容摘要一致的结论、且完全不参与排序。
"""
import json
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

from app import config, contents, media, media_routes, thumbs
from app.store import Store

ROOT = "articles-full"


def _embedding():
    """固定单位向量：本组测试只关心聚合口径，不需要真的跑 CLIP。"""
    vec = np.ones(512, dtype=np.float32)
    return vec / np.linalg.norm(vec)


def _png(color):
    out = BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def _article(root, account, title, image_count=2):
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


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": ROOT, "type": "local", "path": str(root), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(media_routes, "STORE", store)
    return store, root


def _index(store, root, account="半夏Floom", title="手机壁纸｜海边日落"):
    """先用真实发现逻辑落内容条目，再逐张登记图片（图片先入库才能挂到条目上）。"""
    _article(root, account, title)
    files, _ = media.list_files(ROOT)
    items = contents.discover(ROOT, files)
    assert len(items) == 1
    item = items[0]
    for entry in files:
        if entry.relpath.endswith(".jpg"):
            store.add_image(ROOT, entry.relpath, _read(root, entry.relpath), embedding=_embedding())
    assert store.sync_contents(items)["new"] == 1
    return item["content_id"]


def _read(root, relpath):
    return (root / relpath).read_bytes()


def _image_relpaths(root, title, account="半夏Floom", count=2):
    return [f"{account}/{title}/media/img_{i:03d}.jpg" for i in range(1, count + 1)]


def _sha(store, relpath):
    return store.get(ROOT, relpath)["content_sha256"]


def test_flags_count_images_of_the_entry_only(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    content_id = _index(store, root)
    first, second = _image_relpaths(root, "手机壁纸｜海边日落")

    store.set_nsfw(ROOT, first, _sha(store, first), score=0.91, label="nsfw")
    store.set_nsfw(ROOT, second, _sha(store, second), score=0.02, label="safe")

    assert store.content_nsfw_flags([content_id]) == {content_id: {"total": 2, "flagged": 1}}
    # 没有结论的条目不出现在结果里，前端据此退回「无角标」
    assert store.content_nsfw_flags(["nope"]) == {}
    assert store.content_nsfw_flags([]) == {}


def test_flags_drop_conclusions_of_older_content_version(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    content_id = _index(store, root)
    first, _ = _image_relpaths(root, "手机壁纸｜海边日落")
    store.set_nsfw(ROOT, first, _sha(store, first), score=0.91, label="nsfw")

    # 同一路径换成新图：摘要变了，旧判定不能继续算在条目头上
    (root / first).write_bytes(_png("red"))
    store.add_image(ROOT, first, _read(root, first), embedding=_embedding())

    assert store.content_nsfw_flags([content_id]) == {content_id: {"total": 2, "flagged": 0}}


def test_decorate_keeps_order_and_only_attaches_marks(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    content_id = _index(store, root)
    first, _ = _image_relpaths(root, "手机壁纸｜海边日落")
    rows, total, terms = store.search_contents("")
    before = [row["content_id"] for row in rows]

    store.set_nsfw(ROOT, first, _sha(store, first), score=0.91, label="nsfw")
    rows, total, terms = store.search_contents("")
    items = media_routes._decorate_contents([media_routes._content_out(row, terms) for row in rows])

    assert [item["content_id"] for item in items] == before, "打标不得改变检索顺序"
    assert items[0]["nsfw"] == {"total": 2, "flagged": 1, "stage": "first"}


def test_detail_images_carry_their_own_marks(tmp_path, monkeypatch):
    store, root = _setup(tmp_path, monkeypatch)
    content_id = _index(store, root)
    first, second = _image_relpaths(root, "手机壁纸｜海边日落")
    store.set_nsfw(ROOT, first, _sha(store, first), score=0.88, label="nsfw")

    data = media_routes.contents_detail(content_id)
    marks = {image["relpath"]: image["nsfw"] for image in data["images"]}

    assert marks[first]["label"] == "nsfw" and marks[first]["score"] == 0.88
    assert marks[second] is None
    assert data["nsfw"] == {"total": 2, "flagged": 1, "stage": "first"}


def test_cover_thumb_asks_for_a_size_the_cache_implements(tmp_path, monkeypatch):
    """封面 URL 里的 size 必须是缩略图缓存真正实现的那一种。

    缓存只实现 size=360；写成别的尺寸时每张卡片封面都会拿到 422（本次实测踩到过），
    所以这里不只比对字符串，而是真按 URL 里的参数取一次缩略图。
    """
    store, root = _setup(tmp_path, monkeypatch)
    _index(store, root)
    monkeypatch.setattr(config, "THUMB_DIR", str(tmp_path / "thumbs"))
    # thumbs 内部是 `from .store import STORE` 现取模块属性，测试库要同步换过去
    from app import store as store_module
    monkeypatch.setattr(store_module, "STORE", store)
    rows, _total, terms = store.search_contents("")
    cover = media_routes._content_out(rows[0], terms)["cover"]
    assert cover, "公众号条目应带封面"

    # _index 登记图片时没写 mtime（默认 0），缩略图会判成源文件已变化；
    # 这里按真实 mtime 重新登记一次，让缩略图链路能真的走通。
    path = root / cover["relpath"]
    store.add_image(ROOT, cover["relpath"], path.read_bytes(),
                    mtime=path.stat().st_mtime, embedding=_embedding())
    rows, _total, terms = store.search_contents("")
    cover = media_routes._content_out(rows[0], terms)["cover"]

    size = int(parse_qs(urlparse(cover["thumb"]).query)["size"][0])
    data, _cached = thumbs.thumbnail(ROOT, cover["relpath"], cover["version"], size)
    assert data


def test_item_carries_stable_work_key(tmp_path, monkeypatch):
    """条目要带「平台|作品 ID」身份：宿主 NAS 归档台账与标签共享都按它对账。"""
    store, root = _setup(tmp_path, monkeypatch)
    _index(store, root)
    rows, _total, terms = store.search_contents("")
    item = media_routes._content_out(rows[0], terms)
    assert item["work_key"].startswith("wechat|https://mp.weixin.qq.com/s/")
