"""标签 HTTP 路由：文件（图片）标签与作品（内容条目）标签。

存储层（``store.py``）早已实现两套标签，语义也是定好的，这里只补出入口：

- **文件标签**挂在 ``content_sha256`` 上：同一内容的所有归档副本共享一套标签，
  给其中任一副本打的标签，其余副本立刻可见；文件内容变了（摘要变）旧标签自然失效。
- **作品标签**挂在 ``work_key``（平台 + 稳定作品 ID）上：同一作品的多份归档副本
  共享一套标签，按标签列作品时只出一条。

单独成文件而不是并进 ``media_routes.py`` 的理由：标签是独立维度（不属于检索、
不属于归档任务），一个路由模块对应一个职责；``media_routes.py`` 已经承载了检索、
缩略图、文件、全景、复核五件事。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import config
from .media_routes import _content_out, _decorate_items, _media_url
from .store import STORE

router = APIRouter()

# 标签是人手输入的短标记，不是描述：限长 32 是为了让它在列表里能整行显示。
TAG_MAX_LEN = 32


def _invalid(message):
    return HTTPException(422, detail={"error": {"code": "invalid_tag", "message": message}})


def _normalize_tag(tag: str) -> str:
    """标签的唯一写法：只去首尾空白，中间的空格、中文、连字符都原样保留。

    不做大小写归一、不做同义词合并——标签是用户自己的词汇表，悄悄改写比重复更糟。
    """
    text = (tag or "").strip()
    if not text:
        raise _invalid("标签不能为空")
    if len(text) > TAG_MAX_LEN:
        raise _invalid(f"标签最长 {TAG_MAX_LEN} 个字符")
    if any(ord(ch) < 32 for ch in text):
        raise _invalid("标签不能包含换行或其他控制字符")
    return text


def _require_root(root: str) -> None:
    try:
        config.get_root(root)
    except KeyError:
        raise HTTPException(422, "未知媒体根")


def _require_mode(mode: str) -> str:
    if mode not in ("add", "remove"):
        raise _invalid("mode 必须是 add 或 remove")
    return mode


class TagImageRequest(BaseModel):
    root: str
    relpath: str
    tag: str
    mode: str = "add"


class RenameTagRequest(BaseModel):
    old: str
    new: str


class TagContentRequest(BaseModel):
    content_id: str
    tag: str
    mode: str = "add"


@router.get("/api/tags")
def list_tags():
    """标签字典与使用次数（文件标签按文件行计，作品标签按条目行计）。"""
    items = STORE.list_tags()
    return {"items": items, "total": len(items)}


@router.get("/api/tags/images")
def images_by_tag(tag: str, limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0)):
    """某标签下的图片。

    同一内容的多个归档副本会各成一条（它们共享标签，也各自有独立的路径与版本），
    所以这里不按摘要去重——去重会让「这张图在哪些地方」这个信息丢掉。
    """
    rows = STORE.images_by_tag(_normalize_tag(tag), limit=limit, offset=offset)
    # 搜索结果的字段名是 version，标签列表沿用它，前端可以直接把行喂给同一个查看器
    items = [{**row, "version": row["content_version"]} for row in rows]
    _decorate_items(items)
    for item in items:
        item["thumb"] = _media_url("/api/media/thumb", item["root"], item["relpath"], item["version"], size=360)
        item["file"] = _media_url("/api/media/file", item["root"], item["relpath"], item["version"], display=1)
    return {"items": items, "limit": limit, "offset": offset,
            # 存储层只给一页数据，不额外跑 COUNT：用满页即视为可能还有下一页
            "has_more": len(rows) == limit}


@router.get("/api/tags/image")
def image_tags(root: str, relpath: str):
    """单张图片当前可见的标签（同内容副本共享）。"""
    _require_root(root)
    return {"root": root, "relpath": relpath, "tags": STORE.image_tags(root, relpath)}


@router.post("/api/tags/image")
def tag_image(body: TagImageRequest):
    """给图片打标签或去标签；返回变更后的完整标签列表。"""
    _require_root(body.root)
    tag = _normalize_tag(body.tag)
    mode = _require_mode(body.mode)
    try:
        tags = STORE.tag_image(body.root, body.relpath, tag, mode=mode)
    except KeyError:
        raise HTTPException(404, detail={"error": {"code": "not_found",
                                                  "message": "该图片不在索引中，请先扫描"}})
    except ValueError as exc:
        # 索引里没有 content_sha256 的旧记录无法跨副本共享标签，明确拒绝而不是静默跳过
        raise HTTPException(409, detail={"error": {"code": "not_indexed", "message": str(exc)}})
    return {"root": body.root, "relpath": body.relpath, "tags": tags}


@router.get("/api/tags/contents")
def contents_by_tag(tag: str, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    """某标签下的作品；同一 work_key 的多份归档副本只出一条。"""
    rows = STORE.contents_by_tag(_normalize_tag(tag), limit=limit, offset=offset)
    return {"items": [_content_out(row, []) for row in rows],
            "limit": limit, "offset": offset, "has_more": len(rows) == limit}


@router.get("/api/tags/content")
def content_tags(content_id: str):
    """单个作品当前可见的标签（同 work_key 副本共享）。"""
    return {"content_id": content_id, "tags": STORE.content_tags(content_id)}


@router.post("/api/tags/content")
def tag_content(body: TagContentRequest):
    """给作品打标签或去标签；同 work_key 的所有归档副本一起生效。"""
    tag = _normalize_tag(body.tag)
    mode = _require_mode(body.mode)
    try:
        tags = STORE.tag_content(body.content_id, tag, mode=mode)
    except KeyError:
        raise HTTPException(404, detail={"error": {"code": "not_found", "message": "内容条目不存在"}})
    except ValueError as exc:
        raise HTTPException(422, detail={"error": {"code": "invalid_tag", "message": str(exc)}})
    return {"content_id": body.content_id, "tags": tags}


@router.post("/api/tags/rename")
def rename_tag(body: RenameTagRequest):
    """把标签整体改名，文件标签与作品标签一起改；新名字已存在时合并成一个。

    改的是词汇表而不是某一条记录：标签是用户自己的分类法，改名意味着「我之前
    叫它的那个名字，从今往后都叫这个」，所以不做「只改这一张图」的选项。
    """
    old = _normalize_tag(body.old)
    new = _normalize_tag(body.new)
    try:
        result = STORE.rename_tag(old, new)
    except KeyError:
        raise HTTPException(404, detail={"error": {"code": "not_found",
                                                 "message": "该标签不存在，可能已被改名或删除"}})
    except ValueError as exc:
        raise _invalid(str(exc))
    return result
