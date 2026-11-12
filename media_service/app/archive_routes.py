"""公众号归档与扫描任务 HTTP 路由。"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from . import config
from .jobs import JOBS
from .parse import extract_upload, extract_urls

router = APIRouter()


@router.get("/api/archive/jobs")
def list_jobs(limit: int = 20, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(422, "limit/offset 超出范围")
    items, total = JOBS.list(limit, offset, "archive")
    return {"items": items, "total": total}


def _target(root_id: str, target_dir: str) -> dict:
    try:
        root = config.get_root(root_id)
    except KeyError as exc:
        raise HTTPException(400, "未知媒体根") from exc
    if not root.get("writable"):
        raise HTTPException(400, "目标媒体根只读")
    parts = target_dir.split("/") if target_dir else []
    if target_dir.startswith("/") or any(p in ("", ".", "..") for p in parts) or "\\" in target_dir or "\x00" in target_dir:
        raise HTTPException(400, "target_dir 必须是安全的根内相对路径")
    return root


def _create(text: str, root_id: str, target_dir: str):
    root = _target(root_id, target_dir)
    urls = extract_urls(text)
    if not urls:
        raise HTTPException(422, "未找到有效公众号文章链接")
    if len(urls) > config.MAX_URLS_PER_JOB:
        raise HTTPException(422, "单任务链接超过 500 条")
    try:
        job = JOBS.create("archive", urls=urls, target_root=root["id"], target_path=root["path"],
                          target_dir=target_dir, articles=[])
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"id": job["id"], "status": job["status"]}


@router.post("/api/archive/jobs", status_code=202)
def create_job(payload: dict):
    text = payload.get("text")
    if not isinstance(text, str):
        raise HTTPException(422, "text 必须是字符串")
    root_id = payload.get("target_root", "wx-archive")
    target_dir = payload.get("target_dir", "")
    if not isinstance(root_id, str) or not isinstance(target_dir, str):
        raise HTTPException(422, "目标 root/path 格式无效")
    return _create(text, root_id, target_dir)


@router.post("/api/archive/jobs/upload", status_code=202)
async def upload_job(file: UploadFile = File(...), target_root: str = Form("wx-archive"), target_dir: str = Form("")):
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "上传文件超过 30 MiB")
    try:
        urls = extract_upload(file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _create("\n".join(urls), target_root, target_dir)


@router.get("/api/archive/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return job


@router.post("/api/archive/jobs/{job_id}/retry", status_code=202)
def retry(job_id: str):
    previous = JOBS.get_job(job_id)
    if previous is None:
        raise HTTPException(404, "任务不存在")
    try:
        item = JOBS.retry(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": item["id"], "status": item["status"], "parent_id": job_id}


@router.post("/api/archive/jobs/{job_id}/verify", status_code=202)
def verify(job_id: str, article_index: int | None = None):
    job = JOBS.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    try:
        ctrl = JOBS.start_verify(job_id, article_index)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": ctrl["id"], "status": ctrl["status"], "expires_seconds": ctrl["expires_seconds"]}


@router.get("/api/archive/jobs/{job_id}/download")
def download(job_id: str):
    job = JOBS.get_job(job_id)
    if job is None or job.get("kind") != "archive":
        raise HTTPException(404, "归档任务不存在")
    if job["status"] not in {"completed", "partial", "interrupted"}:
        raise HTTPException(409, "归档尚未就绪")
    from pathlib import Path
    target = Path(config.DATA_DIR / "jobs" / job_id / "export.zip")
    if target.is_file():
        from .archive_maintenance import begin_download, end_download
        begin_download(job_id)
        return FileResponse(target, filename=f"ai-media-{job_id}.zip", media_type="application/zip",
                            background=BackgroundTask(end_download, job_id))
    try:
        export = JOBS.enqueue_export(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return JSONResponse(status_code=202, content={"export_id": export["id"], "status": export["status"]})


@router.get("/api/archive/exports/{export_id}")
def get_export(export_id: str):
    export = JOBS.get_job(export_id)
    if export is None or export.get("kind") != "export":
        raise HTTPException(404, "导出任务不存在")
    result = {"id": export_id, "status": export["status"], "error": export.get("error"),
              "download_url": f"/api/archive/jobs/{export.get('source_job_id')}/download" if export.get("status") == "completed" else None}
    return result
