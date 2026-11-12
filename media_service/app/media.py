"""媒体源统一访问；远端列表异常不会被解释为空目录。"""
from __future__ import annotations
import json, os, subprocess, tempfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import config

IMAGE_EXTS={".jpg",".jpeg",".png",".webp",".gif",".bmp",".tif",".tiff",".heic",".heif"}
_IO_GATE=threading.BoundedSemaphore(2)
_heif_registered=False
def register_heif():
    global _heif_registered
    if not _heif_registered:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError: pass
        _heif_registered=True

def check_pixels(image):
    width,height=image.size
    if width<=0 or height<=0 or width*height>config.MAX_IMAGE_PIXELS:
        raise ValueError("图片像素数超过安全上限")

@dataclass(frozen=True)
class MediaEntry:
    root: str
    relpath: str
    size: int
    mtime: float

def validate_relpath(value: str) -> str:
    if not isinstance(value,str) or not value or "\x00" in value or "\\" in value or value.startswith("/"):
        raise ValueError("非法媒体相对路径")
    parts=value.split("/")
    if any(p in ("",".","..") for p in parts): raise ValueError("非法媒体相对路径")
    return value

def _cli():
    path=os.environ.get("WX_NAS_CLI") or os.path.expanduser("~/Library/Application Support/dsh-desktop/harness/skills/zspace-nas-agent-toolkit/scripts/zspace-cli")
    if not os.path.isfile(path): path="zspace-cli"
    return path

def _run(params, action, extra=()):
    with _IO_GATE:
        p=subprocess.run([_cli(),"file",action,"--params",json.dumps(params,ensure_ascii=False),*extra,"--mode","auto"],capture_output=True,text=True,timeout=config.NAS_TIMEOUT)
    try: out=json.loads(p.stdout)
    except Exception as e: raise OSError("zspace-cli 返回无效 JSON") from e
    if p.returncode or (isinstance(out,dict) and out.get("code") not in (None,0,"0","SUCCESS")):
        raise OSError(f"zspace-cli {action} failed")
    return out

def stat_entry(root_id, relpath):
    relpath=validate_relpath(relpath); root=config.get_root(root_id)
    if root["type"]=="local":
        base=Path(root["path"]).resolve(); target=(base/relpath).resolve()
        if not target.is_relative_to(base): raise ValueError("路径越过媒体根")
        st=target.stat(); return MediaEntry(root_id,relpath,st.st_size,st.st_mtime)
    path=root["path"].rstrip("/")+"/"+relpath
    parent,name=path.rsplit("/",1)
    start=0
    while True:
        resp=_run({"path":parent,"start":start,"num":100},"list")
        data=resp.get("data") if isinstance(resp,dict) else None
        items=data.get("list") if isinstance(data,dict) else None
        if not isinstance(items,list): raise OSError("zspace 列表缺少有效 data.list")
        total=int(data.get("total") or len(items))
        for item in items:
            if item.get("name")==name and str(item.get("is_dir"))!="1": return MediaEntry(root_id,relpath,int(item.get("size") or 0),float(item.get("modify_time") or 0))
        if not items and start<total: raise OSError("zspace stat 列举不完整")
        start+=len(items)
        if start>=total: break
    raise FileNotFoundError(relpath)

def check_root(root_id):
    root=config.get_root(root_id)
    if root["type"]=="local": return Path(root["path"]).is_dir()
    resp=_run({"path":root["path"],"start":0,"num":1},"list")
    data=resp.get("data") if isinstance(resp,dict) else None
    if not isinstance(data,dict) or not isinstance(data.get("list"),list): raise OSError("zspace root 状态响应不完整")
    return True

def _excluded(root, relpath):
    return any(relpath == prefix or relpath.startswith(prefix + "/")
               for prefix in root.get("exclude_paths", []))


def list_entries(root_id):
    """只列图片。内容条目索引需要全部文件时用 ``list_files``。"""
    return _list_files(root_id, IMAGE_EXTS)


def list_files(root_id):
    """列举媒体根内全部普通文件（不只图片），供内容条目发现使用。

    跳过规则与 ``list_entries`` 完全一致，减少两套遍历出现行为差异。
    """
    return _list_files(root_id, None)


def _list_files(root_id, suffixes):
    root=config.get_root(root_id)
    if root["type"]=="local":
        base=Path(root["path"]).resolve()
        if not base.is_dir(): raise OSError(f"媒体根不可达：{root_id}")
        out=[]
        def fail(error):
            raise error
        for directory, dirs, filenames in os.walk(base, onerror=fail, followlinks=False):
            dirs[:] = [name for name in dirs if name not in {"_rejected", "_manifests"}
                       and not name.startswith((".ai-media-", ".download-"))
                       and not _excluded(root, (Path(directory) / name).relative_to(base).as_posix())]
            for name in filenames:
                if name.startswith((".ai-media-", ".download-")):
                    continue
                p=Path(directory)/name
                if _excluded(root, p.relative_to(base).as_posix()):
                    continue
                if suffixes is not None and p.suffix.lower() not in suffixes:
                    continue
                resolved=p.resolve()
                if not resolved.is_relative_to(base):
                    continue
                st=resolved.stat()
                out.append(MediaEntry(root_id,p.relative_to(base).as_posix(),st.st_size,st.st_mtime))
        return sorted(out,key=lambda e:e.relpath),True
    stack=[root["path"]]; seen=set(); entries=[]
    while stack:
        path=stack.pop()
        if path in seen: continue
        seen.add(path); start=0
        while True:
            resp=_run({"path":path,"start":start,"num":100},"list")
            data=resp.get("data") if isinstance(resp,dict) else None
            if not isinstance(data,dict) or not isinstance(data.get("list"),list): raise OSError("zspace 列表不完整")
            items=data["list"]; total=int(data.get("total") or len(items))
            for item in items:
                name = item.get("name", "")
                rel = item["path"][len(root["path"].rstrip("/") + "/"):]
                if name in {"_rejected", "_manifests"} or name.startswith((".ai-media-", ".download-")) or _excluded(root, rel):
                    continue
                if str(item.get("is_dir"))=="1": stack.append(item["path"])
                else:
                    suffix=Path(name).suffix.lower()
                    if suffixes is not None and suffix not in suffixes:
                        continue
                    rel=item["path"][len(root["path"].rstrip("/")+"/"):]
                    validate_relpath(rel); entries.append(MediaEntry(root_id,rel,int(item.get("size") or 0),float(item.get("modify_time") or 0)))
            if not items and start<total: raise OSError("zspace 分页不完整")
            if not items: break
            start+=len(items)
            if start>=total: break
    return sorted(entries,key=lambda e:e.relpath),True

def read_bytes(root_id, relpath):
    relpath=validate_relpath(relpath); root=config.get_root(root_id)
    if root["type"]=="local":
        base=Path(root["path"]).resolve(); target=(base/relpath).resolve()
        if not target.is_relative_to(base): raise ValueError("路径越过媒体根")
        with _IO_GATE: return target.read_bytes()
    remote=root["path"].rstrip("/")+"/"+relpath
    with tempfile.TemporaryDirectory(prefix="ai-media-") as d:
        local=os.path.join(d,"download.bin")
        _run({"path":remote,"local_path":local,"overwrite":True},"download",["--local-root",d,"--yes"])
        return Path(local).read_bytes()

def write_new(root_id, relpath, local_file):
    root=config.get_root(root_id); relpath=validate_relpath(relpath)
    if not root.get("writable"): raise PermissionError("媒体根只读")
    base=Path(root["path"])
    if root["type"]=="local":
        resolved=base.resolve(); target=(resolved/relpath).resolve()
        if not target.is_relative_to(resolved): raise ValueError("路径越过媒体根")
        target.parent.mkdir(parents=True,exist_ok=True)
        import shutil
        fd, temp = tempfile.mkstemp(prefix=".ai-media-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as dst, open(local_file, "rb") as src:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            try:
                os.link(temp, target)
                return {"status": "uploaded", "target": relpath, "size": target.stat().st_size}
            except FileExistsError:
                return {"status": "skipped", "target": relpath, "size": target.stat().st_size}
        finally:
            os.unlink(temp)
    remote = str(base).rstrip("/") + "/" + relpath
    from . import nas
    from hashlib import sha256
    parent = str(base).rstrip("/")
    nas.ensure_dir(parent)
    for segment in relpath.split("/")[:-1]:
        parent += "/" + segment
        nas.ensure_dir(parent)
    try:
        status = nas.upload_file(Path(local_file), remote, local_root=Path(local_file).parent)
    except nas.NasError as exc:
        # 上传超时可能已经写入远端；仅在完整读回并核对一致时确认成功。
        try:
            actual = read_bytes(root_id, relpath)
        except Exception as probe_exc:
            raise OSError("远端上传结果未知，目标复核失败") from probe_exc
        if sha256(actual).digest() != sha256(Path(local_file).read_bytes()).digest():
            raise RuntimeError("target_conflict: 远端目标内容与本地候选不一致") from exc
        status = "skipped"
    return {"status": status, "target": relpath}
