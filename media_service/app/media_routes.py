"""图片与检索 HTTP 路由。"""
from __future__ import annotations
import hashlib
import threading
from datetime import datetime,timezone
from fastapi import APIRouter,HTTPException,Query,UploadFile,File,Form,Body
from fastapi.responses import Response
from pydantic import BaseModel,Field,StrictInt,field_validator
from starlette.concurrency import run_in_threadpool
from . import config,media,encoder,review,search,thumbs
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
    return items

class TextRequest(BaseModel):
    q:str
    neg:str=""
    roots:list[str]=Field(default_factory=list)
    after:str|None=None
    before:str|None=None
    k:StrictInt=Field(default=24,ge=1,le=200)
    review:bool=False

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

def _checked_search(items,enabled,query_data=None):
    items=_decorate_items(items)
    qreview=None
    if enabled:
        if review.status()!="ready":
            try: review._load()
            except RuntimeError: raise HTTPException(503,detail={"error":{"code":"review_unavailable","message":"NSFW 模型不可用"}})
        if query_data is not None: qreview=review.review_bytes(query_data)
        for item in items:
            try:
                row=STORE.get(item["root"],item["relpath"])
                before=media.stat_entry(item["root"],item["relpath"])
                data=media.read_bytes(item["root"],item["relpath"])
                after=media.stat_entry(item["root"],item["relpath"])
                valid=(row["content_version"]==item["version"] and (before.size,before.mtime)==(after.size,after.mtime)==(row["size"],row["mtime"]) and (not row["content_sha256"] or hashlib.sha256(data).hexdigest()==row["content_sha256"]))
                if not valid: item["review"]={"score":None,"label":None,"status":"failed"}
                else: item["review"]=review.review_bytes(data)
            except Exception: item["review"]={"score":None,"label":None,"status":"failed"}
    return {"items":items,"query_review":qreview}

@router.post("/api/search/text")
def search_text(body:TextRequest):
    try: items=search.text_query(body.q,body.neg,body.roots,body.after,body.before,body.k)
    except ValueError as e: raise HTTPException(422,str(e))
    except RuntimeError as e: raise HTTPException(503,detail={"error":{"code":"model_unavailable","message":str(e)}})
    return _checked_search(items,body.review)

@router.post("/api/search/image")
async def search_image(file:UploadFile=File(...),k:int=Form(24),roots:list[str]=Form(default=[]),after:str|None=Form(None),before:str|None=Form(None),review_enabled:bool=Form(False,alias="review")):
    data=await file.read(config.MAX_UPLOAD_BYTES+1)
    if len(data)>config.MAX_UPLOAD_BYTES: raise HTTPException(413,"image too large")
    if not data: raise HTTPException(422,"empty image")
    try: items=await run_in_threadpool(search.image_query,data,roots,after,before,k)
    except ValueError as e: raise HTTPException(422,str(e))
    except RuntimeError as e: raise HTTPException(503,detail={"error":{"code":"model_unavailable","message":str(e)}})
    return await run_in_threadpool(_checked_search,items,review_enabled,data)

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
