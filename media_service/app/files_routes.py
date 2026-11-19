"""全局文件检索与内容查重 HTTP 路由（清单/查重只读，支持删除单个文件）。

数据全部来自媒体服务的 SQLite 索引，不做实时磁盘扫描，也不做重新哈希。
删除是破坏性动作：先删物理文件，成功后再清索引，避免「索引隐藏但文件仍在」。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from . import config, media
from .store import STORE

router = APIRouter()


@router.get("/api/files/list")
def list_files(
    q: str | None = Query(None, description="文件名关键词，模糊匹配"),
    root: str | None = Query(None, description="媒体根 id 精确过滤"),
    ext: str | None = Query(None, description="扩展名过滤，如 jpg/mp4/txt"),
    kind: str | None = Query(None, description="文件类型过滤：image/text/video"),
    duplicates: str | None = Query(None, description="duplicates 只看重复，unique 只看唯一"),
    nsfw: str | None = Query(None, description="NSFW 过滤：flagged 只看疑似敏感，unreviewed 只看未复核"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """全库文件联合清单；每行附带该内容摘要的出现次数 ``dup_count``。

    图片行额外带 ``nsfw``（当前内容的有效结论，人工复核优先）；非图片为 ``None``。
    """
    if duplicates not in (None, "duplicates", "unique"):
        raise HTTPException(422, "duplicates 只能是 duplicates/unique")
    if kind is not None and kind not in ("image", "text", "video"):
        raise HTTPException(422, "kind 只能是 image/text/video")
    if nsfw not in (None, "flagged", "unreviewed"):
        raise HTTPException(422, "nsfw 只能是 flagged/unreviewed")
    items, total = STORE.list_files(
        q=q, root=root, ext=ext, kind=kind,
        duplicates=duplicates, nsfw=nsfw, limit=limit, offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/api/files/duplicates")
def duplicates(
    min_count: int = Query(2, ge=2),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    members: int = Query(5, ge=1, le=50, description="每组预览的成员条数"),
):
    """重复内容摘要分组列表，成员截断预览；完整清单走 group 端点。"""
    groups, total = STORE.duplicate_groups(
        min_count=min_count, limit=limit, offset=offset, members=members,
    )
    return {"items": groups, "total": total, "limit": limit, "offset": offset}


@router.get("/api/files/group/{sha}")
def duplicate_group(sha: str):
    """某个内容摘要下的全部文件记录（未截断）。"""
    members = STORE.duplicate_group(sha)
    if not members:
        raise HTTPException(404, "未找到该内容摘要对应的文件")
    return {"sha": sha, "count": len(members), "members": members}


@router.get("/api/files/stats")
def files_stats():
    """文件清单概览：总量、类型/根分布、重复组与涉及文件数、最大文件。"""
    return STORE.files_stats()


def delete_file_record(root: str, relpath: str) -> dict:
    """校验可写根后删除物理文件与索引记录；单文件删除与「撤销入库」共用。

    只删索引不删文件会留下「看不见但占空间」的幽灵数据；只删文件不删索引则会让
    清单残留、缩略图破图。因此顺序固定为：先物理删除，成功后再清索引。
    物理删除失败时索引保持不动，用户可修复后重试。
    """
    root = str(root or "").strip()
    relpath = str(relpath or "").strip()
    if not root or not relpath:
        raise HTTPException(422, "root 与 relpath 均为必填")
    try:
        cfg = config.get_root(root)
    except KeyError:
        raise HTTPException(404, f"未知媒体根：{root}")
    if not cfg.get("writable"):
        raise HTTPException(403, f"媒体根 {root} 为只读，不支持删除")
    try:
        physical = media.delete_entry(root, relpath)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except OSError as exc:
        # 物理删除失败：索引保持不动，用户可重试，避免索引与磁盘错位。
        raise HTTPException(500, f"物理删除失败，索引未变更：{exc}")
    index = STORE.delete_file(root, relpath)
    return {
        "root": root,
        "relpath": relpath,
        "physical": physical,
        "index": index,
        "removed": physical["existed"] or index["removed"] > 0,
    }


@router.post("/api/files/delete")
def delete_file(payload: dict = Body(...)):
    """删除单个文件：物理文件 + 索引记录，仅限可写媒体根。"""
    return delete_file_record(str(payload.get("root") or ""), str(payload.get("relpath") or ""))
