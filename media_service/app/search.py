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

def text_query(q, neg="", roots=None, after=None, before=None, k=24):
    if not isinstance(q,str) or not q.split(): raise ValueError("q 必须包含至少一个正向短语")
    roots=roots or []
    unknown=set(roots)-{r["id"] for r in config.MEDIA_ROOTS}
    if unknown: raise ValueError("未知 root: "+", ".join(sorted(unknown)))
    _date_bounds(after,before)
    if STORE.stats()["total"] == 0: return []
    pos=encoder.encode_texts(q.split()).mean(axis=0)
    if neg and neg.split(): pos=pos-0.4*encoder.encode_texts(neg.split()).mean(axis=0)
    norm=float(np.linalg.norm(pos))
    if norm<1e-8: raise ValueError("查询向量接近零")
    return _rank(pos/norm,roots,after,before,k)

def image_query(data, roots=None, after=None, before=None, k=24):
    roots = roots or []
    unknown=set(roots)-{r["id"] for r in config.MEDIA_ROOTS}
    if unknown: raise ValueError("未知 root: "+", ".join(sorted(unknown)))
    if isinstance(k,bool) or not isinstance(k,int) or not 1<=k<=200: raise ValueError("k 必须在 1..200")
    _date_bounds(after,before)
    if STORE.stats()["total"] == 0: return []
    return _rank(encoder.encode_image(data),roots,after,before,k)

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
