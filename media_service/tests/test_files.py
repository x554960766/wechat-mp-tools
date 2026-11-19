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


def _flag(store, root, relpath, data, *, score, label="nsfw", stage="first", reviewed_by=None):
    """给一张已入库图片写一条 NSFW 判定（直接落库，不经扫描/模型）。"""
    store.set_nsfw(root, relpath, _sha(data), score=score, label=label,
                   stage=stage, reviewed_by=reviewed_by)


def test_list_files_attaches_effective_nsfw_and_filters(tmp_path, monkeypatch):
    """图片行附带当前内容的有效 NSFW 结论；过滤只看疑似敏感，且非图片恒为 None。"""
    root = tmp_path / "wx"
    root.mkdir()
    store = _store(tmp_path, monkeypatch, [{"id": "wx", "type": "local", "path": str(root), "writable": True}])
    risky = b"risky-bytes"
    clean = b"clean-bytes"
    _img(store, "wx", "risky/a.png", risky, size=50)
    _img(store, "wx", "clean/b.png", clean, size=40)
    store.sync_standalone_texts([
        {"root": "wx", "relpath": "risky/note.txt", "content_sha256": _sha(b"t"),
         "kind": "txt", "title": "t", "body": "t"},
    ])
    _flag(store, "wx", "risky/a.png", risky, score=0.91)
    _flag(store, "wx", "clean/b.png", clean, score=0.12, label="safe")

    rows, _ = store.list_files()
    by_path = {r["relpath"]: r for r in rows}
    assert by_path["risky/a.png"]["nsfw"]["label"] == "nsfw"
    assert by_path["risky/a.png"]["nsfw"]["score"] == 0.91
    assert by_path["clean/b.png"]["nsfw"]["label"] == "safe"
    # 文本不是图片，即使同名内容也不会挂 NSFW 结论
    assert by_path["risky/note.txt"]["nsfw"] is None

    flagged, total_flagged = store.list_files(nsfw="flagged")
    assert total_flagged == 1
    assert [r["relpath"] for r in flagged] == ["risky/a.png"]

    unreviewed, total_unreviewed = store.list_files(nsfw="unreviewed")
    assert total_unreviewed == 1
    assert [r["relpath"] for r in unreviewed] == ["risky/a.png"]

    # 人工复核为误报后，该文件从疑似列表消失
    _flag(store, "wx", "risky/a.png", risky, score=0.91, stage="second", label="safe", reviewed_by="本机")
    assert store.list_files(nsfw="flagged")[1] == 0


def test_files_stats_counts_nsfw_flagged_and_unreviewed(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    store = _store(tmp_path, monkeypatch, [{"id": "wx", "type": "local", "path": str(root), "writable": True}])
    a = b"a-bytes"
    b = b"b-bytes"
    _img(store, "wx", "a/one.png", a)
    _img(store, "wx", "a/two.png", b)
    _flag(store, "wx", "a/one.png", a, score=0.9, stage="first")
    _flag(store, "wx", "a/two.png", b, score=0.8, stage="second", label="nsfw", reviewed_by="本机")

    stats = store.files_stats()
    assert stats["nsfw_flagged"] == 2
    assert stats["nsfw_unreviewed"] == 1

