"""一次性 NSFW 检测路由：任意目录、单文件上传、结果入库。

与 ``/api/nsfw/scan``（给已入库图片补初筛结论）刻意分开：
- 这里扫的是「还没有进索引」的文件，结果只存在任务目录，不写 ``nsfw_results``；
- 只有显式「入库」才会把文件复制进媒体根，再交给一次增量 ``scan`` 去索引与打分。

因此 probe 不改变覆盖率、待复核队列和索引统计，关掉页面结果就失效（保留 7 天）。
"""
from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import config, media, nsfw_probe, nsfw_scan, review, thumbs
from .files_routes import delete_file_record
from .jobs import JOBS

router = APIRouter()

_KINDS = ("image", "video")


class PrecheckRequest(BaseModel):
    path: str
    recursive: bool = True
    max_files: int = Field(default=nsfw_probe.DEFAULT_MAX_FILES, ge=1, le=nsfw_probe.MAX_PROBE_FILES)


class ProbeRequest(BaseModel):
    path: str
    recursive: bool = True
    kinds: list[str] = Field(default_factory=lambda: list(_KINDS))
    video_interval: float = Field(default=5.0, ge=0.5, le=120)
    max_files: int = Field(default=nsfw_probe.DEFAULT_MAX_FILES, ge=1, le=nsfw_probe.MAX_PROBE_FILES)


class ImportRequest(BaseModel):
    index: int = Field(ge=0)
    root: str
    target_dir: str = ""


class UnimportRequest(BaseModel):
    index: int = Field(ge=0)


def _ensure_model() -> dict:
    """模型没装就不要建任务：否则任务跑完只剩一屏 skipped，用户还得自己猜原因。"""
    state = nsfw_scan.available()
    if not state["model"]:
        raise HTTPException(503, detail={"error": {
            "code": state["reason"] or "nsfw_unavailable",
            "message": "NSFW 模型未安装或检测已关闭，无法发起检测"}})
    return state


def _normalize(raw: str) -> Path:
    try:
        return nsfw_probe.normalize_path(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _probe_job(job_id: str) -> dict:
    item = JOBS.get_job(job_id)
    if item is None or item.get("kind") != "nsfw_probe":
        raise HTTPException(404, "检测任务不存在")
    return item


@router.post("/api/nsfw/probes/check")
async def probe_check(body: PrecheckRequest):
    """只读预检：先告诉用户会遇到多少文件，再决定是否真的开跑。"""
    path = _normalize(body.path)
    try:
        data = await run_in_threadpool(nsfw_probe.precheck, str(path), body.recursive, body.max_files)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return data


@router.post("/api/nsfw/probes", status_code=202)
async def probe_create(body: ProbeRequest):
    kinds = [kind for kind in body.kinds if kind in _KINDS]
    if not kinds:
        raise HTTPException(422, "kinds 至少要包含 image 或 video")
    state = _ensure_model()
    path = _normalize(body.path)
    check = await run_in_threadpool(nsfw_probe.precheck, str(path), body.recursive, body.max_files)
    if check.get("error"):
        raise HTTPException(422, check["error"])
    try:
        job = JOBS.enqueue_probe("path", path=str(path), recursive=body.recursive, kinds=kinds,
                                 video_interval=body.video_interval, max_files=body.max_files,
                                 provider=state["provider"], provider_label=state["provider_label"])
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"id": job["id"], "status": job["status"], "source": "path"}


@router.post("/api/nsfw/probes/upload", status_code=202)
async def probe_upload(file: UploadFile = File(...), video_interval: float = Form(5.0)):
    """上传单个文件。先落 staging 再建任务：``create`` 会立刻入队，反过来会让 worker 读到空文件。"""
    state = _ensure_model()
    name = nsfw_probe.safe_filename(file.filename or "upload")
    kind = nsfw_probe.classify(name)
    if kind is None:
        raise HTTPException(422, "只支持常见图片或视频格式")
    if not (0.5 <= video_interval <= 120):
        raise HTTPException(422, "抽帧间隔需在 0.5–120 秒之间")
    dest = nsfw_probe.staging_path(name)
    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > config.PROBE_UPLOAD_BYTES:
                    raise HTTPException(413, f"上传文件超过 {config.PROBE_UPLOAD_BYTES // 1024 // 1024} MiB")
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "上传文件为空")
        job = JOBS.enqueue_probe("upload", source_path=str(dest), video_interval=video_interval,
                                 max_files=1, provider=state["provider"],
                                 provider_label=state["provider_label"])
    except Exception:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise
    return {"id": job["id"], "status": job["status"], "source": "upload", "name": dest.name, "size": size}


def _decorate(job_id: str, item: dict) -> dict:
    """把结果里的相对文件名换成 job 作用域 URL，前端不用拼接磁盘路径。

    必须只改副本：传入的 item 是 load_results 进程内缓存里的行，嵌套的
    video/hits 又是同一批 dict。原地改写会把 "thumbs/1_0.jpg" 换成完整 URL
    写进缓存，之后 thumb_path 的 startswith("thumbs/") 校验就再也过不了，
    同一个进程里第二次取命中帧缩略图必然 404。
    """
    base = f"/api/nsfw/probes/{job_id}/thumb/{item['index']}"
    item["thumb"] = base if item.get("thumb") else None
    video = item.get("video")
    if isinstance(video, dict):
        video = dict(video)
        hits = []
        for rank, hit in enumerate(video.get("hits") or []):
            if isinstance(hit, dict):
                hit = dict(hit)
                hit["thumb"] = f"{base}?hit={rank}" if hit.get("thumb") else None
            hits.append(hit)
        video["hits"] = hits
        item["video"] = video
    return item


@router.get("/api/nsfw/probes/{job_id}")
def probe_get(job_id: str):
    return _probe_job(job_id)


@router.get("/api/nsfw/probes/{job_id}/items")
def probe_items(job_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                flagged: bool = False):
    _probe_job(job_id)
    data = nsfw_probe.items(job_id, offset, limit, flagged_only=flagged)
    data["items"] = [_decorate(job_id, dict(row)) for row in data["items"]]
    return data


@router.get("/api/nsfw/probes/{job_id}/thumb/{index}")
def probe_thumb(job_id: str, index: int, hit: int | None = Query(None, ge=0, le=2)):
    _probe_job(job_id)
    path = nsfw_probe.thumb_path(job_id, index, hit)
    if path is None:
        raise HTTPException(404, "缩略图不存在")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.post("/api/nsfw/probes/{job_id}/cancel", status_code=202)
def probe_cancel(job_id: str):
    _probe_job(job_id)
    return JOBS.cancel(job_id)


@router.post("/api/nsfw/probes/{job_id}/import")
async def probe_import(job_id: str, body: ImportRequest):
    """把一条结果复制进媒体根，并提交一次增量扫描去建索引。"""
    job = _probe_job(job_id)
    if job.get("status") not in {"completed", "partial", "cancelled"}:
        raise HTTPException(409, "检测还没结束，暂不能入库")
    try:
        config.get_root(body.root)
    except KeyError as exc:
        raise HTTPException(422, "未知媒体根") from exc
    try:
        data = await run_in_threadpool(nsfw_probe.import_item, job, body.index, body.root, body.target_dir)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(502, f"写入媒体根失败：{exc}") from exc
    return data


@router.get("/api/nsfw/probes/{job_id}/file/{index}")
def probe_file(job_id: str, index: int):
    """查看已入库的副本（图片 / 视频）。

    刻意不复用 ``/api/media/file``：那个端点靠索引行做版本校验，而入库之后到增量
    扫描跑完之间索引还不存在（队列满时扫描根本没提交），用户刚入库就点查看会拿到
    404。这里直接按 results.json 记的副本路径读，语义是「看我刚入库的那个文件」。
    """
    _probe_job(job_id)
    row = nsfw_probe.find_item(job_id, index)
    if row is None:
        raise HTTPException(404, "检测结果不存在")
    imported = row.get("imported") or {}
    root_id = imported.get("root")
    relpath = imported.get("relpath")
    if not root_id or not relpath:
        raise HTTPException(409, "该结果还没有入库，没有可查看的副本")
    if row.get("kind") == "video":
        try:
            path = media.local_path(root_id, relpath)
        except ValueError as exc:
            # 远端根没有本地路径，无法按 Range 播放；先明确拒绝而不是整段下载到内存。
            raise HTTPException(503, str(exc)) from exc
        if not path.is_file():
            raise HTTPException(404, "已入库副本不存在，可能已被删除")
        return FileResponse(path, media_type=mimetypes.guess_type(relpath)[0] or "application/octet-stream")
    try:
        data = media.read_bytes(root_id, relpath)
    except FileNotFoundError as exc:
        raise HTTPException(404, "已入库副本不存在，可能已被删除") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(503, f"读取副本失败：{exc}") from exc
    payload, mime = thumbs.render_display(data)
    return Response(payload, media_type=mime, headers={"Cache-Control": "no-store"})


@router.post("/api/nsfw/probes/{job_id}/unimport")
async def probe_unimport(job_id: str, body: UnimportRequest):
    """撤销入库：删掉媒体根里的副本并清索引，检测结果保留（可以重新入库）。

    先删副本再清标记：反过来的话中途失败会留下「标记已清、副本还在」的孤儿文件，
    而且用户看不出它还占着空间。
    """
    _probe_job(job_id)
    row = nsfw_probe.find_item(job_id, body.index)
    if row is None:
        raise HTTPException(404, "检测结果不存在")
    imported = row.get("imported") or {}
    if not imported.get("root") or not imported.get("relpath"):
        raise HTTPException(409, "该结果还没有入库")
    deleted = await run_in_threadpool(delete_file_record, imported["root"], imported["relpath"])
    nsfw_probe.drop_imported(job_id, body.index)
    return {"root": deleted["root"], "relpath": deleted["relpath"], "removed": deleted["removed"]}
