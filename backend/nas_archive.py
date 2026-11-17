"""下载成功后的作品自动归档到 NAS（宿主侧队列）。

设计口径
--------
- **不阻塞下载**：各平台下载完成点只调 :func:`submit_path` 入队，摘要计算、HTTP
  提交、重试都在后台单线程里做；任何异常只写日志，绝不影响下载主流程。
- **幂等**：台账 ``data/nas_archive.jsonl`` 按 ``<platform>|<短ID>`` 去重，另外提交
  前向媒体服务核对在途任务，宿主重启也不会重复提交同一作品。
- **不删本地**：提交时固定 ``cleanup=false``。素材检索的本地根与「离线打开」都依赖
  这些文件，自动归档只负责「NAS 上也有一份同版本」。
- **目标根**：``wx-archive``（zspace），由 ``backend/media_host.py`` 注入媒体服务。

状态：``queued``（待提交）/``submitted``（已提交，等媒体服务跑完）/
``archived``（NAS 已校验）/``failed``（超过重试上限或不可恢复）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("nas_archive")

REPO_ROOT = Path(__file__).resolve().parent.parent

# 本地媒体根 → 绝对路径；与 backend/media_host.py::_media_root_env() 的口径一致。
# 视频号落在 articles_full/channels/ 下，共用 articles-full 根。
_MEDIA_ROOT_DIRS: dict[str, tuple[str, ...]] = {
    "articles-full": ("data", "articles_full"),
    "douyin": ("data", "douyin_downloads"),
    "kuaishou": ("data", "kuaishou_downloads"),
    "bilibili": ("data", "bilibili_downloads"),
    "xhs": ("data", "xhs_downloads"),
}

# 目标根固定为 NAS 归档根；target_dir 逐平台分目录，便于人工浏览。
TARGET_ROOT = os.getenv("WX_NAS_TARGET_ROOT", "wx-archive")
ENABLED = os.getenv("WX_NAS_ARCHIVE", "1") not in ("0", "false", "False", "no")
LEDGER_PATH = Path(os.getenv("WX_NAS_LEDGER") or (REPO_ROOT / "data" / "nas_archive.jsonl"))
MAX_ATTEMPTS = int(os.getenv("WX_NAS_ARCHIVE_ATTEMPTS", "3"))
RETRY_BACKOFF = (10.0, 60.0, 300.0)
HTTP_TIMEOUT = float(os.getenv("WX_NAS_ARCHIVE_TIMEOUT", "60"))
RECONCILE_INTERVAL = 5.0
# 同一时刻最多追踪多少在途任务，避免媒体服务队列被打爆
MAX_OUTSTANDING = 8

_lifecycle_lock = threading.RLock()
_host_provider = None
_entries: dict[str, dict] = {}
_pending: "queue.Queue[dict]" = queue.Queue()
_worker: threading.Thread | None = None
_started = False
_ledger_lines = 0


# ── 台账 ────────────────────────────────────────────────


def _load_ledger() -> None:
    global _ledger_lines
    _entries.clear()
    _ledger_lines = 0
    try:
        text = LEDGER_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning("读取归档台账失败（忽略）: %s", exc)
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        _ledger_lines += 1
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = item.get("work_key")
        if isinstance(key, str) and key:
            _entries[key] = item


def _append_ledger(item: dict) -> None:
    global _ledger_lines
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        _ledger_lines += 1
    except OSError as exc:
        log.warning("写归档台账失败（忽略）: %s", exc)


def _compact_ledger() -> None:
    """台账只追加；体量翻倍时重写一次快照，避免无限膨胀。"""
    global _ledger_lines
    if _ledger_lines <= max(len(_entries) * 2, 200):
        return
    tmp = LEDGER_PATH.with_suffix(".jsonl.tmp")
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            for item in _entries.values():
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        tmp.replace(LEDGER_PATH)
        _ledger_lines = len(_entries)
    except OSError as exc:
        log.warning("压缩归档台账失败（忽略）: %s", exc)


def _save_entry(entry: dict) -> None:
    _entries[entry["work_key"]] = entry
    _append_ledger(entry)
    _compact_ledger()


# ── 媒体根解析 ──────────────────────────────────────────


def _root_dirs() -> dict[str, Path]:
    return {root_id: REPO_ROOT.joinpath(*parts) for root_id, parts in _MEDIA_ROOT_DIRS.items()}


def resolve_root(path: str | os.PathLike) -> tuple[str, str] | None:
    """把作品路径归到某个本地媒体根，返回 (root_id, 根内相对路径)。

    不在任何已知根内时返回 None：媒体服务只接受已注册的根，硬塞会 422。
    """
    target = Path(path)
    try:
        resolved = target.resolve()
    except OSError:
        return None
    for root_id, base in _root_dirs().items():
        try:
            base_resolved = base.resolve()
        except OSError:
            continue
        try:
            rel = resolved.relative_to(base_resolved)
        except ValueError:
            continue
        rel_posix = rel.as_posix()
        if not rel_posix or rel_posix == ".":
            return None
        return root_id, rel_posix
    return None


def _is_under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


# ── 媒体服务访问 ────────────────────────────────────────


def configure(host_provider) -> None:
    """绑定宿主进程管理器（与 backend/media_sync.py 同样的注入方式）。"""
    global _host_provider
    _host_provider = host_provider


def _base_url() -> str | None:
    if _host_provider is None:
        return None
    try:
        host = _host_provider()
        st = host.status()
    except Exception:  # 宿主尚未就绪
        return None
    if not st.get("ready") or st.get("state") != "running":
        return None
    return host.base_url


def _job_snapshot(work_id: str) -> dict | None:
    """查媒体服务近端任务里是否已有同作品的在途归档，用于跨进程去重。"""
    base = _base_url()
    if base is None:
        raise RuntimeError("媒体服务未就绪")
    resp = requests.get(f"{base}/api/work-archive/jobs", params={"limit": 50},
                        timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    for job in resp.json().get("items", []):
        if job.get("status") not in ("queued", "running", "waiting_verification"):
            continue
        for work in job.get("works", []):
            if work.get("id") == work_id:
                return job
    return None


def _submit_job(platform: str, work_id: str, root_id: str, relpath: str,
                files: list[dict]) -> dict:
    base = _base_url()
    if base is None:
        raise RuntimeError("媒体服务未就绪")
    payload = {
        "works": [{
            "id": work_id,
            "platform": platform,
            "source_root": root_id,
            "files": files,
        }],
        "target_root": TARGET_ROOT,
        "target_dir": platform,
        # 自动归档保留本地：素材检索与离线打开都依赖本地文件
        "cleanup": False,
    }
    resp = requests.post(f"{base}/api/work-archive/jobs", json=payload,
                         timeout=HTTP_TIMEOUT)
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = str(resp.json().get("detail"))[:200]
        except ValueError:
            detail = resp.text[:200]
        raise RuntimeError(f"提交归档失败 {resp.status_code}: {detail}")
    return resp.json()


def _job_status(job_id: str) -> dict | None:
    base = _base_url()
    if base is None:
        raise RuntimeError("媒体服务未就绪")
    resp = requests.get(f"{base}/api/work-archive/jobs/{job_id}", timeout=HTTP_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


# ── 文件清单 ────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(work_path: Path, root_base: Path) -> list[dict]:
    """列出作品内全部文件及其 sha256/大小，相对路径以媒体根为基准。"""
    if work_path.is_file():
        candidates = [work_path]
    else:
        candidates = sorted(p for p in work_path.rglob("*") if p.is_file())
    files: list[dict] = []
    for path in candidates:
        try:
            rel = path.resolve().relative_to(root_base.resolve()).as_posix()
            stat = path.stat()
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"读取作品文件失败：{path}（{exc}）") from exc
        files.append({
            "relpath": rel,
            "size": stat.st_size,
            "digest": _sha256_file(path),
            "mtime": stat.st_mtime,
        })
    return files


# ── 队列 ────────────────────────────────────────────────


def short_id(value: str) -> str:
    """把 URL 之类的长标识压成稳定短 ID（台账与 NAS 目录名都用它）。"""
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def submit_path(platform: str, work_id: str, path: str | os.PathLike) -> bool:
    """下载成功后登记一个待归档作品。任何异常都吞掉，只记日志。

    ``work_id`` 是作品的短标识（抖音 aweme_id、B 站 bvid、公众号 URL 摘要等），
    最终作品键为 ``<platform>|<work_id>``。
    """
    if not ENABLED:
        return False
    try:
        work_key = f"{platform}|{work_id}"
        target = Path(path)
        with _lifecycle_lock:
            existing = _entries.get(work_key)
            if existing and existing.get("status") in ("archived", "failed"):
                return False
            if existing and existing.get("status") in ("queued", "submitted"):
                return False
            resolved = resolve_root(target)
            if resolved is None:
                log.info("跳过 NAS 归档：%s 不在已知媒体根内（%s）", work_key, target)
                return False
            root_id, relpath = resolved
            entry = {
                "work_key": work_key,
                "platform": platform,
                "work_id": work_key,
                "source_root": root_id,
                "relpath": relpath,
                "status": "queued",
                "attempts": 0,
                "job_id": None,
                "error": None,
                "ts": time.time(),
            }
            _save_entry(entry)
            _pending.put(entry)
            _ensure_worker()
        return True
    except Exception as exc:  # 归档登记失败绝不能影响下载
        log.warning("登记 NAS 归档失败（忽略）: %s", exc)
        return False


def retry(work_key: str) -> bool:
    """人工重试一个失败的作品（前端状态卡「重试」按钮）。

    只重置失败记录：``submitted``/``archived`` 不该被重复提交，直接拒绝。
    """
    if not ENABLED:
        return False
    with _lifecycle_lock:
        entry = _entries.get(work_key)
        if entry is None or entry.get("status") != "failed":
            return False
        entry.update(status="queued", attempts=0, error=None, job_id=None, ts=time.time())
        _save_entry(entry)
        _pending.put(entry)
        _ensure_worker()
    return True


def _ensure_worker() -> None:
    global _worker, _started
    if _started:
        return
    _started = True
    _worker = threading.Thread(target=_loop, daemon=True, name="nas-archive")
    _worker.start()


def _loop() -> None:
    while True:
        try:
            entry = _pending.get(timeout=RECONCILE_INTERVAL)
        except queue.Empty:
            _reconcile()
            continue
        try:
            _process(entry)
        except Exception as exc:  # 单条失败不拖垮队列
            log.warning("归档任务异常（%s）: %s", entry.get("work_key"), exc)
        finally:
            _pending.task_done()


def _process(entry: dict) -> None:
    if entry.get("status") not in ("queued", None):
        return
    base = _base_url()
    if base is None:
        # 媒体服务还没起来：放回队列，等下一轮（不影响下载）
        entry["error"] = "媒体服务未就绪，等待重试"
        _save_entry(entry)
        _pending.put(entry)
        time.sleep(RECONCILE_INTERVAL)
        return

    work_path = _entry_path(entry)
    if work_path is None or not work_path.exists():
        entry.update(status="failed", error="本地作品目录已不存在")
        _save_entry(entry)
        return

    root_base = _root_dirs().get(entry["source_root"])
    if root_base is None:
        entry.update(status="failed", error=f"未知媒体根：{entry['source_root']}")
        _save_entry(entry)
        return

    try:
        if _job_snapshot(entry["work_id"]) is not None:
            entry.update(status="submitted", job_id=None,
                         error="媒体服务已有在途任务，跳过重复提交")
            _save_entry(entry)
            return
        files = build_manifest(work_path, root_base)
        if not files:
            entry.update(status="failed", error="作品内没有可归档文件")
            _save_entry(entry)
            return
        job = _submit_job(entry["platform"], entry["work_id"],
                          entry["source_root"], entry["relpath"], files)
        entry.update(status="submitted", job_id=job.get("id"), error=None,
                     file_count=len(files), ts=time.time())
        _save_entry(entry)
        log.info("已提交 NAS 归档：%s（%d 个文件，job=%s）",
                 entry["work_key"], len(files), job.get("id"))
    except Exception as exc:
        _fail_or_retry(entry, str(exc))


def _fail_or_retry(entry: dict, error: str) -> None:
    attempts = int(entry.get("attempts") or 0) + 1
    entry["attempts"] = attempts
    entry["error"] = error[:300]
    if attempts >= MAX_ATTEMPTS:
        entry["status"] = "failed"
        _save_entry(entry)
        log.warning("归档放弃：%s（%s）", entry["work_key"], error[:200])
        return
    delay = RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)]
    entry["status"] = "queued"
    entry["next_attempt"] = time.time() + delay
    _save_entry(entry)
    threading.Timer(delay, lambda item=dict(entry): _pending.put(item)).start()
    log.info("归档将重试：%s（第 %d 次，%.0fs 后）", entry["work_key"], attempts, delay)


def _entry_path(entry: dict) -> Path | None:
    base = _root_dirs().get(entry.get("source_root", ""))
    if base is None or not entry.get("relpath"):
        return None
    candidate = (base / entry["relpath"]).resolve()
    if not _is_under(candidate, base.resolve()):
        return None
    return candidate


def _reconcile() -> None:
    """轮询在途任务：跑完的落 archived，失败的按退避重试。"""
    if not ENABLED:
        return
    with _lifecycle_lock:
        outstanding = [e for e in _entries.values()
                       if e.get("status") == "submitted" and e.get("job_id")]
    for entry in outstanding[:MAX_OUTSTANDING]:
        try:
            job = _job_status(entry["job_id"])
        except Exception:
            continue
        if job is None:
            entry.update(status="failed", error="媒体服务已无该归档任务记录")
            _save_entry(entry)
            continue
        status = job.get("status")
        if status in ("queued", "running", "waiting_verification"):
            continue
        if status == "completed":
            entry.update(status="archived", error=None, ts=time.time())
            _save_entry(entry)
            continue
        reasons = [w.get("status") for w in job.get("works", [])]
        _fail_or_retry(entry, f"归档任务 {status}（作品状态 {reasons}）")


# ── 对外快照与 REST ─────────────────────────────────────


def status_snapshot() -> dict:
    with _lifecycle_lock:
        items = list(_entries.values())
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get("status") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    recent = sorted(items, key=lambda x: x.get("ts") or 0, reverse=True)[:20]
    return {
        "enabled": ENABLED,
        "target_root": TARGET_ROOT,
        "ledger": str(LEDGER_PATH),
        "counts": counts,
        "pending": _pending.qsize(),
        "recent": [{
            "work_key": item.get("work_key"),
            "platform": item.get("platform"),
            "status": item.get("status"),
            "attempts": item.get("attempts"),
            "job_id": item.get("job_id"),
            "error": item.get("error"),
            "ts": item.get("ts"),
        } for item in recent],
    }


def jobs_snapshot(limit: int = 20) -> dict:
    """透传媒体服务的作品归档任务列表，供前端状态卡展示。"""
    base = _base_url()
    if base is None:
        return {"available": False, "error": "媒体服务未就绪", "items": [], "total": 0}
    resp = requests.get(f"{base}/api/work-archive/jobs",
                        params={"limit": limit}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    data["available"] = True
    return data


# ── 手动多平台归档（按平台历史清单批量登记）──────────────
#
# 自动归档是「下载完成点即时登记」，手动归档是「事后按平台历史清单补登记」。
# 两者共用同一套幂等台账与提交逻辑，作品键口径完全一致（<platform>|<work_id>），
# 因此同一作品手动/自动只会在台账里占一条，不会重复入队。

_PLATFORM_LABELS: dict[str, str] = {
    "wechat": "公众号",
    "channels": "视频号",
    "douyin": "抖音",
    "kuaishou": "快手",
    "bilibili": "B站",
    "xhs": "小红书",
}

# 各平台历史清单文件（相对 REPO_ROOT/data），与各平台下载模块写历史的路径一致。
_PLATFORM_HISTORY: dict[str, tuple[str, ...]] = {
    "wechat": ("data", "download_history.json"),
    "channels": ("data", "channels_history.json"),
    "douyin": ("data", "douyin_history.json"),
    "kuaishou": ("data", "kuaishou_history.json"),
    "bilibili": ("data", "bilibili_history.json"),
    "xhs": ("data", "xhs_history.json"),
}

# 历史条目里的账号字段；视频号历史不记账号，只能退化为路径匹配。
_PLATFORM_ACCOUNT_FIELD: dict[str, str | None] = {
    "wechat": "account",
    "channels": None,
    "douyin": "source",
    "kuaishou": "author",
    "bilibili": "source",
    "xhs": "author",
}


def _leading_id(name: str) -> str:
    """从 ``<作品ID>_<标题>`` 里取作品 ID（首个下划线前的一段）。"""
    return name.split("_", 1)[0].strip()


def _wechat_work_id(path: str, item: dict) -> str:
    """公众号作品 ID 是 article_key 的短摘要，需读目录内的 metadata.json。"""
    try:
        meta = json.loads((Path(path) / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    key = meta.get("article_key")
    return short_id(str(key)) if key else ""


def _path_work_id(path: str, item: dict) -> str:
    """抖音/快手作品 ID 是落盘文件名（或目录名）的前缀数字。"""
    name = Path(path).stem if path.lower().endswith(
        (".mp4", ".m4a", ".jpg", ".jpeg", ".png", ".webp")) else Path(path).name
    return _leading_id(name)


def _channels_work_id(path: str, item: dict) -> str:
    """视频号 work_id 就是文件名，与下载完成点 submit_path 的口径一致。"""
    return Path(path).name


def _field_work_id(field: str):
    def _extract(path: str, item: dict) -> str:
        value = item.get(field)
        return str(value).strip() if value else ""
    return _extract


# 平台 → 从历史条目取作品 ID 的函数。
_PLATFORM_WORK_ID: dict[str, object] = {
    "wechat": _wechat_work_id,
    "channels": _channels_work_id,
    "douyin": _path_work_id,
    "kuaishou": _path_work_id,
    "bilibili": _field_work_id("bvid"),
    "xhs": _field_work_id("note_id"),
}


def _load_history(platform: str) -> list:
    path = REPO_ROOT.joinpath(*_PLATFORM_HISTORY[platform])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("读取 %s 历史清单失败（忽略）: %s", platform, exc)
        return []


def _account_match(item: dict, platform: str, account: str) -> bool:
    if not account:
        return True
    field = _PLATFORM_ACCOUNT_FIELD.get(platform)
    if field:
        return str(item.get(field) or "").strip() == account
    # 历史不记账号的平台（视频号）：退化为路径包含匹配
    return account in str(item.get("path") or "")


def archive_platform_works(platform: str, account: str = "") -> dict:
    """把某个平台历史清单里已下载的作品批量登记进归档队列。

    手动入口：只做「登记」，摘要计算与提交仍在后台队列里完成，不阻塞调用方。
    已归档/在途的作品会被 :func:`submit_path` 判为已存在而跳过，不重复产生任务。
    """
    if platform not in _PLATFORM_LABELS:
        raise ValueError(f"未知平台: {platform}")
    work_id_fn = _PLATFORM_WORK_ID[platform]
    queued = skipped = 0
    failures: list[dict] = []
    for item in _load_history(platform):
        if not isinstance(item, dict) or item.get("success") is False:
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        if not _account_match(item, platform, account):
            continue
        work_id = work_id_fn(path, item)
        if not work_id:
            continue
        try:
            if submit_path(platform, work_id, path):
                queued += 1
            else:
                skipped += 1
        except Exception as exc:  # 单条失败不影响其余作品
            log.warning("手动登记归档失败（%s|%s）: %s", platform, work_id, exc)
            failures.append({"work_key": f"{platform}|{work_id}", "error": str(exc)[:200]})
    return {
        "platform": platform,
        "platform_label": _PLATFORM_LABELS[platform],
        "account": account,
        "queued": queued,
        "skipped": skipped,
        "failed": failures,
    }


def init_blueprint(app):
    """注册宿主侧只读 REST（/api/media/nas-archive/*）。"""
    from flask import Blueprint, jsonify, request

    from backend.media_host import MediaHost

    host = getattr(app, "config", {}).get("MEDIA_HOST_OBJ")
    if host is None:
        host = MediaHost()
        app.config["MEDIA_HOST_OBJ"] = host
    configure(lambda: host)

    _load_ledger()
    with _lifecycle_lock:
        _ensure_worker()

    bp = Blueprint("nas_archive", __name__, url_prefix="/api/media/nas-archive")

    @bp.route("/status", methods=["GET"])
    def status():
        return jsonify(status_snapshot())

    @bp.route("/jobs", methods=["GET"])
    def jobs():
        try:
            limit = int(request.args.get("limit", 20))
        except ValueError:
            limit = 20
        limit = max(1, min(limit, 100))
        try:
            return jsonify(jobs_snapshot(limit))
        except Exception as exc:
            return jsonify({"available": False, "error": str(exc)[:200],
                            "items": [], "total": 0})

    @bp.route("/retry", methods=["POST"])
    def retry_work():
        payload = request.get_json(silent=True) or {}
        work_key = payload.get("work_key")
        if not isinstance(work_key, str) or not work_key.strip():
            return jsonify({"error": "work_key 不能为空"}), 400
        key = work_key.strip()
        if not retry(key):
            return jsonify({"error": "该作品当前不可重试（仅失败记录可重试）"}), 409
        return jsonify({"ok": True, "work_key": key})

    @bp.route("/platform", methods=["POST"])
    def archive_platform():
        """手动入口：按平台历史清单把已下载作品批量登记进归档队列。"""
        payload = request.get_json(silent=True) or {}
        platform = payload.get("platform")
        account = payload.get("account")
        if not isinstance(platform, str) or platform.strip() not in _PLATFORM_LABELS:
            return jsonify({"error": "platform 必须是受支持的平台"}), 400
        if account is not None and not isinstance(account, str):
            return jsonify({"error": "account 必须是字符串"}), 400
        try:
            return jsonify(archive_platform_works(platform.strip(), (account or "").strip()))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"登记失败：{str(exc)[:200]}"}), 500

    return bp


_load_ledger()
