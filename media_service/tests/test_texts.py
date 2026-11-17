"""独立文本域：TXT/MD/HTML 全文检索，内容摘要驱动重建，目录重扫清理幽灵条目。"""
import hashlib

from app import config
from app.store import Store


def _store(tmp_path, monkeypatch, roots):
    monkeypatch.setattr(config, "MEDIA_ROOTS", tuple(roots))
    return Store(tmp_path / "index.sqlite")


def _local(name, path):
    return {"id": name, "type": "local", "path": str(path), "writable": True}


def _item(root_id, base, relpath, body, title=None, kind=None):
    path = base / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    data = body.encode("utf-8")
    path.write_bytes(data)
    suffix = path.suffix.lower().lstrip(".")
    return {
        "root": root_id,
        "relpath": relpath,
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "kind": kind or suffix,
        "title": title or path.stem,
        "body": body,
    }


def test_sync_search_and_detail(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    items = [
        _item("wx", root, "notes/shufa.md", "# 书法笔记\n\n翰墨修心，笃行致远。"),
        _item("wx", root, "notes/haibian.txt", "海边日落，光影变化记录。", title="海边日落"),
    ]
    store = _store(tmp_path, monkeypatch, [_local("wx", root)])
    report = store.sync_standalone_texts(items)

    assert report == {"new": 2, "updated": 0, "unchanged": 0, "removed": 0}

    rows, total, tokens = store.search_texts("翰墨修心")
    assert total == 1
    assert tokens == ["翰", "墨", "修", "心", "翰墨", "墨修", "修心"]
    assert rows[0]["relpath"] == "notes/shufa.md"

    detail = store.text_detail(rows[0]["id"])
    assert detail["body"].startswith("# 书法笔记")
    assert detail["kind"] == "md"
    assert store.text_detail(99999) is None


def test_search_filters_by_root_and_kind(tmp_path, monkeypatch):
    local, nas = tmp_path / "local", tmp_path / "nas"
    items = [
        _item("local", local, "a.txt", "共同关键词出现在本地文本。"),
        _item("nas", nas, "b.md", "共同关键词出现在 NAS 文本。"),
        _item("nas", nas, "c.html", "<p>共同关键词出现在 HTML。</p>", title="页面"),
    ]
    store = _store(tmp_path, monkeypatch, [_local("local", local), _local("nas", nas)])
    store.sync_standalone_texts(items)

    rows, total, _ = store.search_texts("共同关键词")
    assert total == 3
    assert sorted(row["root"] for row in rows) == ["local", "nas", "nas"]

    rows, total, _ = store.search_texts("共同关键词", root="nas")
    assert total == 2
    assert {row["root"] for row in rows} == {"nas"}

    rows, total, _ = store.search_texts("共同关键词", kind="html")
    assert total == 1
    assert rows[0]["relpath"] == "c.html"


def test_unchanged_content_skips_reindex_and_change_reindexes(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    item = _item("wx", root, "note.txt", "初版内容：包含唯一标记 alpha。")
    store = _store(tmp_path, monkeypatch, [_local("wx", root)])
    store.sync_standalone_texts([item])

    # 同摘要重扫：不重建索引，也不产生幽灵。
    report = store.sync_standalone_texts([item])
    assert report == {"new": 0, "updated": 0, "unchanged": 1, "removed": 0}
    assert store.texts_facets()["total"] == 1

    # 内容变化：新摘要触发重建，旧词不再命中，新词可命中。
    item = _item("wx", root, "note.txt", "修订版内容：换成唯一标记 beta。")
    report = store.sync_standalone_texts([item])
    assert report["updated"] == 1
    assert store.texts_facets()["total"] == 1

    assert store.search_texts("alpha")[1] == 0
    rows, total, _ = store.search_texts("beta")
    assert total == 1
    assert rows[0]["content_sha256"] == item["content_sha256"]


def test_prune_removes_ghost(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    keep = _item("wx", root, "keep.txt", "保留的文本。")
    drop = _item("wx", root, "drop.txt", "将被删除的文本。")
    store = _store(tmp_path, monkeypatch, [_local("wx", root)])
    store.sync_standalone_texts([keep, drop])
    assert store.texts_facets()["total"] == 2

    report = store.sync_standalone_texts([keep], prune_root="wx")
    assert report["removed"] == 1
    assert store.texts_facets()["total"] == 1
    assert store.search_texts("删除")[1] == 0
    assert store.search_texts("保留")[1] == 1


def test_fallback_any_token_and_empty_query(tmp_path, monkeypatch):
    """长查询先要求全部二元词命中，无结果才退回任意词命中。"""
    root = tmp_path / "wx"
    items = [
        _item("wx", root, "a.txt", "手机屏幕碎了怎么办"),
        _item("wx", root, "b.txt", "手机壁纸推荐"),
    ]
    store = _store(tmp_path, monkeypatch, [_local("wx", root)])
    store.sync_standalone_texts(items)

    assert store.search_texts("手机屏幕")[1] == 1
    # 二元词 AND 无结果，退回 OR 后两条都命中。
    assert store.search_texts("手机碎屏")[1] == 2
    # 空查询退回按索引时间倒序列出。
    rows, total, tokens = store.search_texts("")
    assert tokens == []
    assert total == 2
    assert len(rows) == 2


def test_html_and_markdown_tokenized(tmp_path, monkeypatch):
    """HTML 标签与 Markdown 符号不参与分词后，正文词仍可命中。"""
    root = tmp_path / "wx"
    items = [
        _item("wx", root, "page.html", "<html><body><p>深海里的鲸歌</p></body></html>", title="鲸歌"),
        _item("wx", root, "doc.md", "## 标题\n\n鲸歌与潮汐。", title="潮汐"),
    ]
    store = _store(tmp_path, monkeypatch, [_local("wx", root)])
    store.sync_standalone_texts(items)

    rows, total, _ = store.search_texts("鲸歌")
    assert total == 2
    rows, total, _ = store.search_texts("潮汐")
    assert total == 1
    assert rows[0]["relpath"] == "doc.md"
    # 标题也进索引。
    assert store.search_texts("标题")[1] == 1


def test_texts_facets(tmp_path, monkeypatch):
    local, nas = tmp_path / "local", tmp_path / "nas"
    store = _store(tmp_path, monkeypatch, [_local("local", local), _local("nas", nas)])
    store.sync_standalone_texts([
        _item("local", local, "a.txt", "文本一。"),
        _item("nas", nas, "b.md", "文本二。"),
        _item("nas", nas, "c.html", "<p>文本三。</p>", title="三"),
    ])

    facets = store.texts_facets()
    assert facets["total"] == 3
    assert {row["kind"] for row in facets["kinds"]} == {"txt", "md", "html"}
    assert {row["root"]: row["count"] for row in facets["roots"]} == {"local": 1, "nas": 2}
