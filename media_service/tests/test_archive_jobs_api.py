"""归档任务 HTTP 契约：前端「链接批量归档 / 验证窗口 / 导出 ZIP」三个入口依赖这张脸。

只钉请求形状与状态码，不跑真实抓取（JOBS 全部打桩，不起浏览器、不联网）。
"""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import config
from app.jobs import JOBS
from app.main import app

ARTICLE = "https://mp.weixin.qq.com/s?__biz=MzA5&mid=2650&idx=1&sn=abc123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA_ROOTS", (
        {"id": "wx-archive", "type": "local", "path": str(root), "writable": True},
        {"id": "readonly", "type": "local", "path": str(root), "writable": False},
    ))
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _fake_create(record: dict):
    def create(kind, **values):
        record.clear()
        record.update({"kind": kind, **values})
        return {"id": "job-1", "status": "queued"}
    return create


def test_create_job_passes_urls_and_target(client, monkeypatch):
    record = {}
    monkeypatch.setattr(JOBS, "create", _fake_create(record))

    resp = client.post("/api/archive/jobs",
                       json={"text": f"看这篇 {ARTICLE} 不错", "target_root": "wx-archive",
                             "target_dir": "汽车/某号"})

    assert resp.status_code == 202
    assert resp.json() == {"id": "job-1", "status": "queued"}
    assert record["kind"] == "archive"
    assert record["urls"] == [ARTICLE]
    assert record["target_root"] == "wx-archive"
    assert record["target_dir"] == "汽车/某号"


def test_create_job_rejects_bad_target_and_empty_text(client, monkeypatch):
    record = {}
    monkeypatch.setattr(JOBS, "create", _fake_create(record))

    assert client.post("/api/archive/jobs",
                       json={"text": ARTICLE, "target_root": "nope"}).status_code == 400
    assert client.post("/api/archive/jobs",
                       json={"text": ARTICLE, "target_root": "readonly"}).status_code == 400
    assert client.post("/api/archive/jobs",
                       json={"text": ARTICLE, "target_dir": "../etc"}).status_code == 400
    assert client.post("/api/archive/jobs", json={"text": "没有链接"}).status_code == 422
    assert client.post("/api/archive/jobs", json={"text": 123}).status_code == 422
    assert record == {}  # 校验失败一律不建任务


def test_upload_txt_and_zip_create_job(client, monkeypatch):
    record = {}
    monkeypatch.setattr(JOBS, "create", _fake_create(record))

    txt = client.post("/api/archive/jobs/upload",
                      files={"file": ("links.txt", ARTICLE.encode(), "text/plain")},
                      data={"target_root": "wx-archive", "target_dir": "汽车"})
    assert txt.status_code == 202
    assert record["urls"] == [ARTICLE]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("a.txt", ARTICLE)
        archive.writestr("skip.png", b"\x89PNG")
    zipped = client.post("/api/archive/jobs/upload",
                         files={"file": ("links.zip", buf.getvalue(), "application/zip")},
                         data={"target_root": "wx-archive"})
    assert zipped.status_code == 202
    assert record["urls"] == [ARTICLE]


def test_upload_rejects_unsupported_file(client, monkeypatch):
    record = {}
    monkeypatch.setattr(JOBS, "create", _fake_create(record))

    resp = client.post("/api/archive/jobs/upload",
                       files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert resp.status_code == 422
    assert "txt" in resp.json()["error"]["message"]
    assert record == {}


def test_verify_returns_expiry_and_forwards_index(client, monkeypatch):
    seen = {}

    def fake_verify(job_id, index=None):
        seen.update({"job_id": job_id, "index": index})
        return {"id": "verify-1", "status": "queued", "expires_seconds": 180}

    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "archive"})
    monkeypatch.setattr(JOBS, "start_verify", fake_verify)

    resp = client.post("/api/archive/jobs/job-1/verify?article_index=2")
    assert resp.status_code == 202
    assert resp.json()["expires_seconds"] == 180
    assert seen == {"job_id": "job-1", "index": 2}


def test_verify_conflict_and_missing_job(client, monkeypatch):
    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "archive"})

    def busy(job_id, index=None):
        raise ValueError("已有人工验证窗口正在运行")

    monkeypatch.setattr(JOBS, "start_verify", busy)
    assert client.post("/api/archive/jobs/job-1/verify").status_code == 409

    monkeypatch.setattr(JOBS, "get_job", lambda job_id: None)
    assert client.post("/api/archive/jobs/missing/verify").status_code == 404


def test_download_enqueues_export_then_streams(client, monkeypatch):
    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "archive",
                                                         "status": "completed"})
    monkeypatch.setattr(JOBS, "enqueue_export",
                        lambda job_id: {"id": "export-1", "status": "queued"})

    resp = client.get("/api/archive/jobs/job-1/download")
    assert resp.status_code == 202
    assert resp.json() == {"export_id": "export-1", "status": "queued"}

    # zip 落盘后同一条 URL 直接回文件，前端靠 content-type 区分这两种响应
    target = config.DATA_DIR / "jobs" / "job-1" / "export.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    streamed = client.get("/api/archive/jobs/job-1/download")
    assert streamed.status_code == 200
    assert streamed.headers["content-type"] == "application/zip"


def test_download_not_ready_and_export_status(client, monkeypatch):
    monkeypatch.setattr(JOBS, "get_job", lambda job_id: {"id": job_id, "kind": "archive",
                                                         "status": "running"})
    assert client.get("/api/archive/jobs/job-1/download").status_code == 409

    exports = {"export-1": {"id": "export-1", "kind": "export", "status": "completed",
                            "source_job_id": "job-1"}}
    monkeypatch.setattr(JOBS, "get_job", lambda job_id: exports.get(job_id))
    done = client.get("/api/archive/exports/export-1").json()
    assert done["status"] == "completed"
    assert done["download_url"] == "/api/archive/jobs/job-1/download"

    exports["export-1"]["status"] = "queued"
    pending = client.get("/api/archive/exports/export-1").json()
    assert pending["download_url"] is None
    assert client.get("/api/archive/exports/missing").status_code == 404
