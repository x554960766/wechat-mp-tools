"""标签域：文件标签按 content_sha256 共享，作品标签按 work_key 共享。"""
import json
from io import BytesIO

import numpy as np
from PIL import Image

from app import config, contents, media
from app.store import Store

_EMB = np.eye(1, 512, 0, dtype=np.float32)[0]


def _store(tmp_path, monkeypatch, roots):
    monkeypatch.setattr(config, "MEDIA_ROOTS", tuple(roots))
    return Store(tmp_path / "index.sqlite")


def _local(name, path):
    return {"id": name, "type": "local", "path": str(path), "writable": True}


def _png(color="white"):
    out = BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


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


def _sync_all(store, roots):
    for root_id, path in roots.items():
        files, _ = media.list_files(root_id)
        store.sync_contents(contents.discover(root_id, files))


def test_image_tag_add_list_and_remove(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    (root / "media").mkdir(parents=True)
    (root / "media" / "a.jpg").write_bytes(_png())
    store = _store(tmp_path, monkeypatch, [_local("wx", root)])
    store.add_image("wx", "media/a.jpg", _png(), embedding=_EMB)

    assert store.image_tags("wx", "media/a.jpg") == []
    assert store.tag_image("wx", "media/a.jpg", "精选") == ["精选"]
    assert store.image_tags("wx", "media/a.jpg") == ["精选"]
    assert store.list_tags() == [{"tag": "精选", "count": 1}]
    assert [row["relpath"] for row in store.images_by_tag("精选")] == ["media/a.jpg"]

    assert store.tag_image("wx", "media/a.jpg", "精选", mode="remove") == []
    assert store.list_tags() == []
    assert store.images_by_tag("精选") == []


def test_image_tags_shared_by_content_sha256(tmp_path, monkeypatch):
    """同一文件归档到 NAS 后是同内容摘要，标签对两个位置同时生效。"""
    local = tmp_path / "wx-local"
    nas = tmp_path / "wx-nas"
    local.mkdir(parents=True)
    nas.mkdir(parents=True)
    data = _png()
    (local / "img.jpg").write_bytes(data)
    (nas / "img.jpg").write_bytes(data)
    store = _store(tmp_path, monkeypatch, [_local("wx", local), _local("nas", nas)])
    store.add_image("wx", "img.jpg", data, embedding=_EMB)
    store.add_image("nas", "img.jpg", data, embedding=_EMB)

    store.tag_image("wx", "img.jpg", "精选")

    assert store.image_tags("nas", "img.jpg") == ["精选"]
    assert sorted(row["root"] for row in store.images_by_tag("精选")) == ["nas", "wx"]

    store.tag_image("nas", "img.jpg", "精选", mode="remove")
    assert store.image_tags("wx", "img.jpg") == []


def test_image_tags_survive_rescan_and_drop_on_content_change(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    (root / "media").mkdir(parents=True)
    (root / "media" / "a.jpg").write_bytes(_png("white"))
    store = _store(tmp_path, monkeypatch, [_local("wx", root)])
    store.add_image("wx", "media/a.jpg", _png("white"), embedding=_EMB)
    store.tag_image("wx", "media/a.jpg", "精选")

    # 同摘要重扫：content_sha256 不变，标签保留。
    result = store.add_image("wx", "media/a.jpg", _png("white"), embedding=_EMB)
    assert result["status"] == "unchanged"
    assert store.image_tags("wx", "media/a.jpg") == ["精选"]

    # 内容变化：新摘要，标签不继承，旧版本结论保留可追溯。
    store.add_image("wx", "media/a.jpg", _png("black"), embedding=_EMB)
    assert store.image_tags("wx", "media/a.jpg") == []


def test_tag_image_requires_indexed_file(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, [_local("wx", tmp_path / "wx")])
    try:
        store.tag_image("wx", "missing.jpg", "精选")
    except KeyError:
        pass
    else:
        raise AssertionError("未索引文件应抛 KeyError")


def test_content_tags_shared_by_work_key(tmp_path, monkeypatch):
    """同一作品本地与 NAS 副本（同 work_key）共享标签，列表只出一条。"""
    local = tmp_path / "wx-local"
    nas = tmp_path / "wx-nas"
    _wechat(local, "半夏Floom", "手机壁纸｜翰墨修心")
    _wechat(nas, "半夏Floom", "手机壁纸｜翰墨修心")
    store = _store(tmp_path, monkeypatch, [_local("wx", local), _local("nas", nas)])
    _sync_all(store, {"wx": local, "nas": nas})

    rows = store.search_contents("翰墨修心")
    assert rows[1] == 2
    local_row = next(r for r in rows[0] if r["root"] == "wx")
    nas_row = next(r for r in rows[0] if r["root"] == "nas")
    assert local_row["work_key"] == nas_row["work_key"] == "wechat|https://mp.weixin.qq.com/s/x"

    store.tag_content(local_row["content_id"], "精选")

    assert store.content_tags(nas_row["content_id"]) == ["精选"]
    listed = store.contents_by_tag("精选")
    assert len(listed) == 1
    assert listed[0]["title"] == "手机壁纸｜翰墨修心"

    store.tag_content(nas_row["content_id"], "精选", mode="remove")
    assert store.content_tags(local_row["content_id"]) == []
    assert store.contents_by_tag("精选") == []


def test_content_without_url_keeps_per_location_tags(tmp_path, monkeypatch):
    """没有稳定作品 ID 时 work_key 退化为 content_id，标签不跨位置传播。"""
    local = tmp_path / "wx-local"
    nas = tmp_path / "wx-nas"
    _wechat(local, "半夏Floom", "无链接作品", url="")
    _wechat(nas, "半夏Floom", "无链接作品", url="")
    store = _store(tmp_path, monkeypatch, [_local("wx", local), _local("nas", nas)])
    _sync_all(store, {"wx": local, "nas": nas})

    rows = store.search_contents("无链接作品")
    local_row = next(r for r in rows[0] if r["root"] == "wx")
    nas_row = next(r for r in rows[0] if r["root"] == "nas")
    assert local_row["work_key"] != nas_row["work_key"]

    store.tag_content(local_row["content_id"], "精选")
    assert store.content_tags(nas_row["content_id"]) == []


def test_discover_sets_stable_work_key(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    _wechat(root, "半夏Floom", "手机壁纸｜翰墨修心")
    (root / "channels").mkdir(parents=True, exist_ok=True)
    (root / "channels" / "海边日落.mp4").write_bytes(b"\x00")
    store = _store(tmp_path, monkeypatch, [_local("wx", root)])
    files, _ = media.list_files("wx")
    by_platform = {item["platform"]: item for item in contents.discover("wx", files)}
    assert by_platform["wechat"]["work_key"] == "wechat|https://mp.weixin.qq.com/s/x"
    assert by_platform["channels"]["work_key"] == "channels|海边日落.mp4"
