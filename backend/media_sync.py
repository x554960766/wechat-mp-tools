"""wmp 与旁挂媒体服务之间的粘合层。

两件事：

1. ``normalize_article_url``：把公众号文章 URL 压成稳定的关联键，写进
   ``metadata.json``，供媒体索引把「一张图」挂回「哪篇文章」。
2. ``notify_scan``：文章落盘后通知媒体服务增量扫描。宿主进程里媒体服务
   是子进程，可能没装、可能正在重启；任何失败都必须静默吞掉，下载链路
   不能因为媒体服务缺席而变慢或报错。

扫描请求按 5 秒防抖合并：批量下载时连续落盘几百篇，只在最后安静下来后
触发一次扫描，避免把子进程的扫描队列打满。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import requests

logger = logging.getLogger("backend.media_sync")

# media_host 注入给子进程的媒体根 ID，见 MediaHost._media_root_env
SCAN_ROOT = "articles-full"
DEBOUNCE_SECONDS = 5.0
_SCAN_TIMEOUT = 5.0

_host_provider: Callable[[], object] | None = None
_cond = threading.Condition()
_pending = False
_stop = False
_worker: threading.Thread | None = None


def set_host_provider(provider: Callable[[], object] | None) -> None:
    """注册「取当前 MediaHost」的回调。

    downloader 在下载线程里运行，拿不到 Flask 请求上下文，所以用模块级
    回调而不是 current_app。
    """
    global _host_provider
    _host_provider = provider


def normalize_article_url(url: str) -> str:
    """把公众号文章 URL 归一为关联键。

    优先级：``biz:mid:idx``（微信文章三元组，同文不同链接也稳定）→
    ``sn:<sn>``（短链只有 sn 时）→ ``url:<规范化后的 URL>``（兜底）。

    不带 ``.md`` 之类的项目约定，返回值直接写进 metadata.json 和索引表。
    """
    if not isinstance(url, str):
        return ""
    raw = url.strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    query = parse_qs(parts.query)
    # 微信有时把参数放在 fragment 里（#wechat_redirect 之外的情况少见，仍兼容）
    if parts.fragment and "=" in parts.fragment:
        for key, values in parse_qs(parts.fragment).items():
            query.setdefault(key, values)

    def _first(name: str) -> str:
        values = query.get(name) or []
        return values[0].strip() if values else ""

    biz, mid, idx = _first("__biz"), _first("mid"), _first("idx")
    if biz and mid and idx:
        return f"{biz}:{mid}:{idx}"

    sn = _first("sn")
    path = parts.path or ""
    if not sn and path.startswith("/s/"):
        sn = path[3:].split("/")[0]
    if sn:
        return f"sn:{sn}"

    # 兜底：去 fragment、去跟踪参数、统一小写，保证同一篇文章的多种写法能折叠。
    kept = {
        key: values
        for key, values in query.items()
        if key in ("__biz", "mid", "idx", "sn")
    }
    base = f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}".rstrip("/")
    if kept:
        tail = "&".join(f"{key}={values[0]}" for key, values in sorted(kept.items()))
        base = f"{base}?{tail}"
    return f"url:{base.lower()}"


def article_id_for_key(article_key: str) -> str:
    """与 media_service 索引侧的 article_id 保持同一算法（sha256 十六进制）。"""
    import hashlib

    return hashlib.sha256(article_key.encode("utf-8")).hexdigest()


def _bind_host(host: object) -> None:
    """由 init_blueprint 调用；避免 app.py / app_mp.py 各自记一遍。"""
    set_host_provider(lambda: host)


def notify_scan(reason: str = "") -> None:
    """请求一次（防抖后的）增量扫描；永不抛异常。"""
    global _pending, _worker
    try:
        with _cond:
            _pending = True
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(
                    target=_scan_worker, name="media-scan-notifier", daemon=True
                )
                _worker.start()
            _cond.notify()
        if reason:
            logger.debug("媒体扫描已排队：%s", reason)
    except Exception:  # noqa: BLE001 - 通知失败绝不影响下载
        logger.debug("媒体扫描通知失败", exc_info=True)


def flush_pending(timeout: float = DEBOUNCE_SECONDS + 5.0) -> bool:
    """测试用：立刻放弃防抖窗口并同步触发一次扫描。"""
    global _pending
    with _cond:
        had = _pending
        _pending = False
        _cond.notify_all()
    if not had:
        return False
    return _trigger_scan()


def stop_notifier() -> None:
    """测试用：停掉后台线程，避免进程退出前残留。"""
    global _stop, _worker
    with _cond:
        _stop = True
        _cond.notify_all()
    if _worker is not None:
        _worker.join(timeout=2)
        _worker = None
    # 复位：测试与热重载会反复启停通知线程，_stop 不能残留。
    with _cond:
        _stop = False


def _scan_worker() -> None:
    global _pending
    while True:
        with _cond:
            while not _pending and not _stop:
                _cond.wait()
            if _stop and not _pending:
                return
            _pending = False
        # 从最后一次通知起再等满一个防抖窗口；期间又来通知就顺延。
        deadline = time.monotonic() + DEBOUNCE_SECONDS
        while True:
            with _cond:
                if _pending:
                    _pending = False
                    deadline = time.monotonic() + DEBOUNCE_SECONDS
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                _cond.wait(remaining)
        # 触发失败不能把通知线程带走：否则第一次媒体服务崩溃后就再也不会扫描。
        try:
            _trigger_scan()
        except Exception:  # noqa: BLE001
            logger.debug("媒体扫描触发异常", exc_info=True)


def _trigger_scan() -> bool:
    """向就绪的媒体子进程投递扫描任务；不可用时直接放弃。"""
    provider = _host_provider
    if provider is None:
        return False
    try:
        host = provider()
    except Exception:  # noqa: BLE001
        return False
    if host is None:
        return False
    try:
        status = host.status()
        if not status.get("ready") or status.get("state") != "running":
            logger.debug("媒体服务未就绪，跳过扫描：%s", status.get("state"))
            return False
        resp = requests.post(
            f"http://127.0.0.1:{host.port}/api/media/scan",
            json={"root": SCAN_ROOT},
            timeout=_SCAN_TIMEOUT,
        )
        if resp.status_code not in (200, 202):
            logger.info("媒体扫描请求被拒绝：%s %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception:  # noqa: BLE001 - 子进程崩溃/端口不可达都属于正常缺席
        logger.debug("媒体扫描触发失败", exc_info=True)
        return False


def scan_root_id() -> str:
    """当前注入给子进程的扫描根 ID，测试与前端提示共用。"""
    return SCAN_ROOT


def dumps_compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
