"""增量扫描与候选式重建：只有完整成功的候选才发布。"""
from __future__ import annotations
import hashlib, threading, uuid
from datetime import datetime
from . import article_link, encoder, media
from .store import STORE, utcnow

_scan_lock=threading.Lock()

def _shot_at(relpath,data):
    from pathlib import Path
    import re
    m=re.search(r"(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)",Path(relpath).name)
    if m:
        try: return datetime.strptime("".join(m.groups()),"%Y%m%d").date().isoformat()
        except ValueError: pass
    try:
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(data)) as im:
            value=im.getexif().get(36867) or im.getexif().get(306)
        if value: return datetime.strptime(value,"%Y:%m:%d %H:%M:%S").isoformat()
    except Exception: pass
    return None

def _quarantine(root, entry, error):
    with STORE.lock:
        old=STORE.conn.execute("SELECT id FROM images WHERE root=? AND relpath=?",(root,entry.relpath)).fetchone()
        if old:
            STORE.conn.execute("UPDATE images SET size=?,mtime=?,emb=NULL,state='quarantined',last_error=? WHERE id=?",(entry.size,entry.mtime,str(error)[:500],old["id"]))
        else:
            STORE.conn.execute("INSERT INTO images(root,relpath,size,mtime,content_version,emb,ingested_at,indexed_at,origin,state,last_error) VALUES(?,?,?,?,?,NULL,NULL,NULL,'scan','quarantined',?)",(root,entry.relpath,entry.size,entry.mtime,str(uuid.uuid4()),str(error)[:500]))
        STORE.conn.commit(); STORE.reload_snapshot()

def _validate_image(data):
    from io import BytesIO
    from PIL import Image
    media.register_heif()
    with Image.open(BytesIO(data)) as im:
        media.check_pixels(im)
        im.load()

def _publish_rebuild(root, candidates, seen):
    """在一个 SQLite 事务内替换 root，并保留历史身份、关联及首次入库时间。"""
    now=utcnow()
    with STORE.lock:
        STORE.conn.execute("BEGIN IMMEDIATE")
        try:
            old_rows={r["relpath"]:dict(r) for r in STORE.conn.execute("SELECT * FROM images WHERE root=?",(root,))}
            for relpath,item in candidates.items():
                old=old_rows.get(relpath)
                if item.get("bad"):
                    if old:
                        STORE.conn.execute("UPDATE images SET size=?,mtime=?,emb=NULL,state='quarantined',last_error=? WHERE id=?",(item["size"],item["mtime"],item["error"],old["id"]))
                    else:
                        STORE.conn.execute("INSERT INTO images(root,relpath,size,mtime,content_version,emb,origin,state,last_error) VALUES(?,?,?,?,?,NULL,'scan','quarantined',?)",(root,relpath,item["size"],item["mtime"],str(uuid.uuid4()),item["error"]))
                    continue
                same=old and old["content_sha256"]==item["digest"] and old["state"]=="ready"
                version=old["content_version"] if same else (str(uuid.uuid4()) if not old or old["content_sha256"]!=item["digest"] else old["content_version"])
                ingested=(old["ingested_at"] if old else None) or now
                origin=(old["origin"] if old else "scan")
                article=item.get("article")
                article_id=STORE._upsert_article_tx(article, now) if article else (old["article_id"] if old else None)
                shot=item["shot_at"] or (old["shot_at"] if old else None)
                STORE.conn.execute("INSERT INTO images(root,relpath,size,mtime,content_sha256,content_version,emb,article_id,shot_at,ingested_at,indexed_at,origin,state,last_error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'ready',NULL) ON CONFLICT(root,relpath) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,content_sha256=excluded.content_sha256,content_version=excluded.content_version,emb=excluded.emb,article_id=excluded.article_id,shot_at=excluded.shot_at,ingested_at=COALESCE(images.ingested_at,excluded.ingested_at),indexed_at=excluded.indexed_at,origin=excluded.origin,state='ready',last_error=NULL",(root,relpath,item["size"],item["mtime"],item["digest"],version,item["embedding"].tobytes(),article_id,shot,ingested,now,origin))
            # 未出现的既有记录变成墓碑，行身份和来源数据原样保留。
            for relpath,old in old_rows.items():
                if relpath not in seen and old["state"]!="deleted":
                    STORE.conn.execute("UPDATE images SET state='deleted',emb=NULL WHERE id=?",(old["id"],))
            STORE.conn.execute("UPDATE index_meta SET generation=generation+1 WHERE id=1")
            STORE.conn.commit(); STORE.reload_snapshot()
        except Exception:
            STORE.conn.rollback(); raise

def scan_root(root, rebuild=False, progress=None, cancel=None):
    counts={"scanned":0,"indexed_new":0,"indexed_updated":0,"indexed_unchanged":0,"deleted":0,"failed":0,"errors":[],"complete":False,"status":"running"}
    if not _scan_lock.acquire(blocking=False): raise RuntimeError("scan_busy")
    try:
        if cancel and cancel(): counts["status"]="cancelled"; return counts
        entries,complete=media.list_entries(root)
        if not complete: raise OSError("来源目录列举不完整")
        if cancel and cancel(): counts["status"]="cancelled"; return counts
        seen={e.relpath for e in entries}; candidates={}; systematic=False
        article_cache={}  # 每轮扫描按文章目录缓存侧车元数据
        for entry in entries:
            if cancel and cancel(): counts["status"]="cancelled"; return counts
            counts["scanned"]+=1
            try:
                old=STORE.get(root,entry.relpath)
                vector=None
                if not rebuild and old and old["state"]=="ready" and old["size"]==entry.size and old["mtime"]==entry.mtime:
                    counts["indexed_unchanged"]+=1
                else:
                    before=media.stat_entry(root,entry.relpath)
                    if (before.size,before.mtime)!=(entry.size,entry.mtime):
                        raise OSError("来源文件在列举后发生变化")
                    data=media.read_bytes(root,entry.relpath)
                    after=media.stat_entry(root,entry.relpath)
                    if len(data)!=entry.size or (after.size,after.mtime)!=(before.size,before.mtime):
                        raise OSError("来源文件读取过程中发生变化")
                    digest=hashlib.sha256(data).hexdigest()
                    decode_failed=False
                    try: _validate_image(data)
                    except Exception as decode_error:
                        decode_failed=True
                        counts["failed"]+=1
                        if rebuild: candidates[entry.relpath]={"size":entry.size,"mtime":entry.mtime,"bad":True,"error":str(decode_error)[:500]}
                        else: _quarantine(root,entry,decode_error)
                    if not decode_failed:
                        vector=encoder.encode_image(data)
                        article=article_link.load_article(root,entry.relpath,article_cache)
                        if rebuild:
                            candidates[entry.relpath]={"size":entry.size,"mtime":entry.mtime,"digest":digest,"embedding":vector,"shot_at":_shot_at(entry.relpath,data),"article":article}
                            if old and old["state"]=="ready" and old["content_sha256"]==digest: counts["indexed_unchanged"]+=1
                            elif old: counts["indexed_updated"]+=1
                            else: counts["indexed_new"]+=1
                        else:
                            result=STORE.add_image(root,entry.relpath,data,origin="scan",size=entry.size,mtime=entry.mtime,embedding=vector,shot_at=_shot_at(entry.relpath,data),article=article)
                            counts["indexed_"+result["status"]]+=1
            except (OSError,TimeoutError) as exc:
                systematic=True; counts["failed"]+=1; counts["errors"].append({"path":entry.relpath,"error":str(exc)[:300]})
            except (ValueError, SyntaxError) as exc:
                # 解码已由 _validate_image 单独完成；这里的 ValueError 来自 encoder，属系统故障。
                systematic=True; counts["failed"]+=1; counts["errors"].append({"path":entry.relpath,"error":str(exc)[:300]})
            except Exception as exc:
                # 解码异常归为单图坏图；模型/系统故障属于本轮失败，避免发布不完整候选。
                systematic=True; counts["failed"]+=1; counts["errors"].append({"path":entry.relpath,"error":str(exc)[:300]})
            if progress: progress(dict(counts,total=len(entries),current=entry.relpath))
            if cancel and cancel(): counts["status"]="cancelled"; return counts
        if systematic:
            counts["status"]="partial"; return counts
        if cancel and cancel(): counts["status"]="cancelled"; return counts
        if rebuild:
            previous={r["relpath"]:r["state"] for r in STORE.conn.execute("SELECT relpath,state FROM images WHERE root=?",(root,))}
            _publish_rebuild(root,candidates,seen)
            counts["deleted"]=sum(1 for path,state in previous.items() if state!="deleted" and path not in seen)
        else:
            with STORE.lock:
                STORE.conn.execute("BEGIN IMMEDIATE")
                try:
                    rows=STORE.conn.execute("SELECT id,relpath FROM images WHERE root=? AND state!='deleted'",(root,)).fetchall()
                    for row in rows:
                        if row["relpath"] not in seen:
                            # 单参数也要写成元组，裸 int 会让 sqlite3 报 parameters are of unsupported type
                            STORE.conn.execute("UPDATE images SET state='deleted',emb=NULL WHERE id=?",(row["id"],))
                            counts["deleted"]+=1
                    STORE.conn.execute("UPDATE index_meta SET generation=generation+1 WHERE id=1")
                    STORE.conn.commit(); STORE.reload_snapshot()
                except Exception:
                    # 不回滚会把连接留在打开的事务里，后续每次 BEGIN IMMEDIATE 都报
                    # cannot start a transaction within a transaction。
                    STORE.conn.rollback(); raise
        counts["complete"]=True; counts["status"]="partial" if counts["failed"] else "completed"
        return counts
    finally: _scan_lock.release()
