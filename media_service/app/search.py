"""基于当前代际只读矩阵的文搜图与以图搜图。"""
from __future__ import annotations
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
import numpy as np
from . import config, encoder
from .store import STORE

def _date_bounds(after=None,before=None):
    if after and before and date.fromisoformat(after)>date.fromisoformat(before): raise ValueError("after 不能晚于 before")
    tz=ZoneInfo("Asia/Shanghai")
    start=datetime.combine(date.fromisoformat(after),time.min,tz).astimezone(timezone.utc) if after else None
    end=datetime.combine(date.fromisoformat(before)+timedelta(days=1),time.min,tz).astimezone(timezone.utc) if before else None
    return start.isoformat() if start else None,end.isoformat() if end else None

def _check_roots(roots):
    roots = roots or []
    unknown=set(roots)-{r["id"] for r in config.MEDIA_ROOTS}
    if unknown: raise ValueError("未知 root: "+", ".join(sorted(unknown)))
    return roots

def text_vector(q, neg=""):
    """把查询文本编成一个单位向量；图搜与视频搜共用，避免同一句话编码两次。"""
    if not isinstance(q,str) or not q.split(): raise ValueError("q 必须包含至少一个正向短语")
    pos=encoder.encode_texts(q.split()).mean(axis=0)
    if neg and neg.split(): pos=pos-0.4*encoder.encode_texts(neg.split()).mean(axis=0)
    norm=float(np.linalg.norm(pos))
    if norm<1e-8: raise ValueError("查询向量接近零")
    return pos/norm

def text_query(q, neg="", roots=None, after=None, before=None, k=24):
    roots=_check_roots(roots)
    vec=text_vector(q,neg)
    _date_bounds(after,before)
    if STORE.stats()["total"] == 0: return []
    return _rank(vec,roots,after,before,k)

def image_query(data, roots=None, after=None, before=None, k=24):
    roots = _check_roots(roots)
    if isinstance(k,bool) or not isinstance(k,int) or not 1<=k<=200: raise ValueError("k 必须在 1..200")
    _date_bounds(after,before)
    vec=encoder.encode_image(data)
    if STORE.stats()["total"] == 0: return []
    return _rank(vec,roots,after,before,k)

def text_query_with_videos(q, neg="", roots=None, after=None, before=None, k=24, videos_k=0, per_video=3):
    """一次编码同时给出「图片命中」与「视频画面命中」，两套结果共享同一查询向量。"""
    roots=_check_roots(roots)
    vec=text_vector(q,neg)
    _date_bounds(after,before)
    items=_rank(vec,roots,after,before,k) if STORE.stats()["total"] else []
    videos=_rank_frames(vec,roots,after,before,videos_k,per_video) if videos_k>0 else []
    return items,videos

def image_query_with_videos(data, roots=None, after=None, before=None, k=24, videos_k=0, per_video=3):
    roots=_check_roots(roots)
    if isinstance(k,bool) or not isinstance(k,int) or not 1<=k<=200: raise ValueError("k 必须在 1..200")
    _date_bounds(after,before)
    vec=encoder.encode_image(data)
    items=_rank(vec,roots,after,before,k) if STORE.stats()["total"] else []
    videos=_rank_frames(vec,roots,after,before,videos_k,per_video) if videos_k>0 else []
    return items,videos

def _rank_frames(query, roots, after, before, k, per_video=3):
    """帧向量排序后按视频归并：每个视频只留最高分代表命中与若干帧，避免同一视频刷屏。

    日期过滤用抽帧时间（videos.sampled_at）：视频本身没有单独的入库时间，
    抽帧发生在入库当轮，用它作为「这条视频何时进入索引」的代理。
    """
    if isinstance(k,bool) or not isinstance(k,int) or not 1<=k<=200: raise ValueError("k 必须在 1..200")
    if isinstance(per_video,bool) or not isinstance(per_video,int) or not 1<=per_video<=12: raise ValueError("per_video 必须在 1..12")
    lo,hi=_date_bounds(after,before)
    records,matrix=STORE.frame_snapshot()
    if not records: return []
    mask=np.ones(len(records),dtype=bool)
    if roots: mask &= np.array([r["root"] in roots for r in records])
    if lo: mask &= np.array([r["sampled_at"]>=lo for r in records])
    if hi: mask &= np.array([r["sampled_at"]<hi for r in records])
    ix=np.flatnonzero(mask)
    if len(ix)==0: return []
    scores=matrix[ix]@np.asarray(query,dtype=np.float32)
    groups={}
    for j in np.argsort(-scores):
        row=records[ix[int(j)]]
        key=(row["root"],row["video_relpath"])
        group=groups.get(key)
        if group is None:
            root=next((x for x in config.MEDIA_ROOTS if x["id"]==row["root"]),None)
            group={"root":row["root"],"relpath":row["video_relpath"],"version":row["content_sha256"],
                   "score":float(scores[int(j)]),"duration":row["duration"],"frame_count":row["frame_count"],
                   "source_label":(root or {}).get("label",row["root"]),"hits":[]}
            groups[key]=group
        if len(group["hits"])<per_video:
            group["hits"].append({"ts":float(row["ts_seconds"]),"score":float(scores[int(j)]),
                                  "thumb":_frame_url(row["root"],row["video_relpath"],row["ts_seconds"])})
    out=sorted(groups.values(),key=lambda g:(-g["score"],g["root"],g["relpath"]))[:k]
    for group in out:
        group["hits"].sort(key=lambda h:h["ts"])
        group["file"]=_video_url(group["root"],group["relpath"],group["version"])
    return out

def _frame_url(root, relpath, ts):
    from urllib.parse import urlencode
    return "/api/media/frame?"+urlencode({"root":root,"relpath":relpath,"ts":f"{float(ts):g}"})

def _video_url(root, relpath, version):
    from urllib.parse import urlencode
    return "/api/media/video?"+urlencode({"root":root,"relpath":relpath,"version":version})

def _rank(query, roots, after, before, k):
    if isinstance(k,bool) or not isinstance(k,int) or not 1<=k<=200: raise ValueError("k 必须在 1..200")
    lo,hi=_date_bounds(after,before)
    generation,records,matrix=STORE.snapshot()
    if len(records)==0: return []
    mask=np.ones(len(records),dtype=bool)
    if roots: mask &= np.array([r["root"] in roots for r in records])
    if lo: mask &= np.array([r["ingested_at"]>=lo for r in records])
    if hi: mask &= np.array([r["ingested_at"]<hi for r in records])
    ix=np.flatnonzero(mask)
    if len(ix)==0:return []
    scores=matrix[ix]@np.asarray(query,dtype=np.float32)
    order=sorted(range(len(ix)),key=lambda j:(-float(scores[j]),records[ix[j]]["root"],records[ix[j]]["relpath"]))[:k]
    from urllib.parse import urlencode
    out=[]
    for j in order:
        r=records[ix[j]]; root=next((x for x in config.MEDIA_ROOTS if x["id"]==r["root"]),None)
        article=None
        if r["article_id"]:
            with STORE.lock: a=STORE.conn.execute("SELECT article_id,title,source_url,published_at FROM articles WHERE article_id=?",(r["article_id"],)).fetchone()
            article={"id":a["article_id"],"title":a["title"],"url":a["source_url"],"published_at":a["published_at"]} if a else None
        params=urlencode({"root":r["root"],"relpath":r["relpath"],"version":r["content_version"]})
        out.append({"root":r["root"],"relpath":r["relpath"],"version":r["content_version"],"score":float(scores[j]),"thumb":"/api/media/thumb?"+params+"&size=360","file":"/api/media/file?"+params+"&display=1","ingested_at":r["ingested_at"],"shot_at":r["shot_at"],"origin":r["origin"],"article":article,"source_label":(root or {}).get("label",r["root"]),"source_state":"unknown","preview_cached":False,"review":None})
    return out
