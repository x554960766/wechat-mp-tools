"""全局文件清单与内容查重：三表联合查询、按摘要归组、筛选与统计。"""
import hashlib

import numpy as np

from app import config
from app.store import Store


def _store(tmp_path, monkeypatch, roots):
    monkeypatch.setattr(config, "MEDIA_ROOTS", tuple(roots))
    return Store(tmp_path / "index.sqlite")


def _img(store, root, relpath, data, *, size=None):
    vec = np.zeros(512, dtype=np.float32)
    vec[0] = 1.0
    return store.add_image(root, relpath, data, embedding=vec, size=size)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def test_list_files_unions_images_texts_videos(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    store = _store(tmp_path, monkeypatch, [{"id": "wx", "type": "local", "path": str(root), "writable": True}])
    png = b"\x89PNG-shared-bytes"
    _img(store, "wx", "a/one.png", png, size=10)
    _img(store, "wx", "a/two.png", b"\x89PNG-other-bytes", size=20)
    store.sync_standalone_texts([
        {"root": "wx", "relpath": "a/one.txt", "content_sha256": _sha(b"note"), "kind": "txt",
         "title": "one", "body": "note"},
    ])
    store.replace_frames("wx", "a/clip.mp4", _sha(b"clip"), 3.5, [])

    rows, total = store.list_files()
    assert total == 4
    kinds = {r["kind"] for r in rows}
    assert kinds == {"image", "text", "video"}
    # 按大小降序；无大小记录（文本/视频）排末尾，再按根+路径稳定排序
    paths = [r["relpath"] for r in rows]
    assert paths == ["a/two.png", "a/one.png", "a/clip.mp4", "a/one.txt"]


def test_duplicate_groups_and_duplicate_count(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    nas = tmp_path / "nas"
    nas.mkdir()
    store = _store(tmp_path, monkeypatch, [
        {"id": "wx", "type": "local", "path": str(root), "writable": True},
        {"id": "nas", "type": "local", "path": str(nas), "writable": True},
    ])
    shared = b"identical-image-bytes"
    _img(store, "wx", "local/photo.png", shared)
    _img(store, "nas", "nas/photo.png", shared)
    _img(store, "wx", "local/unique.png", b"another-image")

    rows, total = store.list_files(duplicates="duplicates")
    assert total == 2
    assert all(r["dup_count"] == 2 for r in rows)

    rows_unique, total_unique = store.list_files(duplicates="unique")
    assert total_unique == 1
    assert rows_unique[0]["relpath"] == "local/unique.png"

    groups, group_total = store.duplicate_groups()
    assert group_total == 1
    assert groups[0]["sha"] == _sha(shared)
    assert groups[0]["count"] == 2
    assert {m["relpath"] for m in groups[0]["members"]} == {"local/photo.png", "nas/photo.png"}

    full = store.duplicate_group(_sha(shared))
    assert len(full) == 2
    assert store.duplicate_group("no-such-sha") == []


def test_list_files_filters_by_q_root_ext_and_kind(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    store = _store(tmp_path, monkeypatch, [{"id": "wx", "type": "local", "path": str(root), "writable": True}])
    _img(store, "wx", "shots/beach.jpg", b"jpg-bytes")
    _img(store, "wx", "shots/sunset.jpg", b"jpg-other")
    _img(store, "wx", "docs/page.png", b"png-bytes")
    store.sync_standalone_texts([
        {"root": "wx", "relpath": "docs/notes.txt", "content_sha256": _sha(b"x"), "kind": "txt",
         "title": "notes", "body": "x"},
    ])

    assert store.list_files(q="beach")[1] == 1
    assert store.list_files(ext="jpg")[1] == 2
    assert store.list_files(kind="text")[1] == 1
    assert store.list_files(kind="image")[1] == 3


def test_files_stats(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    store = _store(tmp_path, monkeypatch, [{"id": "wx", "type": "local", "path": str(root), "writable": True}])
    shared = b"dup-bytes"
    _img(store, "wx", "a/one.png", shared, size=50)
    _img(store, "wx", "a/two.png", shared, size=30)
    _img(store, "wx", "a/three.png", b"unique-bytes", size=10)
    store.sync_standalone_texts([
        {"root": "wx", "relpath": "a/one.txt", "content_sha256": _sha(b"x"), "kind": "txt",
         "title": "one", "body": "x"},
    ])

    stats = store.files_stats()
    assert stats["total"] == 4
    assert stats["duplicate_groups"] == 1
    assert stats["duplicate_files"] == 2
    assert stats["biggest"]["relpath"] == "a/one.png"
    assert {k["kind"]: k["count"] for k in stats["kinds"]} == {"image": 3, "text": 1}


def test_delete_file_clears_index_for_all_kinds(tmp_path, monkeypatch):
    """删除单个文件的索引：图片墓碑、文本删行清 FTS、视频删行清代表帧。"""
    root = tmp_path / "wx"
    root.mkdir()
    store = _store(tmp_path, monkeypatch, [{"id": "wx", "type": "local", "path": str(root), "writable": True}])
    _img(store, "wx", "a/one.png", b"png-bytes")
    _img(store, "wx", "a/two.png", b"png-other")
    store.sync_standalone_texts([
        {"root": "wx", "relpath": "a/one.txt", "content_sha256": _sha(b"x"), "kind": "txt",
         "title": "one", "body": "x"},
    ])
    store.replace_frames("wx", "a/one.mp4", _sha(b"clip"), 1.0, [])

    # 图片：墓碑而非删行，行身份保留但不再出现在清单
    report = store.delete_file("wx", "a/one.png")
    assert report == {"image": True, "text": False, "video": False, "removed": 1}
    assert store.list_files(q="one.png")[1] == 0
    assert store.conn.execute(
        "SELECT state FROM images WHERE root=? AND relpath=?", ("wx", "a/one.png")
    ).fetchone()["state"] == "deleted"

    # 文本：删行并清 FTS，清单与底层表都不残留
    assert store.delete_file("wx", "a/one.txt")["text"] is True
    assert store.list_files(kind="text")[1] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM standalone_texts").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM text_fts").fetchone()[0] == 0

    # 视频：删行并清代表帧
    assert store.delete_file("wx", "a/one.mp4")["video"] is True
    assert store.list_files(kind="video")[1] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM video_frames").fetchone()[0] == 0

    # 未被删除的其他文件不受影响
    assert store.list_files(q="two.png")[1] == 1


def test_delete_file_does_not_touch_unrelated_duplicates(tmp_path, monkeypatch):
    """删一份重复件只影响该行，同一摘要的其余成员与统计保持准确。"""
    root = tmp_path / "wx"
    root.mkdir()
    nas = tmp_path / "nas"
    nas.mkdir()
    store = _store(tmp_path, monkeypatch, [
        {"id": "wx", "type": "local", "path": str(root), "writable": True},
        {"id": "nas", "type": "local", "path": str(nas), "writable": True},
    ])
    shared = b"dup-bytes"
    _img(store, "wx", "local/a.png", shared)
    _img(store, "wx", "local/b.png", shared)
    _img(store, "nas", "nas/photo.png", shared)

    assert store.delete_file("wx", "local/a.png")["removed"] == 1

    rows, total = store.list_files(duplicates="duplicates")
    assert total == 2
    assert {r["relpath"] for r in rows} == {"local/b.png", "nas/photo.png"}
    groups, group_total = store.duplicate_groups()
    assert group_total == 1 and groups[0]["count"] == 2
    stats = store.files_stats()
    assert stats["total"] == 2
    assert stats["duplicate_groups"] == 1
    assert stats["duplicate_files"] == 2
