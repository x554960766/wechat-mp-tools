"""图片与检索 HTTP 路由。"""
from __future__ import annotations
import hashlib
import threading
from datetime import datetime,timezone
from fastapi import APIRouter,HTTPException,Query,UploadFile,File,Form,Body
from fastapi.responses import Response
from pydantic import BaseModel,Field,StrictInt,field_validator
from starlette.concurrency import run_in_threadpool
import re
from urllib.parse import urlencode
from . import config,contents,frames,media,encoder,review,search,thumbs
from .store import STORE
from .jobs import JOBS

router=APIRouter()
_reachable={r["id"]:{"reachable":None,"checked_at":None} for r in config.MEDIA_ROOTS}
_probe_lock=threading.Lock(); _probing=set()

def _refresh_roots():
    now=datetime.now(timezone.utc)
    for item in config.MEDIA_ROOTS:
        root_id=item["id"]; checked=_reachable[root_id]["checked_at"]
        if checked and (now-datetime.fromisoformat(checked)).total_seconds()<15: continue
        with _probe_lock:
            if root_id in _probing: continue
            _probing.add(root_id)
        def probe(rid=root_id):
            try: reachable=media.check_root(rid)
            except Exception: reachable=False
            _reachable[rid].update(reachable=reachable,checked_at=datetime.now(timezone.utc).isoformat())
            with _probe_lock: _probing.discard(rid)
        threading.Thread(target=probe,name=f"media-root-{root_id}",daemon=True).start()

def _decorate_items(items):
    _refresh_roots()
    states={root_id:state["reachable"] for root_id,state in _reachable.items()}
    for item in items:
        reachable=states.get(item["root"])
        item["source_state"]="online" if reachable is True else ("offline" if reachable is False else "unknown")
        item["preview_cached"]=thumbs.cache_path(item["root"],item["relpath"],item["version"]).is_file()
        # 初筛结论来自扫描期入库，只作标记展示，绝不参与排序或过滤
        try: item["nsfw"]=STORE.nsfw_summary(item["root"],item["relpath"])
        except Exception: item["nsfw"]=None
    return items

class TextRequest(BaseModel):
    q:str
    neg:str=""
    roots:list[str]=Field(default_factory=list)
    after:str|None=None
    before:str|None=None
    k:StrictInt=Field(default=24,ge=1,le=200)
    review:bool=False
    videos:bool=True

    @field_validator("q","neg")
    @classmethod
    def bound_query_text(cls,value):
        if len(value)>2000 or len(value.split())>32: raise ValueError("查询最多 2000 字符、32 个短语")
        return value

@router.get("/api/media/roots")
def roots():
    _refresh_roots()
    return {"items":[{"id":r["id"],"type":r["type"],"label":r.get("label",r["id"]),"writable":bool(r.get("writable")),**_reachable[r["id"]]} for r in config.MEDIA_ROOTS]}

@router.get("/api/media/failures")
def failures(root:str|None=None,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
    if root:
        try: config.get_root(root)
        except KeyError: raise HTTPException(422,"unknown root")
    return {"items":STORE.failures(root,limit,offset),"limit":limit,"offset":offset}

def _version(root,relpath,version):
    row=STORE.get(root,relpath)
    if row is None: raise HTTPException(404,detail={"error":{"code":"not_found","message":"索引记录不存在"}})
    if row["content_version"]!=version: raise HTTPException(409,detail={"error":{"code":"source_changed","message":"图片版本已变化，请刷新搜索结果"}})
    return row

@router.get("/api/media/thumb")
def get_thumb(root:str,relpath:str,version:str,size:int=360):
    _version(root,relpath,version)
    try: data,cached=thumbs.thumbnail(root,relpath,version,size)
    except ValueError as e: raise HTTPException(422,detail={"error":{"code":"invalid_size","message":str(e)}})
    except FileNotFoundError: raise HTTPException(404,"media not found")
    except RuntimeError as e: raise HTTPException(409,detail={"error":{"code":"source_changed","message":str(e)}})
    except (OSError,TimeoutError) as e: raise HTTPException(503,detail={"error":{"code":"source_unavailable","message":str(e)}})
    tag='"'+version+'-'+hashlib.sha256(data).hexdigest()[:16]+'"'
    return Response(data,media_type="image/webp",headers={"ETag":tag,"X-Preview-Cache":"hit" if cached else "miss","X-Source-State":"cached" if cached else "online"})

@router.get("/api/media/file")
def get_file(root:str,relpath:str,version:str,display:int=0):
    _version(root,relpath,version)
    try:
        if display: data,mime=thumbs.display_image(root,relpath,version)
        else:
            before=media.stat_entry(root,relpath); row=STORE.get(root,relpath)
            if (before.size,before.mtime)!=(row["size"],row["mtime"]): raise RuntimeError("source_changed")
            data=media.read_bytes(root,relpath); after=media.stat_entry(root,relpath)
            if (before.size,before.mtime)!=(after.size,after.mtime) or (row["content_sha256"] and hashlib.sha256(data).hexdigest()!=row["content_sha256"]): raise RuntimeError("source_changed")
            import mimetypes
            mime=mimetypes.guess_type(relpath)[0] or "application/octet-stream"
    except FileNotFoundError: raise HTTPException(404,"media not found")
    except RuntimeError as e: raise HTTPException(409,detail={"error":{"code":"source_changed","message":str(e)}})
    except (OSError,TimeoutError) as e: raise HTTPException(503,detail={"error":{"code":"source_unavailable","message":str(e)}})
    return Response(data,media_type=mime,headers={"ETag":'"'+version+'"',"X-Source-State":"online"})

@router.get("/api/media/frame")
def get_frame(root:str,relpath:str,ts:float):
    """返回某个视频命中时间点的帧图；帧图本身就是 JPEG，直接当缩略图与大图用。"""
    try: config.get_root(root)
    except KeyError: raise HTTPException(422,"unknown root")
    record=STORE.video_record(root,relpath)
    if record is None: raise HTTPException(404,detail={"error":{"code":"not_found","message":"该视频还没有代表帧，请先扫描"}})
    path=frames.frame_file(root,relpath,ts)
    if not path.is_file(): raise HTTPException(404,detail={"error":{"code":"not_found","message":"帧文件缺失，请重新扫描该媒体根"}})
    return Response(path.read_bytes(),media_type="image/jpeg",headers={"ETag":'"'+record["content_sha256"][:16]+'"',"Cache-Control":"public, max-age=86400"})

@router.get("/api/media/video")
def get_video(root:str,relpath:str,version:str):
    """按抽帧时的内容摘要播放视频：摘要不符即拒绝，避免旧链接播到新内容。"""
    try: item=config.get_root(root)
    except KeyError: raise HTTPException(422,"unknown root")
    record=STORE.video_record(root,relpath)
    if record is None: raise HTTPException(404,detail={"error":{"code":"not_found","message":"该视频还没有代表帧"}})
    if record["content_sha256"]!=version: raise HTTPException(409,detail={"error":{"code":"source_changed","message":"视频内容已变化，请重新检索"}})
    if item.get("type")!="local":
        raise HTTPException(503,detail={"error":{"code":"source_unavailable","message":"远端媒体根暂不支持直接播放"}})
    from pathlib import Path
    from fastapi.responses import FileResponse
    base=Path(item["path"]).resolve(); path=(base/relpath).resolve()
    if not path.is_relative_to(base) or not path.is_file(): raise HTTPException(404,"media not found")
    import mimetypes
    return FileResponse(path,media_type=mimetypes.guess_type(relpath)[0] or "application/octet-stream")

def _checked_search(items,enabled,query_data=None):
    items=_decorate_items(items)
    qreview=None
    if enabled:
        if review.status()!="ready":
            try: review._load()
            except RuntimeError: raise HTTPException(503,detail={"error":{"code":"review_unavailable","message":"NSFW 模型不可用"}})
        if query_data is not None: qreview=review.review_bytes(query_data)
        for item in items:
            cached=item.get("nsfw")
            if cached and cached.get("status")=="ok" and cached.get("score") is not None:
                # 扫描期已经判过同一内容版本，直接用库里的结论，省一次推理
                item["review"]={"score":cached["score"],"label":cached["label"],"status":"ok","cached":True}
                continue
            try:
                row=STORE.get(item["root"],item["relpath"])
                before=media.stat_entry(item["root"],item["relpath"])
                data=media.read_bytes(item["root"],item["relpath"])
                after=media.stat_entry(item["root"],item["relpath"])
                valid=(row["content_version"]==item["version"] and (before.size,before.mtime)==(after.size,after.mtime)==(row["size"],row["mtime"]) and (not row["content_sha256"] or hashlib.sha256(data).hexdigest()==row["content_sha256"]))
                if not valid: item["review"]={"score":None,"label":None,"status":"failed"}
                else:
                    item["review"]=review.review_bytes(data)
                    if item["review"].get("status")=="ok" and row["content_sha256"]:
                        # 现算的结论回写，下次扫描/检索不必重复推理
                        try:
                            STORE.set_nsfw(item["root"],item["relpath"],row["content_sha256"],score=item["review"]["score"],label=item["review"]["label"],model_version=config.NSFW_MODEL_SOURCE)
                            item["nsfw"]=STORE.nsfw_summary(item["root"],item["relpath"])
                        except Exception: pass
            except Exception: item["review"]={"score":None,"label":None,"status":"failed"}
    return {"items":items,"query_review":qreview}

@router.post("/api/search/text")
def search_text(body:TextRequest):
    try:
        items,videos=search.text_query_with_videos(body.q,body.neg,body.roots,body.after,body.before,body.k,
                                                   videos_k=body.k if body.videos else 0)
    except ValueError as e: raise HTTPException(422,str(e))
    except RuntimeError as e: raise HTTPException(503,detail={"error":{"code":"model_unavailable","message":str(e)}})
    result=_checked_search(items,body.review)
    result["videos"]=videos
    return result

async def _fetch_url_image(url: str) -> bytes:
    """把图片网址下载成字节，供以图搜图使用。

    只放行 http/https，按声明长度与实收字节双重卡住 MAX_UPLOAD_BYTES，避免把远端
    超大文件整段拉进内存；下载失败按语义区分「地址不对」与「取不到」。
    """
    if not url or not url.strip():
        raise HTTPException(422, "empty url")
    if not re.match(r"^https?://", url, re.I):
        raise HTTPException(422, "url 必须是 http/https 地址")
    import httpx
    # trust_env=False：本机 all_proxy 是 socks5，而 httpx 未装 socksio 时构造期就抛
    # ImportError，整个网图检索直接 500；实测直连可出网，故图片下载不读环境代理。
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=True,
                                     timeout=config.URL_DOWNLOAD_TIMEOUT,
                                     headers={"User-Agent": config.UA}) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise HTTPException(502, f"image download failed: HTTP {resp.status_code}")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "image too large")
                chunks = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > config.MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "image too large")
                    chunks.append(chunk)
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(504, "image download timeout")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"image download failed: {e}")
    return b"".join(chunks)


@router.post("/api/search/image")
async def search_image(file:UploadFile|None=File(None),url:str|None=Form(None),k:int=Form(24),roots:list[str]=Form(default=[]),after:str|None=Form(None),before:str|None=Form(None),review_enabled:bool=Form(False,alias="review"),videos:bool=Form(True)):
    if file is not None:
        data=await file.read(config.MAX_UPLOAD_BYTES+1)
    else:
        data=await _fetch_url_image(url)
    if len(data)>config.MAX_UPLOAD_BYTES: raise HTTPException(413,"image too large")
    if not data: raise HTTPException(422,"empty image")
    try:
        items,video_hits=await run_in_threadpool(search.image_query_with_videos,data,roots,after,before,k,
                                                 k if videos else 0)
    except ValueError as e: raise HTTPException(422,str(e))
    except RuntimeError as e: raise HTTPException(503,detail={"error":{"code":"model_unavailable","message":str(e)}})
    result=await run_in_threadpool(_checked_search,items,review_enabled,data)
    result["videos"]=video_hits
    return result

@router.get("/api/search/stats")
def stats(): return STORE.stats()

@router.post("/api/media/scan", status_code=202)
def scan_post(payload: dict = Body(...)):
    root = payload.get("root")
    if not isinstance(root, str):
        raise HTTPException(422, "root 必须是媒体根 ID")
    try:
        config.get_root(root)
    except KeyError as exc:
        raise HTTPException(422, "未知媒体根") from exc
    rebuild = payload.get("rebuild", False)
    if not isinstance(rebuild, bool):
        raise HTTPException(422, "rebuild 必须是布尔值")
    try:
        item = JOBS.enqueue_scan(root, rebuild)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"id": item["id"], "status": item["status"]}


@router.get("/api/media/scans/{job_id}")
def scan_get(job_id: str):
    item = JOBS.get_job(job_id)
    if item is None or item.get("kind") != "scan":
        raise HTTPException(404, "扫描任务不存在")
    return item


@router.post("/api/media/scans/{job_id}/cancel", status_code=202)
def scan_cancel(job_id: str):
    item = JOBS.get_job(job_id)
    if item is None or item.get("kind") != "scan":
        raise HTTPException(404, "扫描任务不存在")
    return JOBS.cancel(job_id)


def _media_url(path, root, relpath, version, **extra):
    params={"root":root,"relpath":relpath,"version":version}
    params.update(extra)
    return path+"?"+urlencode(params)


def _snippet(body, terms, width=140):
    if not body:
        return None
    text=re.sub(r"\s+"," ",body).strip()
    if not text:
        return None
    lowered=text.lower()
    pos=-1
    for term in terms:
        if len(term)<2:
            continue
        found=lowered.find(term)
        if found>=0 and (pos<0 or found<pos):
            pos=found
    if pos<0:
        return text[:width]+("…" if len(text)>width else "")
    start=max(0,pos-width//3)
    end=min(len(text),start+width)
    return ("…" if start else "")+text[start:end]+("…" if end<len(text) else "")


def _cover(row):
    """封面的缩略图地址；封面图没被图片索引收录时返回 None，前端退回占位。"""
    cover=row.get("cover_relpath")
    if not cover:
        return None
    image=STORE.get(row["root"],cover)
    if image is None or image["state"]!="ready":
        return None
    return {"root":row["root"],"relpath":cover,"version":image["content_version"],
            # 缩略图缓存只实现 size=360；写别的尺寸会让卡片封面每次请求都 422
            "thumb":_media_url("/api/media/thumb",row["root"],cover,image["content_version"],size=360),
            "file":_media_url("/api/media/file",row["root"],cover,image["content_version"],display=1)}


def _content_out(row, terms):
    root=next((x for x in config.MEDIA_ROOTS if x["id"]==row["root"]),None)
    return {
        "content_id":row["content_id"],
        "root":row["root"],
        "relpath":row["relpath"],
        "platform":row["platform"],
        "platform_label":contents.PLATFORMS.get(row["platform"],row["platform"]),
        "kind":row["kind"],
        "title":row["title"],
        "account":row["account"],
        "url":row["url"],
        "published_at":row["published_at"],
        "digest":row["digest"],
        "snippet":_snippet(row.get("body"),terms),
        "media_count":row["media_count"],
        "cover":_cover(row),
        "indexed_at":row["indexed_at"],
        "source_label":(root or {}).get("label",row["root"]),
        # 稳定作品身份（平台+作品 ID）：NAS 归档台账与标签共享都按它对账
        "work_key":row.get("work_key") or row["content_id"],
    }


def _decorate_contents(items):
    """给内容条目挂上图片级 NSFW 聚合（flagged/total），没有结论时置 None。

    条目本身不带风险属性，聚合只用于卡片角标与前端筛选；完全不参与排序，
    同一查询的条目顺序不受模型是否跑过影响。
    """
    flags=STORE.content_nsfw_flags([item["content_id"] for item in items])
    for item in items:
        found=flags.get(item["content_id"])
        item["nsfw"]=None if found is None else {**found,"stage":"first"}
    return items


@router.get("/api/contents/search")
def contents_search(q:str="",platform:str|None=None,root:str|None=None,account:str|None=None,
                    limit:int=Query(24,ge=1,le=100),offset:int=Query(0,ge=0)):
    if len(q)>2000: raise HTTPException(422,"查询最多 2000 字符")
    if platform and platform not in contents.PLATFORMS: raise HTTPException(422,"未知平台")
    if root:
        try: config.get_root(root)
        except KeyError: raise HTTPException(422,"未知媒体根")
    rows,total,terms=STORE.search_contents(q,platform=platform,root=root,account=account,
                                           limit=limit,offset=offset)
    return {"items":_decorate_contents([_content_out(row,terms) for row in rows]),
            "total":total,"limit":limit,"offset":offset,"terms":terms}


@router.get("/api/contents/facets")
def contents_facets():
    data=STORE.content_facets()
    for item in data["platforms"]:
        item["label"]=contents.PLATFORMS.get(item["platform"],item["platform"])
    return data


@router.get("/api/contents/{content_id}")
def contents_detail(content_id:str):
    row=STORE.content_detail(content_id)
    if row is None: raise HTTPException(404,detail={"error":{"code":"not_found","message":"内容条目不存在"}})
    data=_content_out(row,contents.tokenize(row["title"]))
    data["body"]=row.get("body")
    data["images"]=[{
        "root":image["root"],"relpath":image["relpath"],
        "content_version":image["content_version"],"version":image["content_version"],
        "size":image["size"],"shot_at":image["shot_at"],
        "nsfw":STORE.nsfw_summary(image["root"],image["relpath"]),
        "thumb":_media_url("/api/media/thumb",image["root"],image["relpath"],image["content_version"],size=360),
        "file":_media_url("/api/media/file",image["root"],image["relpath"],image["content_version"],display=1),
    } for image in row["images"]]
    return _decorate_contents([data])[0]
