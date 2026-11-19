"""一次性 NSFW 检测（任意目录 / 上传 / 入库）的引擎与接口契约。

不变量：
1. 预检 = 扫描的枚举口径：不跟随符号链接、按扩展名分类、到上限如实标 truncated；
2. probe 结果只落任务目录，不写 nsfw_results / 不动索引统计；
3. 视频结论取最严重帧，并保留逐帧分数与命中帧缩略图；
4. 入库必须先校验源文件没变，复制后只入队一次增量 scan，不自己写索引；
5. 接口把参数错误映射成 422/403/409，不用 500 糊过去。
"""
from __future__ import annotations

import hashlib
import os
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, frames, nsfw_probe, review
from app.jobs import JOBS
from app.main import app


def _png(color="red"):
    out = BytesIO()
    Image.new("RGB", (16, 16), color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def _probe_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(nsfw_probe, "PROBE_DIR", tmp_path / "probe")
    nsfw_probe._items_cache.clear()
    yield


def _job(job_id="job1", **values):
    base = {"id": job_id, "source": "path", "path": "", "source_path": None,
            "recursive": True, "kinds": ["image", "video"], "video_interval": 5.0,
            "max_files": 100, "status": "running", "cancel_requested": False}
    base.update(values)
    return base


def _client():
    return TestClient(app, base_url="http://127.0.0.1:8765")


# ── 引擎 ──────────────────────────────────────────────────

def test_precheck_counts_types_and_skips_symlinks(tmp_path):
    base = tmp_path / "dir"
    (base / "sub").mkdir(parents=True)
    (base / "a.png").write_bytes(_png())
    (base / "b.jpg").write_bytes(_png())
    (base / "note.txt").write_text("x")
    (base / "sub" / "clip.mp4").write_bytes(b"not-really-video")
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png())
    os.symlink(outside, base / "link.png")
    os.symlink(outside.parent, base / "linkdir")

    flat = nsfw_probe.precheck(str(base), recursive=False)
    assert (flat["images"], flat["videos"], flat["total"]) == (2, 0, 2)
    assert flat["truncated"] is False

    deep = nsfw_probe.precheck(str(base), recursive=True)
    assert (deep["images"], deep["videos"], deep["total"]) == (2, 1, 3)
    assert "link.png" not in deep["sample"] and "sub/clip.mp4" in deep["sample"]

    capped = nsfw_probe.precheck(str(base), recursive=True, max_files=2)
    assert capped["total"] == 2 and capped["truncated"] is True


def test_precheck_reports_missing_and_relative(tmp_path):
    assert nsfw_probe.precheck(str(tmp_path / "nope"))["error"] == "路径不存在"
    with pytest.raises(ValueError):
        nsfw_probe.precheck("relative/dir")


def test_scan_images_records_result_and_thumb(tmp_path, monkeypatch):
    base = tmp_path / "dir"
    base.mkdir()
    (base / "a.png").write_bytes(_png())
    monkeypatch.setattr(review, "score_image",
                        lambda data, provider=None: {"score": 0.93, "label": "nsfw", "status": "ok"})
    job = _job(path=str(base))
    counts = nsfw_probe.scan(job)

    assert counts["images"] == 1 and counts["scanned"] == 1 and counts["flagged"] == 1
    assert counts["status"] == "completed" and counts["complete"] is True
    rows = nsfw_probe.load_results("job1")
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "nsfw" and row["score"] == pytest.approx(0.93)
    assert row["digest"] == hashlib.sha256((base / "a.png").read_bytes()).hexdigest()
    assert nsfw_probe.thumb_path("job1", 0).is_file()


def test_scan_video_takes_worst_frame_and_keeps_timeline(tmp_path, monkeypatch):
    base = tmp_path / "dir"
    base.mkdir()
    video = base / "clip.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(frames, "available", lambda: True)
    monkeypatch.setattr(frames, "probe_duration", lambda path: 30.0)
    monkeypatch.setattr(frames, "extract_frames",
                        lambda path, duration, interval, limit: [(0.0, _png("green")),
                                                                 (10.0, _png("red"))])
    scores = iter([0.12, 0.88])
    monkeypatch.setattr(review, "score_image",
                        lambda data, provider=None: {"score": next(scores), "label": "nsfw",
                                                     "status": "ok"})
    counts = nsfw_probe.scan(_job(path=str(base)))
    assert counts["videos"] == 1 and counts["flagged"] == 1

    row = nsfw_probe.load_results("job1")[0]
    assert row["score"] == pytest.approx(0.88)
    assert row["video"]["frames"] == 2
    assert [row["ts"] if "ts" in row else item["ts"] for item in row["video"]["frame_scores"]] == [0.0, 10.0]
    assert [hit["ts"] for hit in row["video"]["hits"]] == [10.0, 0.0]
    assert nsfw_probe.thumb_path("job1", 0, 0).is_file()


def test_scan_marks_error_items_without_aborting_batch(tmp_path, monkeypatch):
    base = tmp_path / "dir"
    base.mkdir()
    (base / "bad.png").write_bytes(b"not an image")
    (base / "ok.png").write_bytes(_png())
    monkeypatch.setattr(review, "score_image",
                        lambda data, provider=None: {"score": 0.1, "label": "normal", "status": "ok"})
    counts = nsfw_probe.scan(_job(path=str(base)))
    assert counts["images"] == 2 and counts["failed"] == 1 and counts["scanned"] == 1
    assert counts["status"] == "partial"
    assert counts["errors"]


# ── 入库 ──────────────────────────────────────────────────

def _media_root(tmp_path, monkeypatch, writable=True):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", (
        {"id": "r", "type": "local", "path": str(root), "writable": writable},
    ))
    return root


def test_import_copies_and_enqueues_one_scan(tmp_path, monkeypatch):
    root = _media_root(tmp_path, monkeypatch, writable=True)
    src = tmp_path / "dir" / "a b?.png"
    src.parent.mkdir()
    src.write_bytes(_png())
    nsfw_probe._write_results("job1", [{"index": 0, "path": str(src), "name": src.name,
                                        "kind": "image", "size": src.stat().st_size,
                                        "mtime": src.stat().st_mtime, "status": "ok",
                                        "score": 0.9, "label": "nsfw", "thumb": None}])
    calls = []
    monkeypatch.setattr(JOBS, "enqueue_scan", lambda root_id, rebuild=False: calls.append(root_id) or {"id": "scan1"})

    data = nsfw_probe.import_item(_job(), 0, "r", "NSFW导入/2026-11-19")
    assert data == {"root": "r", "relpath": "NSFW导入/2026-11-19/a b_.png", "scan_job_id": "scan1"}
    assert (root / data["relpath"]).read_bytes() == src.read_bytes()
    assert calls == ["r"]
    assert nsfw_probe.load_results("job1")[0]["imported"]["relpath"] == data["relpath"]

    second = nsfw_probe.import_item(_job(), 0, "r", "NSFW导入/2026-11-19")
    assert second["relpath"].endswith("a b_ (2).png")


def test_import_rejects_changed_or_missing_source(tmp_path, monkeypatch):
    _media_root(tmp_path, monkeypatch)
    src = tmp_path / "a.png"
    src.write_bytes(_png())
    nsfw_probe._write_results("job1", [{"index": 0, "path": str(src), "name": "a.png",
                                        "kind": "image", "size": src.stat().st_size,
                                        "mtime": src.stat().st_mtime, "status": "ok",
                                        "digest": "deadbeef"}])
    with pytest.raises(ValueError, match="内容已变化"):
        nsfw_probe.import_item(_job(), 0, "r", "")
    src.unlink()
    with pytest.raises(ValueError, match="已不存在"):
        nsfw_probe.import_item(_job(), 0, "r", "")


def test_import_refuses_readonly_root(tmp_path, monkeypatch):
    _media_root(tmp_path, monkeypatch, writable=False)
    src = tmp_path / "a.png"
    src.write_bytes(_png())
    nsfw_probe._write_results("job1", [{"index": 0, "path": str(src), "name": "a.png",
                                        "kind": "image", "size": src.stat().st_size,
                                        "mtime": src.stat().st_mtime, "status": "ok"}])
    with pytest.raises(PermissionError):
        nsfw_probe.import_item(_job(), 0, "r", "")


def test_safe_dir_rejects_absolute_and_dotdot():
    assert nsfw_probe.safe_dir("a/b") == "a/b"
    assert nsfw_probe.safe_dir("") == ""
    for bad in ("/tmp/x", "../x", "a/../b", "a/.git", "C:\\tmp"):
        with pytest.raises(ValueError):
            nsfw_probe.safe_dir(bad)


def test_cleanup_only_removes_expired_job_dirs(tmp_path, monkeypatch):
    old = nsfw_probe.PROBE_DIR / "old"
    new = nsfw_probe.PROBE_DIR / "new"
    for path in (old, new):
        (path / "thumbs").mkdir(parents=True)
    stale = 1_600_000_000
    os.utime(old, (stale, stale))
    assert nsfw_probe.cleanup(max_age_seconds=3600) == 1
    assert not old.exists() and new.exists()


# ── 接口 ──────────────────────────────────────────────────

def test_check_endpoint_returns_counts(tmp_path):
    (tmp_path / "a.png").write_bytes(_png())
    body = _client().post("/api/nsfw/probes/check", json={"path": str(tmp_path)}).json()
    assert body["total"] == 1 and body["images"] == 1


def test_create_endpoint_requires_model(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "available", lambda: False)
    resp = _client().post("/api/nsfw/probes", json={"path": str(tmp_path)})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "nsfw_model_missing"


def test_create_endpoint_rejects_relative_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "available", lambda: True)
    client = _client()
    assert client.post("/api/nsfw/probes", json={"path": "rel"}).status_code == 422
    assert client.post("/api/nsfw/probes", json={"path": str(tmp_path / "nope")}).status_code == 422


def test_create_endpoint_creates_job(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "available", lambda: True)
    (tmp_path / "a.png").write_bytes(_png())
    captured = {}

    def fake_enqueue(source, **values):
        captured.update(source=source, **values)
        return {"id": "p1", "status": "queued"}

    monkeypatch.setattr(JOBS, "enqueue_probe", fake_enqueue)
    body = _client().post("/api/nsfw/probes", json={"path": str(tmp_path),
                                                    "kinds": ["image"], "video_interval": 3}).json()
    assert body == {"id": "p1", "status": "queued", "source": "path"}
    assert captured["kinds"] == ["image"] and captured["video_interval"] == 3


def test_upload_endpoint_streams_to_staging_and_creates_job(monkeypatch):
    monkeypatch.setattr(review, "available", lambda: True)
    captured = {}

    def fake_enqueue(source, **values):
        captured.update(source=source, **values)
        assert os.path.isfile(values["source_path"])
        return {"id": "p2", "status": "queued"}

    monkeypatch.setattr(JOBS, "enqueue_probe", fake_enqueue)
    resp = _client().post("/api/nsfw/probes/upload",
                          files={"file": ("a.png", _png(), "image/png")})
    assert resp.status_code == 202
    assert captured["source"] == "upload" and captured["max_files"] == 1

    bad = _client().post("/api/nsfw/probes/upload",
                         files={"file": ("a.txt", b"x", "text/plain")})
    assert bad.status_code == 422


def test_upload_endpoint_enforces_size_limit(monkeypatch):
    monkeypatch.setattr(review, "available", lambda: True)
    monkeypatch.setattr(config, "PROBE_UPLOAD_BYTES", 8)
    resp = _client().post("/api/nsfw/probes/upload",
                          files={"file": ("a.png", b"x" * 32, "image/png")})
    assert resp.status_code == 413


def test_items_endpoint_rewrites_thumb_urls(monkeypatch):
    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "nsfw_probe",
                                                         "status": "completed"})
    monkeypatch.setattr(nsfw_probe, "items", lambda job_id, offset, limit, flagged_only=False: {
        "items": [{"index": 0, "thumb": "thumbs/0.jpg", "kind": "video",
                   "video": {"hits": [{"ts": 1, "thumb": "thumbs/0_0.jpg"},
                                      {"ts": 2, "thumb": None}]}}],
        "total": 1, "offset": 0, "limit": 50})
    body = _client().get("/api/nsfw/probes/j1/items").json()
    item = body["items"][0]
    assert item["thumb"] == "/api/nsfw/probes/j1/thumb/0"
    assert item["video"]["hits"][0]["thumb"] == "/api/nsfw/probes/j1/thumb/0?hit=0"
    assert item["video"]["hits"][1]["thumb"] is None


def test_thumb_endpoint_is_job_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "nsfw_probe"})
    path = nsfw_probe.PROBE_DIR / "j1" / "thumbs" / "0.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xd8\xff")
    nsfw_probe._write_results("j1", [{"index": 0, "thumb": "thumbs/0.jpg"}])
    assert _client().get("/api/nsfw/probes/j1/thumb/0").status_code == 200
    assert _client().get("/api/nsfw/probes/j1/thumb/9").status_code == 404
    # 盘上存在但没登记进结果的缩略图不对外服务：端点只认 results.json 里的名字
    (path.parent / "1.jpg").write_bytes(b"\xff\xd8\xff")
    assert _client().get("/api/nsfw/probes/j1/thumb/1").status_code == 404


def test_items_endpoint_does_not_poison_thumb_names(tmp_path, monkeypatch):
    """列表请求只许改副本：写回缓存会让命中帧缩略图从此固定 404。"""
    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "nsfw_probe"})
    thumbs = nsfw_probe.PROBE_DIR / "j1" / "thumbs"
    thumbs.mkdir(parents=True)
    for name in ("0.jpg", "0_0.jpg"):
        (thumbs / name).write_bytes(b"\xff\xd8\xff")
    nsfw_probe._write_results("j1", [{"index": 0, "thumb": "thumbs/0.jpg", "kind": "video",
                                      "video": {"hits": [{"ts": 3, "thumb": "thumbs/0_0.jpg"}]}}])
    client = _client()
    hit_url = client.get("/api/nsfw/probes/j1/items").json()["items"][0]["video"]["hits"][0]["thumb"]
    assert hit_url == "/api/nsfw/probes/j1/thumb/0?hit=0"
    # 先取列表、再取命中帧：缓存里的文件名必须还是文件名
    assert client.get("/api/nsfw/probes/j1/thumb/0?hit=0").status_code == 200
    assert nsfw_probe.load_results("j1")[0]["video"]["hits"][0]["thumb"] == "thumbs/0_0.jpg"


def test_import_endpoint_maps_errors(monkeypatch):
    client = _client()
    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "nsfw_probe",
                                                         "status": "running"})
    assert client.post("/api/nsfw/probes/j1/import",
                       json={"index": 0, "root": "r"}).status_code == 409

    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "nsfw_probe",
                                                         "status": "completed"})
    monkeypatch.setattr(config, "MEDIA_ROOTS", (
        {"id": "r", "type": "local", "path": "/tmp", "writable": False},))
    monkeypatch.setattr(nsfw_probe, "import_item",
                        lambda job, index, root, target: (_ for _ in ()).throw(PermissionError("只读")))
    assert client.post("/api/nsfw/probes/j1/import",
                       json={"index": 0, "root": "r"}).status_code == 403

    monkeypatch.setattr(nsfw_probe, "import_item",
                        lambda job, index, root, target: {"root": root, "relpath": "x/a.png",
                                                          "scan_job_id": "s1"})
    ok = client.post("/api/nsfw/probes/j1/import", json={"index": 0, "root": "r"}).json()
    assert ok["scan_job_id"] == "s1"


# ── 已入库副本：查看与撤销入库 ─────────────────────────────

from app.store import Store  # noqa: E402  （只在下面几例里用到）


def _completed_job(monkeypatch):
    monkeypatch.setattr(JOBS, "get_job",
                        lambda job_id: {"id": job_id, "kind": "nsfw_probe", "status": "completed"})


def _imported_row(root_id, relpath, kind="image", index=0, imported=True):
    row = {"index": index, "path": "/nowhere/a.png", "name": "a.png", "kind": kind,
           "size": 1, "mtime": 0.0, "status": "ok", "score": 0.1, "label": "safe",
           "thumb": None}
    if imported:
        row["imported"] = {"root": root_id, "relpath": relpath}
    return row


def test_file_endpoint_serves_imported_copy(tmp_path, monkeypatch):
    root = _media_root(tmp_path, monkeypatch)
    (root / "NSFW").mkdir()
    payload = _png("blue")
    (root / "NSFW" / "a.png").write_bytes(payload)
    _completed_job(monkeypatch)
    nsfw_probe._write_results("j1", [_imported_row("r", "NSFW/a.png")])

    resp = _client().get("/api/nsfw/probes/j1/file/0")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == payload


def test_file_endpoint_serves_video_and_supports_range(tmp_path, monkeypatch):
    root = _media_root(tmp_path, monkeypatch)
    (root / "NSFW").mkdir()
    (root / "NSFW" / "v.mp4").write_bytes(b"0123456789")
    _completed_job(monkeypatch)
    nsfw_probe._write_results("j1", [_imported_row("r", "NSFW/v.mp4", kind="video")])

    resp = _client().get("/api/nsfw/probes/j1/file/0")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    part = _client().get("/api/nsfw/probes/j1/file/0", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206
    assert part.content == b"2345"


def test_file_endpoint_rejects_unimported_and_missing_copy(tmp_path, monkeypatch):
    root = _media_root(tmp_path, monkeypatch)
    (root / "NSFW").mkdir()
    _completed_job(monkeypatch)
    client = _client()
    nsfw_probe._write_results("j1", [_imported_row("r", "NSFW/a.png", imported=False)])
    assert client.get("/api/nsfw/probes/j1/file/0").status_code == 409
    assert client.get("/api/nsfw/probes/j1/file/7").status_code == 404
    nsfw_probe._write_results("j1", [_imported_row("r", "NSFW/gone.png")])
    assert client.get("/api/nsfw/probes/j1/file/0").status_code == 404


def test_unimport_deletes_copy_and_clears_marker(tmp_path, monkeypatch):
    root = _media_root(tmp_path, monkeypatch)
    (root / "NSFW").mkdir()
    (root / "NSFW" / "a.png").write_bytes(_png())
    monkeypatch.setattr("app.files_routes.STORE", Store(tmp_path / "index.sqlite"))
    _completed_job(monkeypatch)
    nsfw_probe._write_results("j1", [_imported_row("r", "NSFW/a.png")])
    client = _client()

    resp = client.post("/api/nsfw/probes/j1/unimport", json={"index": 0})
    assert resp.status_code == 200
    assert resp.json()["removed"] is True
    assert not (root / "NSFW" / "a.png").exists()
    assert "imported" not in nsfw_probe.load_results("j1")[0]
    # 再撤一次：标记已清，报「还没有入库」而不是当成成功。
    assert client.post("/api/nsfw/probes/j1/unimport", json={"index": 0}).status_code == 409


def test_unimport_refuses_readonly_root_and_keeps_marker(tmp_path, monkeypatch):
    root = _media_root(tmp_path, monkeypatch, writable=False)
    (root / "NSFW").mkdir()
    (root / "NSFW" / "a.png").write_bytes(_png())
    monkeypatch.setattr("app.files_routes.STORE", Store(tmp_path / "index.sqlite"))
    _completed_job(monkeypatch)
    nsfw_probe._write_results("j1", [_imported_row("r", "NSFW/a.png")])

    resp = _client().post("/api/nsfw/probes/j1/unimport", json={"index": 0})
    assert resp.status_code == 403
    assert (root / "NSFW" / "a.png").exists()
    assert nsfw_probe.load_results("j1")[0]["imported"]["relpath"] == "NSFW/a.png"


def test_unimport_unknown_index_is_404(tmp_path, monkeypatch):
    _media_root(tmp_path, monkeypatch)
    _completed_job(monkeypatch)
    nsfw_probe._write_results("j1", [_imported_row("r", "NSFW/a.png")])
    assert _client().post("/api/nsfw/probes/j1/unimport", json={"index": 3}).status_code == 404
