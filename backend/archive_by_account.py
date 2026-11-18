"""按号归档的平台适配层。

「平台 → 账号 → 最近 N 篇 → 下载 → 扫对应媒体根」这条链在每个平台上
的入口和进度形状都不一样。本模块只做翻译和归一，不复制任何下载逻辑：

- 账号：各平台自己的收藏表 → 统一的 {id, name, sub, url}
- 启动：调用各平台既有的后台线程入口（见各 _start_* 的注释行号）
- 进度：各平台原生任务记录 → 统一的 {status,total,completed,failed,skipped,current}
- 入库：下载终态后自动向媒体子服务提交该根的扫描（幂等，失败只记 scan_error）

平台能力由 init_blueprint 的 platforms 参数决定：全功能版注册四个平台，
公众号轻量版（app_mp.py）只注册公众号。因此对 xhs/bilibili/kuaishou
的 import 一律写在函数内部——轻量版没有装 curl_cffi/lxml 这些依赖。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Optional

import requests

from backend.config import ARCHIVE_TASKS_FILE, load_json, save_json

log = logging.getLogger("backend.archive_by_account")

# 单次归档的篇数上限，与前端 ① 卡片的输入范围保持一致
MAX_LIMIT = 50
DEFAULT_LIMIT = 10
# 快手首页免登录接口固定 20 条/页，只能按页换算
KUAISHOU_PAGE_SIZE = 20
# B站 space 接口单页上限
BILIBILI_PAGE_SIZE = 50
# 活跃槽超过这个时长仍未终结就回收，避免页面关闭后永久锁死
ACTIVE_TTL_SECONDS = 6 * 3600
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

# 下载记录台账的保留上限：条数与时间双上限，避免文件随下载单调膨胀
ARCHIVE_TASKS_MAX_COUNT = 200
ARCHIVE_TASKS_MAX_AGE_SECONDS = 7 * 24 * 3600
# 记录列表单次返回上限
ARCHIVE_TASKS_LIST_MAX = 200
ARCHIVE_TASKS_LIST_DEFAULT = 50

PLATFORM_LABELS = {
    "wechat": "微信公众号",
    "xhs": "小红书",
    "bilibili": "B站",
    "kuaishou": "快手",
}

# 与 media_host / nas_archive 的媒体根映射保持一致
PLATFORM_ROOTS = {
    "wechat": "articles-full",
    "xhs": "xhs",
    "bilibili": "bilibili",
    "kuaishou": "kuaishou",
}

_config_lock = threading.RLock()
_host_provider: Optional[Callable[[], object]] = None
_platforms: tuple = ("wechat",)

# task_id -> 归一任务记录（含原生任务 id、扫描状态）
_tasks: dict = {}
# platform -> 正在跑的 task_id
_active: dict = {}
# 下载记录台账的读写锁：start/status 与列表读取可能并发
_ledger_lock = threading.RLock()


class ArchiveError(Exception):
    """带 HTTP 状态码的业务错误，由 blueprint 转成 JSON 响应。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def configure(host_provider: Optional[Callable[[], object]] = None, platforms=None) -> None:
    """注入媒体服务句柄提供者和启用的平台列表。

    host_provider 每次调用都要返回当前的 MediaHost 实例：app.py 里
    MediaHost 先于 blueprint 创建，但对象本身可能是后替换的。
    """
    global _host_provider, _platforms
    with _config_lock:
        if host_provider is not None:
            _host_provider = host_provider
        if platforms is not None:
            _platforms = tuple(p for p in platforms if p in PLATFORM_LABELS)


def platform_matrix() -> dict:
    """平台能力矩阵，前端据此渲染平台下拉与提示文案。"""
    with _config_lock:
        enabled = _platforms
    items = []
    for pid in enabled:
        item = {
            "id": pid,
            "name": PLATFORM_LABELS[pid],
            "account_source": "list",
            "limit_unit": "page" if pid == "kuaishou" else "note",
            "root": PLATFORM_ROOTS[pid],
            "enabled": True,
            "reason": "",
        }
        if pid == "kuaishou":
            item["allow_link"] = True
            item["note"] = (
                f"快手按页下载，最近 N 篇按 ceil(N/{KUAISHOU_PAGE_SIZE}) 页估算，"
                "实际条数可能多于 N"
            )
        items.append(item)
    return {"platforms": items}


def list_accounts(platform: str) -> dict:
    _ensure_platform(platform)
    loaders = {
        "wechat": _accounts_wechat,
        "xhs": _accounts_xhs,
        "bilibili": _accounts_bilibili,
        "kuaishou": _accounts_kuaishou,
    }
    accounts = loaders[platform]()
    return {
        "platform": platform,
        "name": PLATFORM_LABELS[platform],
        "source": "list",
        "accounts": accounts,
        "total": len(accounts),
    }


def start(platform: str, account_id: str = "", account_name: str = "",
          limit=None, profile_url: str = "") -> dict:
    """启动一次按号归档，返回归一任务记录。"""
    _ensure_platform(platform)
    limit = _normalize_limit(limit)
    account_id = (account_id or "").strip()
    account_name = (account_name or "").strip()

    _reconcile_active()
    with _config_lock:
        running = _active.get(platform)
    if running:
        raise ArchiveError("该平台已有归档任务在运行，请等待完成或稍后重试", 409)

    dispatchers = {
        "wechat": _start_wechat,
        "xhs": _start_xhs,
        "bilibili": _start_bilibili,
        "kuaishou": _start_kuaishou,
    }

    task_id = _next_task_id(platform)
    record = {
        "task_id": task_id,
        "platform": platform,
        "root": PLATFORM_ROOTS[platform],
        "requested": limit,
        "account_id": account_id,
        "account_name": account_name,
        "native_task_id": "",
        "created_at": time.time(),
        "scan_job_id": "",
        "scan_error": "",
        "scan_requested": False,
        "note": "",
    }
    # 先占槽再派发，避免并发 start 双开
    with _config_lock:
        _active[platform] = task_id
        _tasks[task_id] = record

    try:
        detail = dispatchers[platform](
            account_id=account_id, account_name=account_name,
            limit=limit, profile_url=profile_url, task_id=task_id,
        )
    except ArchiveError:
        _drop_task(platform, task_id)
        raise
    except Exception as exc:  # 平台模块抛出的原始异常
        _drop_task(platform, task_id)
        log.exception("启动 %s 归档失败", platform)
        raise ArchiveError(
            f"启动{PLATFORM_LABELS[platform]}下载失败：{str(exc)[:200]}", 502) from exc

    with _config_lock:
        record.update(detail)
    payload = _task_payload(record)
    _sync_ledger(record, payload)
    return payload


def task_status(platform: str, task_id: str) -> dict:
    """归一进度；顺带完成终态回收与自动扫描。"""
    _ensure_platform(platform)
    task_id = (task_id or "").strip()
    if not task_id:
        raise ArchiveError("缺少 task_id", 400)
    with _config_lock:
        record = _tasks.get(task_id)
    if record is None or record["platform"] != platform:
        raise ArchiveError("任务不存在或已过期", 404)

    payload = _native_progress(record)
    _maybe_scan(record, payload)
    _prune_tasks()
    _sync_ledger(record, payload)
    return _task_payload(record, payload)


# ---------------------------------------------------------------- 下载记录台账

def _ledger_load() -> list:
    """载入下载记录台账；文件缺失或格式异常时按空列表处理。"""
    rows = load_json(ARCHIVE_TASKS_FILE, [])
    return rows if isinstance(rows, list) else []


def _ledger_save(rows: list) -> None:
    save_json(ARCHIVE_TASKS_FILE, rows)


def _prune_ledger(rows: list) -> list:
    """按「最近 7 天 + 最近 200 条」双上限裁剪，倒序返回。"""
    cutoff = time.time() - ARCHIVE_TASKS_MAX_AGE_SECONDS
    keep = [row for row in rows if (row.get("created_at") or 0) >= cutoff]
    keep.sort(key=lambda row: row.get("created_at") or 0, reverse=True)
    return keep[:ARCHIVE_TASKS_MAX_COUNT]


def _ledger_record(record: dict, progress: Optional[dict] = None) -> dict:
    """把内存任务记录转成台账记录（含展示与溯源字段）。"""
    payload = _task_payload(record, progress)
    payload["account_id"] = record.get("account_id") or ""
    payload["platform_label"] = PLATFORM_LABELS.get(record["platform"], record["platform"])
    payload["created_at"] = record.get("created_at") or 0
    payload["updated_at"] = time.time()
    if progress and progress.get("status") in TERMINAL_STATUSES:
        payload["finished_at"] = payload["updated_at"]
    else:
        payload["finished_at"] = record.get("finished_at") or ""
    return payload


def _ledger_upsert(record: dict, progress: Optional[dict] = None) -> None:
    """把一条任务记录写入台账（按 task_id 去重），并做保留裁剪。"""
    with _ledger_lock:
        rows = _ledger_load()
        task_id = record.get("task_id") or ""
        row = _ledger_record(record, progress)
        for index, existing in enumerate(rows):
            if existing.get("task_id") == task_id:
                rows[index] = row
                break
        else:
            rows.append(row)
        _ledger_save(_prune_ledger(rows))


def _sync_ledger(record: dict, progress: dict) -> None:
    """把任务当前状态同步到台账；台账故障不影响下载主流程。"""
    try:
        _ledger_upsert(record, progress)
    except Exception as exc:  # 台账写失败只记日志，下载与入库不受影响
        log.warning("下载记录台账写入失败：%s", exc)


def list_tasks(platform: str = "", limit=None) -> dict:
    """下载记录列表，按发起时间倒序；platform 为空则返回全部平台。"""
    with _ledger_lock:
        rows = _ledger_load()
    if (platform or "").strip():
        rows = [row for row in rows if row.get("platform") == platform.strip()]
    rows.sort(key=lambda row: row.get("created_at") or 0, reverse=True)
    total = len(rows)

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = ARCHIVE_TASKS_LIST_DEFAULT
    if limit < 1 or limit > ARCHIVE_TASKS_LIST_MAX:
        limit = ARCHIVE_TASKS_LIST_DEFAULT
    return {"tasks": rows[:limit], "total": total}


def init_blueprint(app, platforms=("wechat",)):
    """注册 /api/archive/*；platforms 决定本进程暴露哪些平台。"""
    from flask import Blueprint, jsonify, request

    configure(None, platforms)

    host = None
    try:
        host = app.config.get("MEDIA_HOST_OBJ")
    except Exception:
        host = None
    if host is None:
        from backend.media_host import MediaHost
        host = MediaHost()
        app.config["MEDIA_HOST_OBJ"] = host
    configure(lambda: host)

    bp = Blueprint("archive_by_account", __name__, url_prefix="/api/archive")

    @bp.route("/platforms", methods=["GET"])
    def api_platforms():
        return jsonify(platform_matrix())

    @bp.route("/accounts", methods=["GET"])
    def api_accounts():
        platform = (request.args.get("platform") or "").strip()
        # 曾默认按 wechat 查：调用方漏传平台时会静默返回公众号账号，把别的平台账号错配掉。
        if not platform:
            return jsonify({"error": "缺少 platform 参数"}), 400
        try:
            return jsonify(list_accounts(platform))
        except ArchiveError as exc:
            return jsonify({"error": exc.message}), exc.status
        except Exception as exc:
            log.exception("读取 %s 账号失败", platform)
            return jsonify({"error": f"读取账号失败：{str(exc)[:200]}"}), 500

    @bp.route("/start", methods=["POST"])
    def api_start():
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(start(
                platform=(payload.get("platform") or "").strip(),
                account_id=payload.get("account_id") or "",
                account_name=payload.get("account_name") or "",
                limit=payload.get("limit"),
                profile_url=payload.get("profile_url") or "",
            ))
        except ArchiveError as exc:
            return jsonify({"error": exc.message}), exc.status
        except Exception as exc:
            log.exception("启动归档失败")
            return jsonify({"error": f"启动失败：{str(exc)[:200]}"}), 500

    @bp.route("/status", methods=["GET"])
    def api_status():
        try:
            return jsonify(task_status(
                platform=(request.args.get("platform") or "").strip(),
                task_id=(request.args.get("task_id") or "").strip(),
            ))
        except ArchiveError as exc:
            return jsonify({"error": exc.message}), exc.status
        except Exception as exc:
            log.exception("读取归档进度失败")
            return jsonify({"error": f"读取进度失败：{str(exc)[:200]}"}), 500

    @bp.route("/tasks", methods=["GET"])
    def api_tasks():
        try:
            return jsonify(list_tasks(
                platform=(request.args.get("platform") or "").strip(),
                limit=request.args.get("limit"),
            ))
        except Exception as exc:
            log.exception("读取下载记录失败")
            return jsonify({"error": f"读取下载记录失败：{str(exc)[:200]}"}), 500

    return bp


# ---------------------------------------------------------------- 账号来源

def _accounts_wechat():
    from backend.config import ACCOUNTS_FILE, load_json

    rows = load_json(ACCOUNTS_FILE, [])
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        fakeid = str(row.get("fakeid") or "").strip()
        if not fakeid:
            continue
        name = (row.get("nickname") or row.get("name") or fakeid).strip()
        out.append({"id": fakeid, "name": name, "sub": "公众号", "url": ""})
    return out


def _accounts_xhs():
    from backend import xiaohongshu as xhs

    rows = xhs.load_json(xhs.XHS_ACCOUNTS_FILE, [])
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        user_id = str(row.get("user_id") or "").strip()
        nickname = (row.get("nickname") or user_id or "").strip()
        if not user_id and not row.get("url"):
            continue
        url = (row.get("url") or "").strip() or _xhs_profile_url(user_id)
        out.append({
            "id": user_id or url,
            "name": nickname or user_id,
            "sub": "小红书博主",
            "url": url,
        })
    return out


def _accounts_bilibili():
    from backend import bilibili as bili

    rows = bili.load_json(bili.ACCOUNTS_FILE, [])
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("mid") or "").strip()
        if not mid:
            continue
        name = (row.get("nickname") or row.get("name") or mid).strip()
        url = (row.get("url") or "").strip() or f"https://space.bilibili.com/{mid}"
        out.append({"id": mid, "name": name, "sub": "B站 UP主", "url": url})
    return out


def _accounts_kuaishou():
    from backend import kuaishou as ks

    rows = ks.load_json(ks.ACCOUNTS_FILE, [])
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        user_id = str(row.get("user_id") or "").strip()
        url = (row.get("url") or "").strip()
        if not user_id and not url:
            continue
        name = (row.get("nickname") or row.get("name") or user_id or url).strip()
        out.append({
            "id": user_id or url,
            "name": name,
            "sub": "快手作者",
            # url 可能为空：这时前端提示贴主页链接
            "url": url or (f"https://www.kuaishou.com/profile/{user_id}" if user_id else ""),
        })
    return out


# ---------------------------------------------------------------- 平台启动

def _start_wechat(account_id, account_name, limit, profile_url, task_id):
    """复用公众号「采集最近文章」的原生任务与线程（backend/articles.py:1467）。"""
    from backend import articles

    if not account_id:
        raise ArchiveError("缺少公众号 fakeid，请重新选择公众号", 400)
    with articles._download_lock:
        for native in articles._download_tasks.values():
            if native.get("status") in ("running", "cancelling"):
                raise ArchiveError("公众号已有下载任务在运行，请等待完成", 409)

    if not account_name:
        for row in _accounts_wechat():
            if row["id"] == account_id:
                account_name = row["name"]
                break
    account_name = account_name or account_id

    native_task_id = f"collect_{int(time.time() * 1000)}"
    with articles._download_lock:
        articles._download_tasks[native_task_id] = {
            "task_id": native_task_id,
            "status": "running",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "scanned": 0,
            "current": "",
            "results": [],
            "stop_reason": "",
            "start_time": time.time(),
        }
    threading.Thread(
        target=articles._do_collect_latest,
        args=(native_task_id, [{"fakeid": account_id, "name": account_name}], limit, True),
        daemon=True,
        name=f"archive-wechat-{native_task_id}",
    ).start()
    return {
        "native_task_id": native_task_id,
        "account_name": account_name,
        "note": "按发布时间取最近 N 篇，已下载过的会跳过",
    }


def _start_xhs(account_id, account_name, limit, profile_url, task_id):
    """复用小红书笔记下载线程（backend/xiaohongshu.py:1496）。

    note 项没有发布时间，只能按主页列表顺序截前 N 条。
    """
    from backend import xiaohongshu as xhs

    with xhs._download_lock:
        for native in xhs._download_tasks.values():
            if native.get("status") in ("running", "cancelling"):
                raise ArchiveError("小红书已有下载任务在运行，请等待完成", 409)

    profile_url = (profile_url or "").strip()
    if not profile_url and account_id:
        for row in _accounts_xhs():
            if row["id"] == account_id:
                profile_url = row["url"]
                account_name = account_name or row["name"]
                break
    if not profile_url and account_id.startswith("http"):
        profile_url = account_id
    if not profile_url:
        raise ArchiveError("缺少小红书博主主页链接", 400)

    client = xhs.XhsClient()
    try:
        profile = client.get_user_profile(profile_url) or {}
    except Exception as exc:
        raise ArchiveError(f"读取博主笔记失败：{str(exc)[:160]}", 502) from exc
    notes = list(profile.get("notes") or [])
    user_id = str(profile.get("user_id") or account_id or "").strip()
    if len(notes) < limit and profile.get("has_more") and user_id:
        try:
            more, _ = xhs._session_manager.get_more(user_id, loaded_count=len(notes))
            notes.extend(list(more or []))
        except Exception as exc:  # 续拉失败不致命，按已有首屏截断
            log.warning("小红书续拉笔记失败：%s", exc)
    notes = notes[:limit]
    if not notes:
        raise ArchiveError("该博主暂无可用笔记", 404)

    account_name = account_name or profile.get("nickname") or user_id or "小红书博主"
    urls = []
    for note in notes:
        note_id = str(note.get("note_id") or "").strip()
        if not note_id:
            continue
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        token = note.get("xsec_token")
        if token:
            url += f"?xsec_token={token}&xsec_source=pc_user"
        urls.append(url)
    if not urls:
        raise ArchiveError("该博主暂无可用笔记", 404)

    native_task_id = f"xhs_{int(time.time() * 1000)}"
    with xhs._download_lock:
        xhs._download_tasks[native_task_id] = {
            "task_id": native_task_id,
            "status": "running",
            "total": len(urls),
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "current": "",
            "results": [],
            "start_time": time.time(),
            "cancel_requested": False,
        }
    threading.Thread(
        target=xhs._do_xhs_download_thread,
        args=(native_task_id, urls, account_name),
        daemon=True,
        name=f"archive-xhs-{native_task_id}",
    ).start()
    return {
        "native_task_id": native_task_id,
        "account_name": account_name,
        "note": "按主页列表顺序取最近 N 篇（笔记无发布时间）",
    }


def _start_bilibili(account_id, account_name, limit, profile_url, task_id):
    """复用 B站批量下载线程（backend/bilibili.py:858）。

    先按 page_size 翻页凑够 N 条投稿，再一次性交给批量下载。
    """
    from backend import bilibili as bili

    if not account_id:
        raise ArchiveError("缺少 B站 mid", 400)
    with bili._task_lock:
        if bili._task_state.get("status") == "running":
            raise ArchiveError("B站已有批量下载任务在运行，请等待完成", 409)

    if not account_name:
        for row in _accounts_bilibili():
            if row["id"] == account_id:
                account_name = row["name"]
                break
    account_name = account_name or account_id

    client = bili.BilibiliClient()
    videos = []
    seen_bvid = set()
    page = 1
    page_size = min(max(limit, 1), BILIBILI_PAGE_SIZE)
    try:
        # 单页不满 page_size 不代表没有下一页（服务端可能限量），按 total 判断
        while len(videos) < limit and page <= 5:
            vlist, total = client.get_user_videos(account_id, page=page, page_size=page_size)
            vlist = list(vlist or [])
            if not vlist:
                break
            for video in vlist:
                bvid = str(video.get("bvid") or "").strip()
                if bvid and bvid not in seen_bvid:
                    seen_bvid.add(bvid)
                    videos.append(video)
            if len(videos) >= limit:
                break
            if total and len(videos) >= int(total):
                break
            page += 1
    except Exception as exc:
        raise ArchiveError(f"读取投稿列表失败：{str(exc)[:160]}", 502) from exc

    items = []
    for video in videos[:limit]:
        bvid = str(video.get("bvid") or "").strip()
        if not bvid:
            continue
        items.append({
            "bvid": bvid,
            "title": video.get("title") or f"BV {bvid}",
            "page_num": 1,
        })
    if not items:
        raise ArchiveError("该 UP 主没有可下载的投稿", 404)

    bili.ensure_bili_dirs()
    bili._task_cancel_event.clear()
    threading.Thread(
        target=bili._run_batch_download_thread,
        args=(items, bili.BILI_DIR, None),
        daemon=True,
        name="archive-bilibili",
    ).start()
    return {
        "native_task_id": "bilibili",
        "account_name": account_name,
        "note": f"取最近 {len(items)} 条投稿",
    }


def _start_kuaishou(account_id, account_name, limit, profile_url, task_id):
    """复用快手主页下载线程（backend/kuaishou.py:936）。

    该线程只接受 max_pages，因此「最近 N 篇」按 ceil(N/20) 页近似，
    实际下载条数可能多于 N（已在平台矩阵的 note 里告知前端）。
    """
    from backend import kuaishou as ks

    with ks._task_lock:
        if ks._task_state.get("status") == "running":
            raise ArchiveError("快手已有批量下载任务在运行，请等待完成", 409)

    profile_url = (profile_url or "").strip()
    if account_id and account_id.startswith("http"):
        profile_url = profile_url or account_id
        account_id = ""
    if account_id and not profile_url:
        for row in _accounts_kuaishou():
            if row["id"] == account_id:
                profile_url = row["url"]
                account_name = account_name or row["name"]
                break
    if not profile_url and account_id:
        profile_url = f"https://www.kuaishou.com/profile/{account_id}"
    if not profile_url:
        raise ArchiveError("缺少快手作者主页链接", 400)

    client = ks.KuaishouClient()
    try:
        resolved = client.resolve_share_url(profile_url) or profile_url
    except Exception as exc:
        raise ArchiveError(f"解析快手主页失败：{str(exc)[:160]}", 502) from exc
    user_id = (ks.KuaishouClient.extract_user_id(resolved)
               or ks.KuaishouClient.extract_user_id(profile_url)
               or account_id
               or "kuaishou_user")

    max_pages = max(1, math.ceil(limit / KUAISHOU_PAGE_SIZE))
    ks.ensure_kuaishou_dirs()
    ks._task_cancel_event.clear()
    ks._set_task_state(status="running", total=0, downloaded_count=0,
                       failed_count=0, current_title="准备中")
    threading.Thread(
        target=ks._run_profile_download_task,
        args=(user_id, max_pages, ks.KUAISHOU_DIR, profile_url),
        daemon=True,
        name="archive-kuaishou",
    ).start()
    return {
        "native_task_id": "kuaishou",
        "account_name": account_name or user_id,
        "note": f"按 {max_pages} 页下载，实际条数可能多于 {limit}",
    }


# ---------------------------------------------------------------- 进度归一

def _native_progress(record: dict) -> dict:
    platform = record["platform"]
    readers = {
        "wechat": _progress_from_task_dict,
        "xhs": _progress_from_task_dict,
        "bilibili": _progress_from_task_state,
        "kuaishou": _progress_from_task_state,
    }
    try:
        return readers[platform](record)
    except Exception as exc:  # 平台模块不可用/被卸载
        log.warning("读取 %s 进度失败：%s", platform, exc)
        return _empty_progress("failed", f"读取进度失败：{str(exc)[:160]}")


def _progress_from_task_dict(record: dict) -> dict:
    """wechat / xhs：原生任务记录就是 dict，字段基本一致。"""
    if record["platform"] == "wechat":
        from backend import articles as module
    else:
        from backend import xiaohongshu as module

    native = module._download_tasks.get(record.get("native_task_id") or "")
    if native is None:
        return _empty_progress("failed", "原生任务记录已被清理")
    return _progress(
        status=_map_status(native.get("status")),
        total=native.get("total") or 0,
        completed=native.get("completed") or 0,
        failed=native.get("failed") or 0,
        skipped=native.get("skipped") or 0,
        current=native.get("current") or "",
        reason=native.get("stop_reason") or "",
    )


def _progress_from_task_state(record: dict) -> dict:
    """bilibili / kuaishou：全局 _task_state 快照。"""
    if record["platform"] == "bilibili":
        from backend import bilibili as module
    else:
        from backend import kuaishou as module

    with module._task_lock:
        state = dict(module._task_state)
    raw_status = state.get("status") or "idle"
    reason = ""
    if raw_status == "idle":
        # 线程还没跑起来（毫秒级），这段时间对外仍算 running
        if time.time() - record["created_at"] < 30:
            raw_status = "running"
        else:
            return _empty_progress("failed", "下载线程未能启动")
    total = state.get("total") or 0
    if record["platform"] == "bilibili":
        completed = state.get("downloaded_count") or 0
        current = state.get("current_title") or ""
    else:
        completed = state.get("downloaded_count") or 0
        current = state.get("current_title") or ""
    failed = state.get("failed_count") or 0
    return _progress(
        status=_map_status(raw_status),
        total=total,
        completed=completed,
        failed=failed,
        skipped=0,
        current=current,
        reason=reason,
    )


def _map_status(raw) -> str:
    raw = (raw or "").strip().lower()
    if raw in ("completed", "success", "done"):
        return "completed"
    if raw in ("failed", "error"):
        return "failed"
    if raw in ("cancelled", "canceled", "cancelling"):
        return "cancelled"
    if raw in ("idle", ""):
        return "idle"
    return "running"


def _progress(status, total=0, completed=0, failed=0, skipped=0, current="", reason="") -> dict:
    return {
        "status": status,
        "total": int(total or 0),
        "completed": int(completed or 0),
        "failed": int(failed or 0),
        "skipped": int(skipped or 0),
        "current": current or "",
        "reason": reason or "",
    }


def _empty_progress(status, reason="") -> dict:
    return _progress(status=status, reason=reason)


# ---------------------------------------------------------------- 终态扫描

def _maybe_scan(record: dict, progress: dict) -> None:
    """下载进入终态后自动扫该平台媒体根，只做一次。"""
    if progress["status"] not in TERMINAL_STATUSES:
        return
    with _config_lock:
        if record.get("scan_requested"):
            return
        record["scan_requested"] = True
        root = record["root"]
        platform = record["platform"]
        task_id = record["task_id"]
        if _active.get(platform) == task_id:
            _active.pop(platform, None)

    job_id, error = _trigger_scan(root)
    with _config_lock:
        if job_id:
            record["scan_job_id"] = job_id
            progress["scan_job_id"] = job_id
        if error:
            record["scan_error"] = error
            progress["scan_error"] = error


def _probe_media_health(port: int) -> bool:
    """直接探媒体服务的健康端点，绕开宿主状态机。

    宿主可能复用了非它托管的进程（没有 _external_pid），此时 status() 里的
    state 会长时间停在 starting；真实判据只能是端口上答不答 200。
    """
    try:
        resp = requests.get(f"http://127.0.0.1:{int(port)}/api/health", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def _trigger_scan(root: str):
    """向媒体子服务提交扫描；返回 (job_id, error)。"""
    provider = _host_provider
    if provider is None:
        return None, "媒体服务未配置，未触发入库"
    try:
        host = provider()
    except Exception as exc:
        return None, f"媒体服务不可用：{str(exc)[:120]}"
    if host is None:
        return None, "媒体服务未启动，未触发入库"

    try:
        info = host.status() or {}
    except Exception as exc:
        return None, f"媒体服务状态获取失败：{str(exc)[:120]}"
    state = info.get("state")
    port = info.get("port") or getattr(host, "port", None)
    if not port:
        return None, "媒体服务端口未知，未触发入库"
    if not info.get("ready") or state != "running":
        # 宿主状态机可能滞后于真实服务（例如复用了非它托管的进程，
        # 或启动握手尚未走完）。真正的判据是端口上答不答得出健康检查，
        # 不能因为状态字符串是 starting 就放弃入库。
        if not _probe_media_health(port):
            return None, f"媒体服务未就绪（{state or 'unknown'}），未触发入库"
    try:
        resp = requests.post(
            f"http://127.0.0.1:{port}/api/media/scan",
            json={"root": root},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as exc:
        return None, f"扫描提交失败：{str(exc)[:160]}"
    job_id = str(data.get("id") or "").strip()
    if not job_id:
        return None, "扫描提交失败：响应缺少 job id"
    return job_id, ""


# ---------------------------------------------------------------- 任务管理

def _task_payload(record: dict, progress: Optional[dict] = None) -> dict:
    if progress is None:
        progress = _native_progress(record)
    payload = dict(progress)
    payload.update({
        "task_id": record["task_id"],
        "platform": record["platform"],
        "root": record["root"],
        "requested": record["requested"],
        "account_id": record.get("account_id") or "",
        "native_task_id": record.get("native_task_id") or "",
        "account_name": record.get("account_name") or "",
        "note": record.get("note") or "",
        "scan_job_id": record.get("scan_job_id") or "",
        "scan_error": record.get("scan_error") or "",
    })
    return payload


def _reconcile_active() -> None:
    """回收已终结或超时的活跃槽，避免页面关掉后平台被永久锁死。"""
    with _config_lock:
        items = list(_active.items())
    for platform, task_id in items:
        with _config_lock:
            record = _tasks.get(task_id)
        stale = record is None or time.time() - (record.get("created_at") or 0) > ACTIVE_TTL_SECONDS
        if not stale:
            try:
                stale = _native_progress(record)["status"] in TERMINAL_STATUSES
            except Exception:
                stale = False
        if stale:
            with _config_lock:
                if _active.get(platform) == task_id:
                    _active.pop(platform, None)


def _drop_task(platform: str, task_id: str) -> None:
    with _config_lock:
        _tasks.pop(task_id, None)
        if _active.get(platform) == task_id:
            _active.pop(platform, None)


def _prune_tasks() -> None:
    """只保留最近 24 小时的任务记录。"""
    cutoff = time.time() - 24 * 3600
    with _config_lock:
        for task_id, record in list(_tasks.items()):
            if (record.get("created_at") or 0) < cutoff:
                _tasks.pop(task_id, None)
        for platform, task_id in list(_active.items()):
            if task_id not in _tasks:
                _active.pop(platform, None)


def _next_task_id(platform: str) -> str:
    return f"arc_{platform}_{int(time.time() * 1000)}_{len(_tasks)}"


def _normalize_limit(limit) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT
    if value < 1 or value > MAX_LIMIT:
        raise ArchiveError(f"篇数需在 1–{MAX_LIMIT} 之间", 400)
    return value


def _ensure_platform(platform: str) -> None:
    platform = (platform or "").strip()
    if not platform:
        raise ArchiveError("缺少 platform 参数", 400)
    with _config_lock:
        enabled = _platforms
    if platform not in enabled:
        raise ArchiveError(f"平台未启用：{platform}", 400)


def _xhs_profile_url(user_id: str) -> str:
    return f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else ""
