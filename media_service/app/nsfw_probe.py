"""一次性 NSFW 检测：任意目录 / 单个文件，不写索引、不改覆盖率。

与 ``nsfw_scan``（对已入库图片补结论）的区别：

- 输入不是媒体根里的 root/relpath，而是用户当场指定的绝对路径或一次上传；
- 结论只落在任务作用域的 ``results.json`` 里，不写 ``nsfw_results`` 表——这些文件
  还没入库，写表会让覆盖率与待复核队列凭空多出条目；
- 需要给前端看图，所以缩略图写在任务目录里，经 job 作用域端点提供。**不提供按任意
  路径读原图的端点**：否则等于把只读文件通道开给了整个文件系统。

路径读取的约束（这是唯一一处不经过媒体根校验的磁盘读取）：

- 只接受绝对路径（允许 ``~`` 展开），解析后必须是已存在的普通文件或目录；
- 递归遍历不跟随符号链接，符号链接的文件/目录一律跳过，避免扫描范围被链接带出用户
  指定目录；
- 图片整文件读入内存前先按大小掐断；视频交给 ffmpeg 流式读，不整段进内存；
- 有文件数硬上限 ``MAX_PROBE_FILES``，预检阶段就会提前告诉用户预计规模。

视频结论取「最严重帧」：单帧分数最高者代表整段视频，同时保留逐帧分数供前端画时间
条——只给一个总分会让用户无法判断到底是哪一段被误判。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from . import config, frames, media, review

PROBE_DIR = Path(config.DATA_DIR) / "nsfw-probe"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif",
              ".avif", ".tif", ".tiff"}
VIDEO_EXTS = set(frames.VIDEO_EXTS)
DEFAULT_MAX_FILES = 5000
MAX_PROBE_FILES = 20000
MAX_IMAGE_BYTES = 60 * 1024 * 1024
THUMB_MAX = 320
RETENTION_SECONDS = 7 * 24 * 60 * 60
_MAX_ERRORS = 50

_items_lock = threading.Lock()
_items_cache: dict[str, tuple[float, list[dict]]] = {}


def classify(path: Path | str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTS:
        return "image"
    if suffix in VIDEO_EXTS:
        return "video"
    return None


def normalize_path(raw: str) -> Path:
    """把用户输入变成绝对路径；不要求存在（预检负责报不存在）。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("路径不能为空")
    text = os.path.expandvars(raw.strip())
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("请填写绝对路径（例如 /Users/you/Pictures）")
    return path


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _walk(base: Path, recursive: bool, limit: int):
    """按文件数与类型枚举候选文件，最多产 ``limit`` 个。

    预检与扫描共用同一套枚举口径；被上限截断的状态由调用方按「产出数达到上限」判断，
    生成器不再额外返回标志。
    """
    seen = 0
    if base.is_file():
        kind = classify(base)
        if kind:
            yield base, kind
        return
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        # 不跟随指向目录的符号链接：os.walk 会把它们列进 dirnames，但不会进入；
        # 显式剔除可以让「不扫描链接目标」这件事在代码上可读。
        dirnames[:] = [name for name in dirnames if not _is_symlink(Path(dirpath) / name)]
        if not recursive:
            dirnames[:] = []
        for name in filenames:
            path = Path(dirpath) / name
            if _is_symlink(path):
                continue
            kind = classify(path)
            if not kind:
                continue
            if seen >= limit:
                return
            seen += 1
            yield path, kind
    return


def precheck(raw_path: str, recursive: bool = True, max_files: int = DEFAULT_MAX_FILES) -> dict:
    """扫描前的只读预检：规模、类型、可读性。不加载模型、不读文件内容。"""
    path = normalize_path(raw_path)
    limit = max(1, min(int(max_files), MAX_PROBE_FILES))
    result = {
        "path": str(path), "exists": path.exists(), "is_dir": path.is_dir(),
        "is_file": path.is_file(), "readable": False, "recursive": bool(recursive),
        "images": 0, "videos": 0, "total": 0, "truncated": False,
        "sample": [], "max_files": limit, "error": None,
    }
    if not result["exists"]:
        result["error"] = "路径不存在"
        return result
    try:
        result["readable"] = os.access(path, os.R_OK)
    except OSError:
        result["readable"] = False
    if not result["readable"]:
        result["error"] = "没有读取权限"
        return result
    if not (result["is_dir"] or result["is_file"]):
        result["error"] = "既不是普通文件也不是目录"
        return result
    for item, kind in _walk(path, recursive, limit):
        result["total"] += 1
        result[f"{kind}s"] += 1
        if len(result["sample"]) < 12:
            try:
                result["sample"].append(str(item.relative_to(path)) if result["is_dir"] else str(item))
            except ValueError:
                result["sample"].append(str(item))
    if result["total"] >= limit:
        # 到上限只说明「至少这么多」，前端据此提示分批扫描。
        result["truncated"] = True
    return result


def _job_dir(job_id: str) -> Path:
    directory = PROBE_DIR / job_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _thumbs_dir(job: dict) -> Path:
    directory = _job_dir(job["id"]) / "thumbs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _score(data: bytes, provider: str) -> tuple[dict | None, str | None]:
    try:
        result = review.score_image(data, provider)
    except Exception as exc:  # 单张失败只记账
        return None, str(exc)[:200]
    if not result:
        return None, "模型未就绪或检测已关闭"
    return result, None


def _thumb(image_bytes: bytes, dest: Path) -> bool:
    """把任意可解码图片压成 JPEG 缩略图；解码失败返回 False。"""
    try:
        media.register_heif()
        with Image.open(BytesIO(image_bytes)) as im:
            media.check_pixels(im)
            im.thumbnail((THUMB_MAX, THUMB_MAX))
            output = BytesIO()
            im.convert("RGB").save(output, "JPEG", quality=82)
        _write_atomic(dest, output.getvalue())
        return True
    except Exception:
        return False


def _scan_image(path: Path, item: dict, job: dict, provider: str) -> None:
    size = item["size"]
    if size > MAX_IMAGE_BYTES:
        item.update(status="error", error=f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MiB")
        return
    try:
        data = path.read_bytes()
    except OSError as exc:
        item.update(status="error", error=f"读取失败：{exc}"[:200])
        return
    item["digest"] = hashlib.sha256(data).hexdigest()
    try:  # 先确认能解码：损坏的图片交给模型打分只会得到一个没有意义的分数
        media.register_heif()
        with Image.open(BytesIO(data)) as probe:
            probe.verify()
    except Exception:
        item.update(status="error", error="无法解码图片")
        return
    result, error = _score(data, provider)
    if result is None:
        item.update(status="skipped", error=error)
        return
    item.update(status="ok", score=result.get("score"), label=result.get("label"))
    thumb_name = f"{item['index']}.jpg"
    if _thumb(data, _thumbs_dir(job) / thumb_name):
        item["thumb"] = f"thumbs/{thumb_name}"


def _scan_video(path: Path, item: dict, job: dict, provider: str, interval: float) -> None:
    if not frames.available():
        item.update(status="error", error="ffmpeg 不可用，无法抽帧")
        return
    duration = frames.probe_duration(path)
    try:
        samples = frames.extract_frames(path, duration, interval, config.FRAME_MAX)
    except Exception as exc:
        item.update(status="error", error=str(exc)[:200])
        return
    if not samples:
        item.update(status="error", error="未能抽出任何帧")
        return
    scores: list[dict] = []
    for ts, frame in samples:
        result, _error = _score(frame, provider)
        if result is None:
            continue
        scores.append({"ts": round(ts, 1), "score": result.get("score"),
                       "label": result.get("label"), "_bytes": frame})
    if not scores:
        item.update(status="skipped", error="所有抽帧都没有结论（模型未就绪）")
        return
    top = max(scores, key=lambda row: row.get("score") or 0)
    thumbs = _thumbs_dir(job)
    hits = []
    for rank, hit in enumerate(sorted(scores, key=lambda row: row.get("score") or 0, reverse=True)[:3]):
        name = f"{item['index']}_{rank}.jpg"
        if _thumb(hit["_bytes"], thumbs / name):
            hits.append({"ts": hit["ts"], "score": hit["score"], "label": hit["label"],
                         "thumb": f"thumbs/{name}"})
    item.update(
        status="ok",
        score=top.get("score"),
        label=top.get("label"),
        video={
            "duration": duration,
            "frames": len(samples),
            "scored": len(scores),
            "interval": interval,
            # 逐帧分数供前端画时间条；只留标量，帧字节不能进结果文件
            "frame_scores": [{k: v for k, v in row.items() if k != "_bytes"} for row in scores],
            "hits": hits,
        },
    )
    if hits:
        item["thumb"] = hits[0]["thumb"]


def scan(job: dict, progress=None, cancel=None) -> dict:
    """执行一次 probe 任务；结果写 ``<job dir>/results.json``，返回值只带计数。"""
    job_id = job["id"]
    directory = _job_dir(job_id)
    thumbs = directory / "thumbs"
    thumbs.mkdir(exist_ok=True)
    results: list[dict] = []
    counts = {"total": 0, "scanned": 0, "images": 0, "videos": 0,
              "flagged": 0, "skipped": 0, "failed": 0, "imported": 0}
    errors: list[str] = []
    truncated = False
    status = "completed"

    if job.get("source") == "upload":
        source_path = Path(job.get("source_path") or "")
        # 上传暂存在 staging；任务真正开始时搬进任务目录，保留给「入库」读取，
        # 也避免 staging 的按天清理在任务结束后把原件删掉。
        if source_path.is_file() and source_path.parent != directory:
            kept = directory / "source" / source_path.name
            kept.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(source_path, kept)
                shutil.rmtree(source_path.parent, ignore_errors=True)
                source_path = kept
                job["source_path"] = str(kept)
            except OSError:
                pass  # 搬不动就原地扫，入库失败时会给「源文件已不存在」
        candidates = [(source_path, classify(source_path) or "image")] if source_path.is_file() else []
    else:
        source_path = normalize_path(job.get("path") or "")
        candidates = list(_walk(source_path, job.get("recursive", True),
                                int(job.get("max_files") or DEFAULT_MAX_FILES)))

    kinds = set(job.get("kinds") or ["image", "video"])
    selected = [(path, kind) for path, kind in candidates if kind in kinds]
    counts["total"] = len(selected)
    if progress:
        progress({"total": counts["total"], "scanned": 0, "images": 0, "videos": 0,
                  "flagged": 0, "skipped": 0, "failed": 0, "complete": False,
                  "phase": "扫描中"})

    provider = review.current_provider()
    interval = float(job.get("video_interval") or 5)
    try:
        for index, (path, kind) in enumerate(selected):
            if cancel and cancel():
                status = "cancelled"
                break
            try:
                stat = path.stat()
                item = {"index": index, "path": str(path), "name": path.name, "kind": kind,
                        "size": stat.st_size, "mtime": stat.st_mtime,
                        "status": "pending", "score": None, "label": None, "error": None}
                if kind == "image":
                    _scan_image(path, item, job, provider)
                    counts["images"] += 1
                else:
                    _scan_video(path, item, job, provider, interval)
                    counts["videos"] += 1
                if item["status"] == "ok":
                    counts["scanned"] += 1
                    if item.get("label") == "nsfw":
                        counts["flagged"] += 1
                elif item["status"] == "skipped":
                    counts["skipped"] += 1
                else:
                    counts["failed"] += 1
                result = {k: v for k, v in item.items() if k != "index"}
                result["index"] = index
                results.append(result)
                if item["status"] == "error" and len(errors) < _MAX_ERRORS:
                    errors.append(f"{path.name}: {item.get('error')}")
            except Exception as exc:  # 单文件异常不该中断整批
                counts["failed"] += 1
                if len(errors) < _MAX_ERRORS:
                    errors.append(f"{path.name}: {str(exc)[:150]}")
            if progress and ((index + 1) % 5 == 0 or index + 1 == counts["total"]):
                progress({**counts, "complete": False, "phase": "扫描中",
                          "current": path.name})
    finally:
        _write_results(job_id, results)

    if status != "cancelled" and counts["failed"]:
        status = "partial"
    payload = {**counts, "errors": errors, "complete": True, "status": status,
               "phase": None, "current": None, "truncated": truncated or counts["total"] >= int(job.get("max_files") or DEFAULT_MAX_FILES)}
    if progress:
        progress(payload)
    return payload


def _results_path(job_id: str) -> Path:
    return _job_dir(job_id) / "results.json"


def _write_results(job_id: str, results: list[dict]) -> None:
    path = _results_path(job_id)
    tmp = path.with_name("results.json.tmp")
    tmp.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_results(job_id: str) -> list[dict]:
    """读任务结果；按 mtime 做进程内缓存，轮询分页不用反复解析同一份 JSON。"""
    path = _results_path(job_id)
    if not path.is_file():
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    with _items_lock:
        cached = _items_cache.get(job_id)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    with _items_lock:
        _items_cache[job_id] = (mtime, data)
    return data


def items(job_id: str, offset: int = 0, limit: int = 50, flagged_only: bool = False) -> dict:
    rows = load_results(job_id)
    if flagged_only:
        rows = [row for row in rows if row.get("label") == "nsfw"]
    total = len(rows)
    page = rows[max(0, offset):max(0, offset) + max(1, min(limit, 200))]
    return {"items": page, "total": total, "offset": offset, "limit": limit}


def thumb_path(job_id: str, index: int, hit: int | None = None) -> Path | None:
    """只允许取结果里登记过的缩略图：路径由服务端拼，不接受前端传文件名。

    ``hit`` 取视频命中帧（按分数倒序）的第 N 个缩略图；图片与视频封面传 None。
    """
    for row in load_results(job_id):
        if row.get("index") != index:
            continue
        name = None
        if hit is None:
            name = row.get("thumb")
        else:
            hits = (row.get("video") or {}).get("hits") or []
            if 0 <= hit < len(hits):
                name = hits[hit].get("thumb")
        if not isinstance(name, str) or not name.startswith("thumbs/"):
            return None
        path = _job_dir(job_id) / name
        return path if path.is_file() else None
    return None


# ── 入库 ──────────────────────────────────────────────────

def safe_filename(name: str, fallback: str = "file") -> str:
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", stem).strip(" .")[:120]
    suffix = re.sub(r"[^0-9A-Za-z.]", "", suffix)[:12]
    if not stem:
        stem = fallback
    return f"{stem}.{suffix}" if suffix else stem


def safe_dir(value: str) -> str:
    """目标目录：只允许媒体根内的相对子目录。

    绝对路径直接拒绝而不是「去掉前导斜杠再当相对」——用户写 ``/tmp/x`` 时大概率
    是想指定机器上的目录，静默改成根内 ``tmp/x`` 会把文件放到他没预期的位置。
    """
    text = (value or "").strip().replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ValueError("目标目录必须是媒体根内的相对路径")
    text = text.strip("/")
    if not text:
        return ""
    parts = text.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise ValueError("目标目录不能包含 . 或 .. 段")
    if len(text) > 200:
        raise ValueError("目标目录过长")
    return "/".join(parts)


def _unique_relpath(root_id: str, directory: str, filename: str) -> str:
    base = directory + "/" if directory else ""
    candidate = base + filename
    stem, dot, suffix = filename.rpartition(".")
    if not dot:
        stem, suffix = filename, ""
    for attempt in range(1, 50):
        try:
            media.stat_entry(root_id, candidate)
        except (FileNotFoundError, OSError):
            return candidate
        candidate = f"{base}{stem} ({attempt + 1}){'.' + suffix if suffix else ''}"
    raise ValueError("同名文件过多，请换个目标目录")


def import_item(job: dict, index: int, root_id: str, target_dir: str = "") -> dict:
    """把一次 probe 结果复制进媒体根，并提交一次增量扫描。

    复制后不自己写索引，而是入队 ``scan`` 任务：索引路径（摘要、embedding、扫描期
    初筛）只有 ``scan_root`` 一份实现，另写一条单文件索引路径等于把两套口径养起来。
    """
    from .jobs import JOBS

    rows = load_results(job["id"])
    row = next((item for item in rows if item.get("index") == index), None)
    if row is None:
        raise KeyError("检测结果不存在")
    if row.get("status") not in {"ok", "skipped"}:
        raise ValueError("该文件没有可用结果，不能入库")
    source = Path(row["path"])
    if not source.is_file():
        raise ValueError("源文件已不存在")
    stat = source.stat()
    if stat.st_size != row.get("size") or abs(stat.st_mtime - float(row.get("mtime") or 0)) > 1e-6:
        raise ValueError("源文件已变化，请重新检测后再入库")
    if row.get("kind") == "image" and row.get("digest"):
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != row["digest"]:
            raise ValueError("源文件内容已变化，请重新检测后再入库")

    root = config.get_root(root_id)
    if not root.get("writable"):
        raise PermissionError("该媒体根只读")
    directory = safe_dir(target_dir)
    relpath = _unique_relpath(root_id, directory, safe_filename(row.get("name") or source.name))
    written = media.write_new(root_id, relpath, str(source))
    if written.get("status") == "skipped":
        raise FileExistsError("目标文件已存在，请换个目标目录")

    row["imported"] = {"root": root_id, "relpath": relpath}
    _write_results(job["id"], rows)
    scan_job_id = None
    try:
        scan_job_id = JOBS.enqueue_scan(root_id)["id"]
    except RuntimeError:
        # 队列满/磁盘紧张时文件已经复制成功，索引留给用户手动「扫描新增」。
        scan_job_id = None
    return {"root": root_id, "relpath": relpath, "scan_job_id": scan_job_id}


def cleanup(max_age_seconds: int = RETENTION_SECONDS) -> int:
    """删掉过期任务目录（缩略图与上传原件）。由 JobManager 的每日清理顺带调用。"""
    if not PROBE_DIR.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    staging = PROBE_DIR / "staging"
    for parent in (PROBE_DIR, staging):
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.name == "staging" and parent == PROBE_DIR:
                continue
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
                    with _items_lock:
                        _items_cache.pop(child.name, None)
            except OSError:
                continue
    return removed


def staging_dir() -> Path:
    directory = PROBE_DIR / "staging"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def staging_path(filename: str) -> Path:
    """上传暂存位置：先落盘，再建任务，避免 worker 比上传先跑到空文件。"""
    directory = staging_dir() / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    return directory / safe_filename(filename, fallback="upload")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
