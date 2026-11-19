"""全局文件检索与查重的 HTTP 端点契约。

存储层语义（三表联合、按摘要归组、统计）已由 ``test_files.py`` 覆盖，这里只断言
「HTTP 层把参数与结果原样透出、错误明确」：

1. ``/api/files/list`` 支持 q/root/ext/kind/duplicates 筛选，返回 rows + total；
2. ``/api/files/duplicates`` 返回按摘要归组的分组，成员截断预览；
3. ``/api/files/group/{sha}`` 返回该摘要下未截断的完整成员；
4. ``/api/files/stats`` 返回总量、类型/根分布、重复组与涉及文件数、最大文件；
5. 非法 duplicates / kind 明确报 422，未知摘要明确报 404，不静默吞掉。
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config
from app.files_routes import STORE as _ROUTE_STORE
from app.main import app
from app.store import Store


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", (
        {"id": "wx", "type": "local", "path": str(root), "writable": True},
    ))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr("app.files_routes.STORE", store)
    png = b"\x89PNG-shared-bytes"
    vec = np.zeros(512, dtype=np.float32)
    vec[0] = 1.0
    store.add_image("wx", "a/one.png", png, embedding=vec, size=10)
    store.add_image("wx", "a/two.png", png, embedding=vec, size=20)
    store.sync_standalone_texts([
        {"root": "wx", "relpath": "a/one.txt", "content_sha256": _sha(b"note"),
         "kind": "txt", "title": "one", "body": "note"},
    ])
    store.replace_frames("wx", "a/clip.mp4", _sha(b"clip"), 3.5, [])
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    return client, store


def test_list_endpoint_unions_and_filters(tmp_path, monkeypatch):
    client, store = _setup(tmp_path, monkeypatch)
    body = client.get("/api/files/list").json()
    assert body["total"] == 4
    assert {r["kind"] for r in body["items"]} == {"image", "text", "video"}

    by_kind = client.get("/api/files/list", params={"kind": "image"}).json()
    assert by_kind["total"] == 2
    assert all(r["kind"] == "image" for r in by_kind["items"])

    by_ext = client.get("/api/files/list", params={"ext": "txt"}).json()
    assert [r["relpath"] for r in by_ext["items"]] == ["a/one.txt"]

    by_q = client.get("/api/files/list", params={"q": "clip"}).json()
    assert [r["relpath"] for r in by_q["items"]] == ["a/clip.mp4"]

    dups = client.get("/api/files/list", params={"duplicates": "duplicates"}).json()
    assert dups["total"] == 2
    assert all(r["dup_count"] == 2 for r in dups["items"])

    unique = client.get("/api/files/list", params={"duplicates": "unique"}).json()
    assert unique["total"] == 2
    assert all(r["dup_count"] == 1 for r in unique["items"])


def test_duplicates_endpoint_groups_by_sha(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch)
    body = client.get("/api/files/duplicates").json()
    assert body["total"] == 1
    group = body["items"][0]
    assert group["count"] == 2
    assert {m["relpath"] for m in group["members"]} == {"a/one.png", "a/two.png"}


def test_duplicate_group_endpoint_returns_all_members(tmp_path, monkeypatch):
    client, store = _setup(tmp_path, monkeypatch)
    listed = client.get("/api/files/duplicates").json()["items"][0]
    sha = listed["sha"]
    body = client.get(f"/api/files/group/{sha}").json()
    assert body["count"] == 2
    assert {m["relpath"] for m in body["members"]} == {"a/one.png", "a/two.png"}

    missing = client.get("/api/files/group/deadbeef")
    assert missing.status_code == 404


def test_stats_endpoint_counts(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch)
    body = client.get("/api/files/stats").json()
    assert body["total"] == 4
    assert {k["kind"] for k in body["kinds"]} == {"image", "text", "video"}
    assert body["duplicate_groups"] == 1
    assert body["duplicate_files"] == 2
    assert body["biggest"]["relpath"] == "a/two.png"


def test_invalid_params_are_explicit(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch)
    assert client.get("/api/files/list", params={"duplicates": "bogus"}).status_code == 422
    assert client.get("/api/files/list", params={"kind": "bogus"}).status_code == 422
    assert client.get("/api/files/list", params={"nsfw": "bogus"}).status_code == 422
    assert client.get("/api/files/list", params={"limit": 0}).status_code == 422
    assert client.get("/api/files/list", params={"offset": -1}).status_code == 422


def test_list_endpoint_attaches_and_filters_nsfw(tmp_path, monkeypatch):
    """图片行携带有效 NSFW 结论，非图片恒为 None；flagged/unreviewed 过滤生效。"""
    client, store = _setup(tmp_path, monkeypatch)
    risky = b"risky-bytes"
    clean = b"clean-bytes"
    vec = np.zeros(512, dtype=np.float32)
    vec[0] = 1.0
    store.add_image("wx", "a/risky.png", risky, embedding=vec, size=30)
    store.add_image("wx", "a/clean.png", clean, embedding=vec, size=20)
    sha = hashlib.sha256(risky).hexdigest()
    store.set_nsfw("wx", "a/risky.png", sha, score=0.91, label="nsfw", stage="first")
    store.set_nsfw("wx", "a/clean.png", hashlib.sha256(clean).hexdigest(),
                   score=0.1, label="safe", stage="first")

    all_rows = client.get("/api/files/list").json()["items"]
    by_path = {r["relpath"]: r for r in all_rows}
    assert by_path["a/risky.png"]["nsfw"]["label"] == "nsfw"
    assert by_path["a/clean.png"]["nsfw"]["label"] == "safe"
    assert by_path["a/one.txt"]["nsfw"] is None

    flagged = client.get("/api/files/list", params={"nsfw": "flagged"}).json()
    assert flagged["total"] == 1
    assert flagged["items"][0]["relpath"] == "a/risky.png"

    stats = client.get("/api/files/stats").json()
    assert stats["nsfw_flagged"] == 1
    assert stats["nsfw_unreviewed"] == 1


def _setup_deletable(tmp_path, monkeypatch, *, writable=True):
    """带真实磁盘文件的删除用例脚手架：索引与物理文件同时存在。"""
    root = tmp_path / "wx"
    (root / "a").mkdir(parents=True)
    (root / "a" / "one.png").write_bytes(b"\x89PNG-shared-bytes")
    (root / "a" / "two.png").write_bytes(b"\x89PNG-other-bytes")
    monkeypatch.setattr(config, "MEDIA_ROOTS", (
        {"id": "wx", "type": "local", "path": str(root), "writable": writable},
    ))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr("app.files_routes.STORE", store)
    vec = np.zeros(512, dtype=np.float32)
    vec[0] = 1.0
    store.add_image("wx", "a/one.png", b"\x89PNG-shared-bytes", embedding=vec, size=10)
    store.add_image("wx", "a/two.png", b"\x89PNG-other-bytes", embedding=vec, size=20)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    return client, store, root


def test_delete_endpoint_removes_physical_file_and_index(tmp_path, monkeypatch):
    client, store, root = _setup_deletable(tmp_path, monkeypatch)

    resp = client.post("/api/files/delete", json={"root": "wx", "relpath": "a/one.png"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["physical"]["existed"] is True
    assert body["index"]["image"] is True
    assert body["removed"] is True

    assert not (root / "a" / "one.png").exists()
    assert store.list_files(q="one.png")[1] == 0
    # 同组其他文件不受影响
    assert (root / "a" / "two.png").exists()
    assert store.list_files(q="two.png")[1] == 1


def test_delete_endpoint_rejects_unknown_and_readonly_root(tmp_path, monkeypatch):
    client, store, root = _setup_deletable(tmp_path, monkeypatch)

    unknown = client.post("/api/files/delete", json={"root": "nope", "relpath": "a/one.png"})
    assert unknown.status_code == 404

    readonly_client, _, _ = _setup_deletable(tmp_path / "ro", monkeypatch, writable=False)
    readonly = readonly_client.post("/api/files/delete", json={"root": "wx", "relpath": "a/one.png"})
    assert readonly.status_code == 403
    assert (tmp_path / "ro" / "wx" / "a" / "one.png").exists()


def test_delete_endpoint_validates_params(tmp_path, monkeypatch):
    client, _, _ = _setup_deletable(tmp_path, monkeypatch)
    assert client.post("/api/files/delete", json={"root": "wx"}).status_code == 422
    assert client.post("/api/files/delete", json={"relpath": "a/one.png"}).status_code == 422
    assert client.post("/api/files/delete", json={}).status_code == 422


def test_delete_endpoint_keeps_index_when_physical_delete_fails(tmp_path, monkeypatch):
    """物理删除失败时索引必须保持不动，用户可修复后重试。"""
    client, store, root = _setup_deletable(tmp_path, monkeypatch)

    def _boom(root_id, relpath):
        raise OSError("disk busy")
    monkeypatch.setattr("app.files_routes.media.delete_entry", _boom)

    resp = client.post("/api/files/delete", json={"root": "wx", "relpath": "a/one.png"})
    assert resp.status_code == 500
    assert "索引未变更" in resp.json()["error"]["message"]
    assert store.list_files(q="one.png")[1] == 1
