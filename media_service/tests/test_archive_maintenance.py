from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import archive_maintenance, config
from app.jobs import JobManager


def _prepare(tmp_path, monkeypatch, *, uploaded_at, confirmed=True):
    data = tmp_path / "data"
    jobs = data / "jobs"
    job_dir = jobs / "job-1"
    job_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "archive", "type": "zspace",
                                                   "path": "/remote", "writable": True},))
    payload = b"verified png bytes"
    local = job_dir / "asset.png"
    local.write_bytes(payload)
    job = {"id": "job-1", "kind": "archive", "target_root": "archive",
           "target_path": "/remote", "articles": [{"files": [{
               "local_path": str(local), "relpath": "article/asset.png",
               "sha256": hashlib.sha256(payload).hexdigest(), "uploaded_at": uploaded_at,
               "upload_confirmed": confirmed, "manifest_confirmed": True,
                "manifest_path": "_manifests/job-1.json",
           }]}]}
    (jobs / "job-1.json").write_text(json.dumps(job), encoding="utf-8")
    monkeypatch.setattr(archive_maintenance.media, "read_bytes", lambda *_: payload)
    return local, jobs / "job-1.json"


def test_cleanup_retains_file_until_full_seven_days(tmp_path, monkeypatch):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    local, _ = _prepare(tmp_path, monkeypatch,
                        uploaded_at=(now - timedelta(days=7) + timedelta(microseconds=1)).isoformat())

    result = archive_maintenance.cleanup_expired(now)

    assert result["deleted"] == 0
    assert local.exists()


def test_cleanup_requires_confirmed_matching_remote_file(tmp_path, monkeypatch):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    local, report_path = _prepare(tmp_path, monkeypatch,
                                  uploaded_at=(now - timedelta(days=7)).isoformat(), confirmed=False)

    result = archive_maintenance.cleanup_expired(now)

    assert result["deleted"] == 0
    assert local.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["articles"][0]["files"][0].get("cleaned_at") is None


def test_cleanup_deletes_only_after_remote_digest_recheck(tmp_path, monkeypatch):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    local, _ = _prepare(tmp_path, monkeypatch,
                        uploaded_at=(now - timedelta(days=7)).isoformat())

    result = archive_maintenance.cleanup_expired(now)

    assert result["deleted"] == 1
    assert not local.exists()


def test_cleanup_preserves_local_when_remote_digest_differs(tmp_path, monkeypatch):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    local, _ = _prepare(tmp_path, monkeypatch,
                        uploaded_at=(now - timedelta(days=8)).isoformat())
    monkeypatch.setattr(archive_maintenance.media, "read_bytes", lambda *_: b"different remote")

    result = archive_maintenance.cleanup_expired(now)

    assert result["deleted"] == 0
    assert local.exists()
    assert any(error["error"] == "remote_digest_mismatch" for error in result["errors"])


def test_retry_reuses_one_active_job_and_keeps_parent_snapshot(tmp_path):
    manager = object.__new__(JobManager)
    manager.dir = tmp_path
    manager.lock = __import__("threading").RLock()
    manager.pending = __import__("queue").Queue()
    parent = {"id": "parent", "kind": "archive", "status": "interrupted",
              "urls": ["https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1"],
              "target_root": "root", "target_path": "/root", "target_dir": "",
              "articles": [{"url": "u", "status": "verify", "files": []}]}
    manager.jobs = {"parent": parent}

    first = manager.retry("parent")
    repeated = manager.retry("parent")
    first["articles"][0]["status"] = "completed"

    assert first["id"] == repeated["id"]
    assert parent["articles"][0]["status"] == "verify"


def test_cleanup_does_not_delete_outside_owned_job_directory(tmp_path, monkeypatch):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    local, report_path = _prepare(tmp_path, monkeypatch,
                                  uploaded_at=(now - timedelta(days=8)).isoformat())
    outside = tmp_path / "valuable.png"
    outside.write_bytes(local.read_bytes())
    job = json.loads(report_path.read_text(encoding="utf-8"))
    job["articles"][0]["files"][0]["local_path"] = str(outside)
    report_path.write_text(json.dumps(job), encoding="utf-8")

    result = archive_maintenance.cleanup_expired(now)

    assert result["deleted"] == 0
    assert outside.exists()
    assert any(error["error"] == "path_outside_job_data" for error in result["errors"])


def test_cleanup_requires_confirmed_source_manifest(tmp_path, monkeypatch):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    local, path = _prepare(tmp_path, monkeypatch, uploaded_at=(now - timedelta(days=8)).isoformat())
    job = json.loads(path.read_text())
    job["articles"][0]["files"][0]["manifest_confirmed"] = False
    path.write_text(json.dumps(job))
    result = archive_maintenance.cleanup_expired(now)
    assert result["deleted"] == 0 and local.exists()


def test_verification_wait_does_not_block_scan(tmp_path, monkeypatch):
    from threading import Event
    from app.jobs import JobManager
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "MIN_FREE_BYTES", 0)
    scanned = Event()
    archived = Event()
    def execute(job):
        if job["kind"] == "scan":
            scanned.set()
        if job["kind"] == "archive":
            archived.set()
        job["status"] = "completed"
    monkeypatch.setattr(JobManager, "_execute", staticmethod(execute))
    manager = JobManager()
    manager.verify_done.clear()
    manager.create("archive")
    manager.create("scan")
    assert scanned.wait(2)
    assert not archived.is_set()
    manager.verify_done.set()
    assert archived.wait(2)


def test_shared_job_limit_is_checked_atomically(tmp_path, monkeypatch):
    from threading import Event
    from app.jobs import JobManager
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "MIN_FREE_BYTES", 0)
    monkeypatch.setattr(config, "MAX_PENDING_JOBS", 1)
    release = Event()
    def execute(job):
        release.wait(2)
        job["status"] = "completed"
    monkeypatch.setattr(JobManager, "_execute", staticmethod(execute))
    manager = JobManager()
    manager.create("scan")
    import pytest
    try:
        with pytest.raises(RuntimeError, match="queue_full"):
            manager.create("archive")
    finally:
        release.set()


def test_export_requires_every_manifest_file_and_publishes_complete_zip(tmp_path, monkeypatch):
    import hashlib
    from zipfile import ZipFile
    from app import archive_maintenance
    data = tmp_path / "data"
    source_file = data / "jobs" / "archive-1" / "image.png"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image bytes")
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "r", "type": "local", "path": str(tmp_path), "writable": True},))
    source = {"id": "archive-1", "kind": "archive", "target_root": "r", "target_path": str(tmp_path),
              "articles": [{"files": [{"local_path": str(source_file), "relpath": "article/image.png",
                                       "sha256": hashlib.sha256(b"image bytes").hexdigest()}]}]}
    monkeypatch.setattr("app.jobs.JOBS.get_job", lambda id: source)
    export = {"id": "export-1", "source_job_id": "archive-1", "status": "running"}
    archive_maintenance.build_export(export)
    with ZipFile(export["export_path"]) as archive:
        assert archive.read("article/image.png") == b"image bytes"
    source_file.write_bytes(b"wrong")
    export2 = {"id": "export-2", "source_job_id": "archive-1", "status": "running"}
    import pytest
    with pytest.raises(ValueError, match="摘要不一致"):
        archive_maintenance.build_export(export2)


def test_archive_job_list_excludes_scan_export_and_verify():
    from threading import RLock
    from app.jobs import JobManager
    manager = object.__new__(JobManager)
    manager.lock = RLock()
    manager.jobs = {kind: {"id": kind, "kind": kind, "created": str(i)} for i, kind in enumerate(("archive", "scan", "export", "verify"))}
    items, total = manager.list(20, 0, "archive")
    assert total == 1 and [item["id"] for item in items] == ["archive"]
