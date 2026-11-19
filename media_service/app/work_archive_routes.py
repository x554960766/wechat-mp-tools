"""多平台作品归档任务 HTTP 路由。

提交带作品清单（平台 + 稳定作品 ID + 本地文件清单）的批量归档任务，
落 `work_archive` 任务并复用 NAS 校验成功后清理本地的七态状态机。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import config
from .archive_routes import _target
from .jobs import JOBS
from .work_archive import WORK_STATUS_LABELS

router = APIRouter()

_HEX = set("0123456789abcdef")


def _check_digest(digest: object, relpath: str) -> str:
    if not isinstance(digest, str) or len(digest) != 64 or not set(digest.lower()) <= _HEX:
        raise HTTPException(422, f"文件摘要不是合法的 sha256：{relpath}")
    return digest.lower()


def _check_files(files: object, work_id: str) -> list[dict]:
    if not isinstance(files, list) or not files:
        raise HTTPException(422, f"作品 {work_id} 的文件清单必须是非空列表")
    out: list[dict] = []
    for raw in files:
        if not isinstance(raw, dict):
            raise HTTPException(422, f"作品 {work_id} 的文件项必须是对象")
        relpath = raw.get("relpath")
        if not isinstance(relpath, str) or not relpath:
            raise HTTPException(422, f"作品 {work_id} 的文件缺少 relpath")
        size = raw.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise HTTPException(422, f"作品 {work_id} 的文件 size 无效：{relpath}")
        digest = _check_digest(raw.get("digest"), relpath)
        out.append({"relpath": relpath, "size": size, "digest": digest,
                    "mtime": raw.get("mtime")})
    return out


def _check_source_root(value: object, work_id: str) -> str:
    """作品的源媒体根必须已注册且是本地目录：远端根无法保证「本地文件仍属同一版本」。"""
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(422, f"作品 {work_id} 缺少 source_root")
    root_id = value.strip()
    try:
        root = config.get_root(root_id)
    except KeyError as exc:
        raise HTTPException(422, f"作品 {work_id} 的 source_root 未注册：{root_id}") from exc
    if root.get("type") != "local":
        raise HTTPException(422, f"作品 {work_id} 的 source_root 必须是本地根：{root_id}")
    return root_id


def _check_works(works: object) -> list[dict]:
    if not isinstance(works, list) or not works:
        raise HTTPException(422, "作品清单必须是非空列表")
    if len(works) > config.MAX_URLS_PER_JOB:
        raise HTTPException(422, "单任务作品超过上限")
    out: list[dict] = []
    seen: set[str] = set()
    for raw in works:
        if not isinstance(raw, dict):
            raise HTTPException(422, "作品项必须是对象")
        work_id = raw.get("id")
        platform = raw.get("platform")
        if not isinstance(work_id, str) or not work_id.strip():
            raise HTTPException(422, "作品缺少稳定作品 ID")
        if not isinstance(platform, str) or not platform.strip():
            raise HTTPException(422, f"作品 {work_id} 缺少平台")
        if work_id in seen:
            raise HTTPException(422, f"作品 ID 重复：{work_id}")
        seen.add(work_id)
        out.append({"id": work_id.strip(), "platform": platform.strip(),
                    "source_root": _check_source_root(raw.get("source_root"), work_id),
                    "files": _check_files(raw.get("files"), work_id)})
    return out


@router.get("/api/work-archive/jobs")
def list_jobs(limit: int = 20, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(422, "limit/offset 超出范围")
    items, total = JOBS.list(limit, offset, "work_archive")
    return {"items": items, "total": total}


@router.post("/api/work-archive/jobs", status_code=202)
def create_job(payload: dict):
    works = _check_works(payload.get("works"))
    root_id = payload.get("target_root", "wx-archive")
    target_dir = payload.get("target_dir", "")
    cleanup = payload.get("cleanup", True)
    if not isinstance(root_id, str) or not isinstance(target_dir, str):
        raise HTTPException(422, "目标 root/path 格式无效")
    if not isinstance(cleanup, bool):
        raise HTTPException(422, "cleanup 必须是布尔值")
    root = _target(root_id, target_dir)
    try:
        job = JOBS.create("work_archive", works=works, target_root=root["id"],
                          target_path=root["path"], target_dir=target_dir,
                          cleanup=cleanup)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"id": job["id"], "status": job["status"],
            "work_count": len(works), "status_labels": WORK_STATUS_LABELS}


@router.get("/api/work-archive/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get_job(job_id)
    if job is None or job.get("kind") != "work_archive":
        raise HTTPException(404, "作品归档任务不存在")
    return job


@router.post("/api/work-archive/jobs/{job_id}/retry", status_code=202)
def retry(job_id: str):
    previous = JOBS.get_job(job_id)
    if previous is None or previous.get("kind") != "work_archive":
        raise HTTPException(404, "作品归档任务不存在")
    try:
        item = JOBS.retry(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": item["id"], "status": item["status"], "parent_id": job_id}
