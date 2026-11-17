"""多平台作品归档任务 HTTP 路由的外部请求边界。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.jobs import JOBS
from app.main import app


def _roots(tmp_path):
    """可写目标根（wx-archive）、只读根（iphone）、本地源根（articles-full）与远端源根。"""
    dst = tmp_path / "wx"
    dst.mkdir()
    src = tmp_path / "articles"
    src.mkdir()
    return [
        {"id": "wx-archive", "type": "local", "path": str(dst), "writable": True},
        {"id": "iphone", "type": "local", "path": str(tmp_path / "iphone"), "writable": False},
        {"id": "articles-full", "type": "local", "path": str(src), "writable": False},
        {"id": "nas-remote", "type": "zspace", "path": "/sata11/my/data", "writable": True},
    ]


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MEDIA_ROOTS", _roots(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data" / "jobs").mkdir(parents=True, exist_ok=True)
    # JOBS 是进程级单例，清空上一组测试遗留的任务，避免计数串扰
    with JOBS.lock:
        JOBS.jobs.clear()
    # 不触真实归档执行：桩掉调度入口
    monkeypatch.setattr(JOBS, "_execute", staticmethod(lambda item: None))


def _work(work_id="w1", platform="wechat", files=None, source_root="articles-full"):
    if files is None:
        files = [{"relpath": "a.txt", "size": 3, "digest": "a" * 64, "mtime": 0}]
    return {"id": work_id, "platform": platform, "source_root": source_root, "files": files}


def _client():
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_create_list_get_round_trip(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = _client()
    resp = client.post("/api/work-archive/jobs",
                       json={"works": [_work()], "target_root": "wx-archive", "target_dir": "a"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["work_count"] == 1
    assert body["status"] == "queued"
    assert body["status_labels"]

    listed = client.get("/api/work-archive/jobs").json()
    assert listed["total"] == 1
    job_id = listed["items"][0]["id"]

    detail = client.get(f"/api/work-archive/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["kind"] == "work_archive"
    assert detail.json()["works"][0]["id"] == "w1"


def test_retry_requeues_work_archive_job(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = _client()
    job_id = client.post("/api/work-archive/jobs",
                         json={"works": [_work()], "target_root": "wx-archive"}).json()["id"]
    with JOBS.lock:
        JOBS.jobs[job_id]["status"] = "failed"
    resp = client.post(f"/api/work-archive/jobs/{job_id}/retry")
    assert resp.status_code == 202
    body = resp.json()
    assert body["parent_id"] == job_id
    assert body["id"] != job_id
    assert body["status"] == "queued"


def test_retry_unknown_or_foreign_kind(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = _client()
    assert client.post("/api/work-archive/jobs/nope/retry").status_code == 404

    other = JOBS.create("scan", root="x")
    assert client.post(f"/api/work-archive/jobs/{other['id']}/retry").status_code == 404


def test_reject_bad_payloads(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = _client()

    assert client.post("/api/work-archive/jobs", json={"works": []}).status_code == 422
    assert client.post("/api/work-archive/jobs", json={}).status_code == 422
    # 作品缺平台 / ID 重复
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work(platform="")]}).status_code == 422
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work(), _work()]}).status_code == 422
    # 文件清单缺 relpath / size 非法 / 摘要非法
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work(files=[{"size": 1, "digest": "a" * 64}])]}).status_code == 422
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work(files=[{"relpath": "a.txt", "size": -1, "digest": "a" * 64}])]}).status_code == 422
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work(files=[{"relpath": "a.txt", "size": 1, "digest": "zz"}])]}).status_code == 422
    # 目标根与目录
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work()], "target_root": "missing"}).status_code == 400
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work()], "target_root": "iphone"}).status_code == 400
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work()], "target_root": "wx-archive", "target_dir": "/x"}).status_code == 400
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work()], "target_root": "wx-archive", "target_dir": "a/../b"}).status_code == 400


def test_list_pagination_bounds(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = _client()
    assert client.get("/api/work-archive/jobs?limit=0").status_code == 422
    assert client.get("/api/work-archive/jobs?limit=101").status_code == 422
    assert client.get("/api/work-archive/jobs?offset=-1").status_code == 422
    assert client.get("/api/work-archive/jobs?limit=1&offset=0").status_code == 200


def test_get_unknown_job(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert _client().get("/api/work-archive/jobs/nope").status_code == 404


def test_reject_invalid_source_root(monkeypatch, tmp_path):
    """source_root 必须已注册且是本地根：远端根无法保证「本地文件仍属同一版本」。"""
    _setup(monkeypatch, tmp_path)
    client = _client()
    missing = _work()
    missing.pop("source_root")
    assert client.post("/api/work-archive/jobs", json={"works": [missing]}).status_code == 422
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work(source_root="nope")]}).status_code == 422
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work(source_root="nas-remote")]}).status_code == 422
    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work(source_root="")]}).status_code == 422


def test_cleanup_flag_persisted_and_validated(monkeypatch, tmp_path):
    """cleanup 是任务级开关：默认 True（手动归档），自动归档显式传 False。"""
    _setup(monkeypatch, tmp_path)
    client = _client()
    auto = client.post("/api/work-archive/jobs",
                       json={"works": [_work()], "cleanup": False})
    assert auto.status_code == 202
    assert client.get(f"/api/work-archive/jobs/{auto.json()['id']}").json()["cleanup"] is False

    manual = client.post("/api/work-archive/jobs", json={"works": [_work("w2")]})
    assert manual.status_code == 202
    assert client.get(f"/api/work-archive/jobs/{manual.json()['id']}").json()["cleanup"] is True

    assert client.post("/api/work-archive/jobs",
                       json={"works": [_work()], "cleanup": "no"}).status_code == 422
