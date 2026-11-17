"""NAS 自动归档队列（backend/nas_archive.py）的路由级单测。

覆盖三条不变量：幂等（同作品不重复提交）、不删本地（cleanup=false）、
以及失败重试与人工重试的状态流转。
"""
from __future__ import annotations

import hashlib
import json
import threading

import pytest

from backend import nas_archive


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeRequests:
    """记录每个请求，按 URL 尾段返回预置响应。"""

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []
        self.job_status = "completed"
        self.existing_jobs: list[dict] = []
        self.post_status = 202
        self.lock = threading.Lock()

    def get(self, url, params=None, timeout=None):
        with self.lock:
            self.gets.append((url, params or {}))
        if url.endswith("/api/work-archive/jobs"):
            return _FakeResponse(200, {"items": self.existing_jobs, "total": len(self.existing_jobs)})
        if "/api/work-archive/jobs/" in url:
            return _FakeResponse(200, {"id": url.rsplit("/", 1)[-1], "status": self.job_status,
                                       "works": [{"id": "x", "status": "archived"}]})
        return _FakeResponse(404, {})

    def post(self, url, json=None, timeout=None):
        with self.lock:
            self.posts.append((url, json or {}))
        if self.post_status >= 400:
            return _FakeResponse(self.post_status, {"detail": "boom"})
        return _FakeResponse(202, {"id": "job-1", "status": "queued", "work_count": 1})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把台账、媒体根、HTTP 客户端都换成测试态，且不起后台线程。"""
    ledger = tmp_path / "nas_archive.jsonl"
    monkeypatch.setattr(nas_archive, "LEDGER_PATH", ledger)
    monkeypatch.setattr(nas_archive, "ENABLED", True)
    monkeypatch.setattr(nas_archive, "TARGET_ROOT", "wx-archive")
    monkeypatch.setattr(nas_archive, "_ensure_worker", lambda: None)
    monkeypatch.setattr(nas_archive, "_entries", {})
    monkeypatch.setattr(nas_archive, "_pending", nas_archive.queue.Queue())
    monkeypatch.setattr(nas_archive, "_base_url", lambda: "http://127.0.0.1:9999")
    fake = _FakeRequests()
    monkeypatch.setattr(nas_archive, "requests", fake)
    # 媒体根指向 tmp：douyin 根下放一个作品目录
    media_root = tmp_path / "data" / "douyin_downloads"
    monkeypatch.setattr(nas_archive, "REPO_ROOT", tmp_path)
    return {"root": media_root, "ledger": ledger, "http": fake, "tmp": tmp_path}


def _make_work(media_root, work_id: str = "123", files: int = 2):
    work = media_root / "作者" / f"{work_id}_作品"
    work.mkdir(parents=True, exist_ok=True)
    for idx in range(files):
        (work / f"{idx + 1}.jpeg").write_bytes(b"image-%d" % idx)
    return work


def test_resolve_root_maps_platform_dirs(env):
    work = _make_work(env["root"])
    assert nas_archive.resolve_root(work) == ("douyin", "作者/123_作品")
    assert nas_archive.resolve_root(env["tmp"] / "elsewhere") is None


def test_build_manifest_records_sha256(env):
    work = _make_work(env["root"])
    files = nas_archive.build_manifest(work, env["root"])
    assert [f["relpath"] for f in files] == ["作者/123_作品/1.jpeg", "作者/123_作品/2.jpeg"]
    assert all(len(f["digest"]) == 64 for f in files)
    expected = hashlib.sha256(b"image-0").hexdigest()
    assert files[0]["digest"] == expected
    assert files[0]["size"] == len(b"image-0")


def test_submit_is_idempotent(env):
    work = _make_work(env["root"])
    assert nas_archive.submit_path("douyin", "123", work) is True
    # 同一作品重复登记直接拒绝，不产生第二条队列项
    assert nas_archive.submit_path("douyin", "123", work) is False
    assert nas_archive._pending.qsize() == 1
    assert list(nas_archive._entries) == ["douyin|123"]


def test_submit_outside_known_root_is_skipped(env):
    outside = env["tmp"] / "other" / "work"
    outside.mkdir(parents=True)
    assert nas_archive.submit_path("douyin", "999", outside) is False
    assert nas_archive._entries == {}


def test_process_submits_without_deleting_local(env):
    work = _make_work(env["root"])
    nas_archive.submit_path("douyin", "123", work)
    entry = nas_archive._pending.get_nowait()
    nas_archive._process(entry)

    url, payload = env["http"].posts[0]
    assert url.endswith("/api/work-archive/jobs")
    # 自动归档必须 cleanup=false：素材检索与离线打开都依赖本地文件
    assert payload["cleanup"] is False
    assert payload["target_root"] == "wx-archive"
    assert payload["target_dir"] == "douyin"
    sent = payload["works"][0]
    assert sent["id"] == "douyin|123"
    assert sent["source_root"] == "douyin"
    assert len(sent["files"]) == 2

    saved = nas_archive._entries["douyin|123"]
    assert saved["status"] == "submitted"
    assert saved["job_id"] == "job-1"
    assert work.exists()


def test_process_skips_when_service_has_running_job(env):
    work = _make_work(env["root"])
    env["http"].existing_jobs = [{"id": "job-old", "status": "running",
                                  "works": [{"id": "douyin|123"}]}]
    nas_archive.submit_path("douyin", "123", work)
    nas_archive._process(nas_archive._pending.get_nowait())
    assert env["http"].posts == []
    assert nas_archive._entries["douyin|123"]["status"] == "submitted"


def test_process_fails_when_work_dir_missing(env):
    work = _make_work(env["root"])
    nas_archive.submit_path("douyin", "123", work)
    entry = nas_archive._pending.get_nowait()
    for child in sorted(work.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    work.rmdir()
    nas_archive._process(entry)
    assert nas_archive._entries["douyin|123"]["status"] == "failed"
    assert env["http"].posts == []


def test_submit_failure_retries_then_gives_up(env, monkeypatch):
    monkeypatch.setattr(nas_archive, "MAX_ATTEMPTS", 2)
    monkeypatch.setattr(nas_archive, "RETRY_BACKOFF", (0.0, 0.0))
    monkeypatch.setattr(nas_archive.threading, "Timer",
                        lambda delay, fn: type("T", (), {"start": staticmethod(fn)})())
    work = _make_work(env["root"])
    env["http"].post_status = 500

    nas_archive.submit_path("douyin", "123", work)
    nas_archive._process(nas_archive._pending.get_nowait())
    entry = nas_archive._entries["douyin|123"]
    assert entry["status"] == "queued" and entry["attempts"] == 1

    # Timer 的 start() 已把重试项放回队列
    nas_archive._process(nas_archive._pending.get_nowait())
    entry = nas_archive._entries["douyin|123"]
    assert entry["status"] == "failed" and entry["attempts"] == 2


def test_reconcile_marks_archived(env):
    work = _make_work(env["root"])
    nas_archive.submit_path("douyin", "123", work)
    nas_archive._process(nas_archive._pending.get_nowait())
    env["http"].job_status = "completed"
    nas_archive._reconcile()
    assert nas_archive._entries["douyin|123"]["status"] == "archived"


def test_reconcile_requeues_partial_job(env, monkeypatch):
    monkeypatch.setattr(nas_archive.threading, "Timer",
                        lambda delay, fn: type("T", (), {"start": staticmethod(fn)})())
    work = _make_work(env["root"])
    nas_archive.submit_path("douyin", "123", work)
    nas_archive._process(nas_archive._pending.get_nowait())
    env["http"].job_status = "partial"
    nas_archive._reconcile()
    entry = nas_archive._entries["douyin|123"]
    assert entry["status"] == "queued" and entry["attempts"] == 1


def test_manual_retry_only_for_failed(env):
    work = _make_work(env["root"])
    nas_archive.submit_path("douyin", "123", work)
    entry = nas_archive._pending.get_nowait()
    entry.update(status="failed", attempts=3, error="boom")
    nas_archive._save_entry(entry)

    assert nas_archive.retry("douyin|123") is True
    assert nas_archive._entries["douyin|123"]["status"] == "queued"
    assert nas_archive._entries["douyin|123"]["attempts"] == 0
    # 已排队的记录不可再重试，避免重复提交
    assert nas_archive.retry("douyin|123") is False


def test_status_snapshot_counts(env):
    work = _make_work(env["root"])
    nas_archive.submit_path("douyin", "123", work)
    nas_archive._process(nas_archive._pending.get_nowait())
    snap = nas_archive.status_snapshot()
    assert snap["enabled"] is True
    assert snap["target_root"] == "wx-archive"
    assert snap["counts"]["submitted"] == 1
    assert snap["recent"][0]["work_key"] == "douyin|123"


def _write_history(env, platform: str, entries: list[dict]) -> None:
    """把平台历史清单写到测试态的 REPO_ROOT/data/<平台>_history.json。"""
    name = {
        "douyin": "douyin_history.json",
        "kuaishou": "kuaishou_history.json",
        "bilibili": "bilibili_history.json",
        "xhs": "xhs_history.json",
        "channels": "channels_history.json",
        "wechat": "download_history.json",
    }[platform]
    path = env["tmp"] / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def test_archive_platform_works_queues_douyin_history(env):
    # 抖音历史条目不带作品 ID，需从落盘文件名前缀解析
    video = env["root"] / "花石" / "7704317767184485775_作品.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    _write_history(env, "douyin", [
        {"title": "作品", "type": "视频", "path": str(video),
         "source": "花石", "size": "1 MB", "time": "2026-11-16"},
    ])

    result = nas_archive.archive_platform_works("douyin")
    assert result["platform_label"] == "抖音"
    assert result["queued"] == 1
    assert result["skipped"] == 0
    assert "douyin|7704317767184485775" in nas_archive._entries
    assert env["http"].posts == []  # 只登记入队，提交在后台线程

    # 再登记一次应命中台账幂等，全部跳过
    second = nas_archive.archive_platform_works("douyin")
    assert second["queued"] == 0
    assert second["skipped"] == 1


def test_archive_platform_works_filters_by_account(env):
    video_a = env["root"] / "花石" / "111_作品.mp4"
    video_b = env["root"] / "别人" / "222_作品.mp4"
    for video in (video_a, video_b):
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
    _write_history(env, "douyin", [
        {"title": "a", "type": "视频", "path": str(video_a), "source": "花石"},
        {"title": "b", "type": "视频", "path": str(video_b), "source": "别人"},
    ])

    only = nas_archive.archive_platform_works("douyin", "花石")
    assert only["queued"] == 1
    assert list(nas_archive._entries) == ["douyin|111"]

    all_works = nas_archive.archive_platform_works("douyin")
    assert all_works["queued"] == 1  # 只剩 222 未登记
    assert list(nas_archive._entries) == ["douyin|111", "douyin|222"]


def test_archive_platform_works_skips_failed_and_missing_id(env):
    good = env["root"] / "花石" / "333_作品.mp4"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"video")
    # 失败条目不登记；没有 path 的条目也不登记
    _write_history(env, "douyin", [
        {"title": "ok", "type": "视频", "path": str(good), "source": "花石"},
        {"title": "failed", "type": "视频", "path": str(good), "source": "花石", "success": False},
        {"title": "nopath", "type": "视频", "source": "花石"},
    ])
    result = nas_archive.archive_platform_works("douyin")
    assert result["queued"] == 1
    assert result["failed"] == []
    assert list(nas_archive._entries) == ["douyin|333"]


def test_archive_platform_works_rejects_unknown_platform(env):
    with pytest.raises(ValueError):
        nas_archive.archive_platform_works("weibo")


def test_archive_platform_works_wechat_reads_metadata(env):
    # 公众号历史条目不记作品 ID，需读文章目录内的 metadata.json
    articles = env["tmp"] / "data" / "articles_full"
    art_dir = articles / "半夏Floom" / "文章"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "metadata.json").write_text(
        json.dumps({"article_key": "https://mp.weixin.qq.com/s/abc"}), encoding="utf-8")
    _write_history(env, "wechat", [
        {"title": "文章", "link": "https://mp.weixin.qq.com/s/abc",
         "account": "半夏Floom", "path": str(art_dir)},
    ])

    result = nas_archive.archive_platform_works("wechat")
    assert result["queued"] == 1
    key = next(iter(nas_archive._entries))
    assert key == f"wechat|{nas_archive.short_id('https://mp.weixin.qq.com/s/abc')}"
