"""内容版本绑定的缩略图缓存。"""
from __future__ import annotations
import hashlib,json,os,tempfile
from pathlib import Path
from io import BytesIO
from PIL import Image
from . import config,media

def _enforce_cache_limit(pinned=None):
    directory=Path(config.THUMB_DIR)
    files=[p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".thumb-")]
    total=sum(p.stat().st_size for p in files)
    for item in sorted(files,key=lambda p:p.stat().st_atime_ns):
        if total<=config.MAX_CACHE_BYTES: break
        if pinned is not None and item==pinned: continue
        size=item.stat().st_size
        try: item.unlink(); total-=size
        except FileNotFoundError: pass

def cache_path(root,relpath,version,size=360,fmt="webp"):
    raw=json.dumps([root,relpath,version,size,fmt],ensure_ascii=False,separators=(",",":")).encode()
    key=hashlib.sha256(raw).hexdigest()
    return Path(config.THUMB_DIR)/(key+"."+fmt)

def thumbnail(root,relpath,version,size=360):
    if size!=360: raise ValueError("仅支持 size=360")
    from .store import STORE
    row=STORE.get(root,media.validate_relpath(relpath))
    if not row or row["state"]!="ready": raise FileNotFoundError(relpath)
    if row["content_version"]!=version: raise RuntimeError("source_changed")
    path=cache_path(root,relpath,version,size)
    if path.is_file():
        data=path.read_bytes()
        try: os.utime(path,ns=(os.stat(path).st_atime_ns,os.stat(path).st_mtime_ns))
        except OSError: pass
        return data,True
    before=media.stat_entry(root,relpath)
    if before.size!=row["size"] or before.mtime!=row["mtime"]: raise RuntimeError("source_changed")
    data=media.read_bytes(root,relpath)
    after=media.stat_entry(root,relpath)
    digest=hashlib.sha256(data).hexdigest()
    if (before.size,before.mtime)!=(after.size,after.mtime) or (row["content_sha256"] and digest!=row["content_sha256"]): raise RuntimeError("source_changed")
    if not row["content_sha256"]:
        with STORE.lock:
            STORE.conn.execute("UPDATE images SET content_sha256=? WHERE id=?",(digest,row["id"]))
            STORE.conn.commit()
    media.register_heif()
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(BytesIO(data)) as im:
        media.check_pixels(im)
        im.thumbnail((size,size))
        output = BytesIO()
        im.convert("RGB").save(output,"WEBP",quality=85)
    fd, temp = tempfile.mkstemp(prefix=".thumb-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(output.getvalue())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        _enforce_cache_limit(path)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return output.getvalue(),False

def display_image(root,relpath,version):
    from .store import STORE
    row=STORE.get(root,media.validate_relpath(relpath))
    if not row or row["state"]!="ready": raise FileNotFoundError(relpath)
    if row["content_version"]!=version: raise RuntimeError("source_changed")
    before=media.stat_entry(root,relpath)
    if (before.size,before.mtime)!=(row["size"],row["mtime"]): raise RuntimeError("source_changed")
    data=media.read_bytes(root,relpath); after=media.stat_entry(root,relpath)
    if (before.size,before.mtime)!=(after.size,after.mtime) or (row["content_sha256"] and hashlib.sha256(data).hexdigest()!=row["content_sha256"]): raise RuntimeError("source_changed")
    media.register_heif()
    with Image.open(BytesIO(data)) as im:
        if im.format and im.format.upper() in ("HEIC","HEIF"): 
            out=BytesIO(); im.convert("RGB").save(out,"JPEG",quality=92); return out.getvalue(),"image/jpeg"
    return data,Image.MIME.get(Image.open(BytesIO(data)).format,"application/octet-stream")
