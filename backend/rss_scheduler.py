"""
RSS 自动订阅调度模块
后台定时抓取已订阅公众号的最新文章，供 RSS Feed 输出
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor
import logging
import base64
import binascii
import json
import random
import requests
from pathlib import Path

from backend.config import DATA_DIR, load_json, save_json

logger = logging.getLogger(__name__)

RSS_SUBSCRIPTIONS_FILE = DATA_DIR / "rss_subscriptions.json"
RSS_ARTICLES_FILE = DATA_DIR / "rss_articles.json"
RSS_UPLOAD_LOG_FILE = DATA_DIR / "rss_upload_log.json"

# 每个公众号 RSS 最多保留的文章数
MAX_ARTICLES_PER_ACCOUNT = 200

# 单次上传最多携带的文章数（超出部分留待下一轮）
UPLOAD_BATCH_LIMIT = 100
# 上传子批次的初始大小（自适应：遇到数据量过大的失败时自动减半，最小为 1）
UPLOAD_INITIAL_BATCH_SIZE = 20
# 单篇连续失败达到该次数即隔离（quarantine），不再阻塞其他文章
UPLOAD_QUARANTINE_THRESHOLD = 3
# 上传审计日志最多保留的记录条数
MAX_UPLOAD_LOG = 100

# 并发抓取的最大线程数（I/O 密集型，线程大部分时间在等网络响应和延时）
MAX_FETCH_WORKERS = 50

# 全局兜底上传的间隔（分钟）：扫描所有账号的待传文章，兜住因账号不属于
# 当前启用订阅（改名/退订/禁用/手动下载）而永远不会被按账号触发上传的文章
GLOBAL_UPLOAD_SWEEP_MINUTES = 30


class RssScheduler:
    """RSS 自动抓取调度器"""

    def __init__(self):
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()          # 保护 rss_subscriptions.json
        self._history_lock = threading.Lock()  # 保护 DOWNLOAD_HISTORY_FILE 读改写
        self._executor = ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS, thread_name_prefix="rss-fetch")
        self._fetching: set[str] = set()       # 正在抓取中的 fakeid，防重复提交
        self._fetching_lock = threading.Lock() # 保护 _fetching 集合
        self._last_upload_response: str | None = None  # 最近一次网关响应摘要（排查静默丢弃用）

    # ── 订阅管理 ──────────────────────────────────────────

    def get_subscriptions(self) -> list:
        return load_json(RSS_SUBSCRIPTIONS_FILE, [])

    def _save_subscriptions(self, subs: list):
        save_json(RSS_SUBSCRIPTIONS_FILE, subs)

    def get_subscription(self, fakeid: str) -> dict | None:
        for sub in self.get_subscriptions():
            if sub.get("fakeid") == fakeid:
                return sub
        return None

    def subscribe(self, fakeid: str, nickname: str, interval_minutes: int = 60) -> dict:
        with self._lock:
            interval_minutes = self._normalize_interval_minutes(interval_minutes)
            subs = self.get_subscriptions()
            for sub in subs:
                if sub.get("fakeid") == fakeid:
                    old_interval = self._normalize_interval_minutes(sub.get("interval_minutes", 60))
                    sub["nickname"] = nickname
                    sub["interval_minutes"] = interval_minutes
                    sub["enabled"] = True
                    if old_interval != interval_minutes or not self._safe_float(sub.get("next_fetch_time"), 0):
                        self._schedule_next_fetch(sub)
                    self._save_subscriptions(subs)
                    return sub

            new_sub = {
                "fakeid": fakeid,
                "nickname": nickname,
                "interval_minutes": interval_minutes,
                "enabled": True,
                "last_fetch_time": 0,
                "last_fetch_count": 0,
                "last_error": None,
                "total_articles": 0,
                "last_upload_time": 0,
                "last_upload_count": 0,
                "last_upload_error": None,
                "pending_upload_count": 0,
                "quarantined_count": 0,
                "last_upload_attempted": False,
                "last_upload_disabled": False,
            }
            self._schedule_next_fetch(new_sub)
            subs.append(new_sub)
            self._save_subscriptions(subs)
            return new_sub

    def unsubscribe(self, fakeid: str) -> bool:
        with self._lock:
            subs = self.get_subscriptions()
            new_subs = [s for s in subs if s.get("fakeid") != fakeid]
            if len(new_subs) == len(subs):
                return False
            self._save_subscriptions(new_subs)
            return True

    # ── 文章存储 ──────────────────────────────────────────

    def get_articles(self, nickname: str) -> list:
        all_articles = load_json(RSS_ARTICLES_FILE, {})
        return all_articles.get(nickname, [])

    def _save_articles(self, nickname: str, articles: list):
        all_articles = load_json(RSS_ARTICLES_FILE, {})
        all_articles[nickname] = articles[:MAX_ARTICLES_PER_ACCOUNT]
        save_json(RSS_ARTICLES_FILE, all_articles)

    # ── 新文章上传 ────────────────────────────────────────

    @staticmethod
    def _content_looks_base64(value: str) -> bool:
        if not isinstance(value, str) or not value or len(value) % 4 != 0:
            return False
        try:
            base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
            return True
        except (binascii.Error, UnicodeError, ValueError):
            return False

    def _normalize_upload_article(self, article: dict, encode_content: bool = True) -> dict | None:
        if not isinstance(article, dict):
            return None
        normalized = dict(article)
        normalized["source"] = normalized.get("source") or normalized.get("author") or ""
        normalized["title"] = normalized.get("title") or ""
        normalized["url"] = normalized.get("url") or normalized.get("link") or ""
        if not normalized["title"] or not normalized["url"]:
            return None

        content = normalized.get("content")
        content_encoding = normalized.pop("content_encoding", None)
        content_is_base64 = (
            bool(normalized.pop("_rss_content_base64", False))
            or content_encoding == "base64"
            or self._content_looks_base64(content)
        )
        if isinstance(content, str) and content:
            if encode_content:
                if not content_is_base64:
                    normalized["content"] = base64.b64encode(content.encode("utf-8")).decode("ascii")
            elif content_is_base64:
                normalized["_rss_content_base64"] = True
        return normalized

    def _article_upload_key(self, article: dict) -> str:
        return article.get("url") or article.get("link") or json.dumps(article, ensure_ascii=False, sort_keys=True)

    def _dedupe_upload_articles(self, articles: list, encode_content: bool = True) -> list:
        seen = set()
        deduped = []
        for article in articles:
            normalized = self._normalize_upload_article(article, encode_content=encode_content)
            if not normalized:
                continue
            key = self._article_upload_key(normalized)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return deduped

    def _read_downloaded_article_data(self, article_dir: str, fallback: dict) -> dict | None:
        data_path = Path(article_dir) / "data.json"
        data = load_json(data_path, {})
        if isinstance(data, dict) and data:
            return data
        if fallback.get("title") and fallback.get("url"):
            return fallback
        return None

    # 以下载历史为唯一事实来源：候选 = success 且未上传、未隔离的条目

    def _collect_unuploaded(self, history: list, account: str | None = None) -> list:
        """从下载历史中挑出待上传的条目（直接返回 history 中的对象引用，便于原地标记）"""
        out = []
        for item in history:
            if not isinstance(item, dict):
                continue
            if not item.get("success") or item.get("uploaded") or item.get("upload_quarantined"):
                continue
            if not item.get("link"):
                continue
            if account is not None and item.get("account") != account:
                continue
            out.append(item)
            if len(out) >= UPLOAD_BATCH_LIMIT:
                break
        return out

    def count_pending(self, history: list, account: str | None = None) -> int:
        return sum(
            1 for it in history
            if isinstance(it, dict) and it.get("success") and not it.get("uploaded")
            and not it.get("upload_quarantined") and it.get("link")
            and (account is None or it.get("account") == account)
        )

    def count_quarantined(self, history: list, account: str | None = None) -> int:
        return sum(
            1 for it in history
            if isinstance(it, dict) and it.get("upload_quarantined")
            and (account is None or it.get("account") == account)
        )

    def _payload_for_item(self, item: dict) -> dict | None:
        """读取该条目对应的文章正文，构造可上传的（已编码）article"""
        data = self._read_downloaded_article_data(
            item.get("path") or "",
            {"source": item.get("account", ""), "title": item.get("title", ""), "url": item.get("link", "")},
        )
        if not data:
            return None
        return self._normalize_upload_article(data, encode_content=True)

    def _post_articles(self, articles: list, settings: dict) -> tuple[bool, str | None]:
        """POST 一批文章到远端网关，返回 (是否成功, 错误信息)"""
        from backend.config import get_proxies_dict

        upload_url = (settings.get("rss_upload_url") or "").strip()
        payload = {
            "articles": articles,
            "deviceId": settings.get("device_id") or "公众号_caiji100",
        }
        self._last_upload_response = None
        try:
            resp = requests.post(
                upload_url, json=payload,
                headers={"Content-Type": "application/json"},
                proxies=get_proxies_dict(), timeout=30,
            )
            # 记录网关响应（状态码+正文），用于排查「已标记上传但服务器没有」的静默丢弃
            self._last_upload_response = f"HTTP {resp.status_code} | {(resp.text or '')[:500]}"
            logger.info("RSS 上传响应[%d篇]: %s", len(articles), self._last_upload_response[:300])
            resp.raise_for_status()
            try:
                data = resp.json()
                if isinstance(data, dict) and data.get("success") is False:
                    return False, (data.get("message") or data.get("error") or "远端接口返回失败")
            except ValueError:
                pass
            return True, None
        except Exception as e:
            # 已拿到响应体（如 4xx/5xx）则保留它，否则记录连接异常
            if self._last_upload_response is None:
                self._last_upload_response = f"ERROR | {e}"
            return False, str(e)

    @staticmethod
    def _mark_uploaded(item: dict):
        item["uploaded"] = True
        item["upload_time"] = time.time()
        item["upload_error"] = None
        item["upload_quarantined"] = False

    @staticmethod
    def _mark_attempt(item: dict, error: str | None, allow_quarantine: bool = False):
        item["upload_attempts"] = item.get("upload_attempts", 0) + 1
        item["upload_error"] = error
        if allow_quarantine and item["upload_attempts"] >= UPLOAD_QUARANTINE_THRESHOLD:
            item["upload_quarantined"] = True

    @staticmethod
    def _is_size_related_error(error: str | None) -> bool:
        """判断上传失败是否由数据量过大导致（仅限真正的体积类错误）。

        注意：超时/连接重置等瞬时网络错误**不**计入此处——它们是可重试的，
        若误判为「数据过大」会在单篇批次时被永久隔离，造成文章漏上传。
        """
        if not error:
            return False
        err_lower = error.lower()
        indicators = [
            "413", "too large", "payload too large", "entity too large",
            "request entity too large", "content length",
        ]
        return any(ind in err_lower for ind in indicators)

    def _append_upload_log(self, record: dict):
        log = load_json(RSS_UPLOAD_LOG_FILE, [])
        if not isinstance(log, list):
            log = []
        log.insert(0, record)
        save_json(RSS_UPLOAD_LOG_FILE, log[:MAX_UPLOAD_LOG])

    def get_upload_log(self, limit: int = 50, account: str | None = None) -> list:
        log = load_json(RSS_UPLOAD_LOG_FILE, [])
        if not isinstance(log, list):
            return []
        if account:
            log = [
                r for r in log
                if isinstance(r, dict) and (
                    r.get("account") == account
                    or any(i.get("account") == account for i in r.get("items", []))
                )
            ]
        return log[:max(1, limit)]

    def _run_upload(self, history: list, trigger: str = "auto", account: str | None = None) -> dict:
        """上传编排器：以 history 为事实来源，原地标记上传/重试/隔离，并写审计日志。

        注意：本方法只修改 history 中的对象，不负责保存，由调用方统一保存。
        """
        from backend.config import get_settings

        settings = get_settings()
        result = {
            "success": True, "attempted": False, "count": 0,
            "succeeded": 0, "failed": 0, "quarantined": 0,
            "pending_count": self.count_pending(history, account), "error": None, "disabled": False,
        }

        if not settings.get("rss_upload_enabled", False):
            result["disabled"] = True
            return result

        if not (settings.get("rss_upload_url") or "").strip():
            result["success"] = False
            result["error"] = "RSS 上传接口地址未配置"
            return result

        candidates = self._collect_unuploaded(history, account)
        if not candidates:
            result["pending_count"] = 0
            return result

        # 构造上传载荷，无法读取正文的视为坏数据（计为一次尝试，达阈值即隔离）
        built = []
        for item in candidates:
            art = self._payload_for_item(item)
            if art:
                built.append((item, art))
            else:
                self._mark_attempt(item, "文章内容缺失或无法读取", allow_quarantine=True)

        succeeded = 0
        quarantined = 0
        last_error = None

        if built:
            # 自适应分批上传：初始批次 UPLOAD_INITIAL_BATCH_SIZE，失败时自动减半
            batch_size = min(UPLOAD_INITIAL_BATCH_SIZE, len(built))
            pos = 0

            while pos < len(built):
                chunk = built[pos:pos + batch_size]
                ok, err = self._post_articles([art for _, art in chunk], settings)

                if ok:
                    for item, _ in chunk:
                        self._mark_uploaded(item)
                    succeeded += len(chunk)
                    pos += len(chunk)
                else:
                    last_error = err
                    if batch_size > 1 and self._is_size_related_error(err):
                        # 疑似数据量过大，自动缩小批次并从当前位置重试
                        new_size = max(1, batch_size // 2)
                        logger.info("RSS 上传批次失败(疑似数据过大)，自动缩小批次: %d → %d", batch_size, new_size)
                        batch_size = new_size
                        continue
                    # 已缩至单条或非数据量问题 → 标记/隔离并跳过
                    for item, _ in chunk:
                        if batch_size == 1 and self._is_size_related_error(err):
                            # 单条仍因数据过大失败 → 判定为坏文章，立即隔离跳过
                            item["upload_quarantined"] = True
                            item["upload_error"] = err
                            logger.info("RSS 上传: 单篇文章过大已自动跳过 [%s]", item.get("title", ""))
                        else:
                            self._mark_attempt(item, err, allow_quarantine=False)
                    pos += len(chunk)
                    # 跳过坏文章后恢复初始批次大小，继续高效上传剩余文章
                    if batch_size == 1:
                        batch_size = UPLOAD_INITIAL_BATCH_SIZE

            # 整批反复失败后逐篇隔离：定位坏文章，避免阻塞其他文章
            needs_quarantine = any(
                not item.get("uploaded") and item.get("upload_attempts", 0) >= UPLOAD_QUARANTINE_THRESHOLD
                for item, _ in built
            )
            if needs_quarantine:
                failed_pairs = [
                    (item, art) for item, art in built
                    if not item.get("uploaded") and not item.get("upload_quarantined")
                ]
                if failed_pairs:
                    singles = [(item, *self._post_articles([art], settings)) for item, art in failed_pairs]
                    any_ok = any(ok_i for _, ok_i, _ in singles)
                    for item, ok_i, err_i in singles:
                        if ok_i:
                            self._mark_uploaded(item)
                            succeeded += 1
                        elif any_ok and item.get("upload_attempts", 0) >= UPLOAD_QUARANTINE_THRESHOLD:
                            # 其他文章能上传、唯独这篇逐篇也失败 → 判定为坏文章并隔离
                            item["upload_quarantined"] = True
                            item["upload_error"] = err_i
                        else:
                            item["upload_error"] = err_i

        quarantined = sum(1 for item in candidates if item.get("upload_quarantined"))
        failed = len(candidates) - succeeded

        record = {
            "time": time.time(),
            "trigger": trigger,
            "account": account,
            "attempted": len(candidates),
            "succeeded": succeeded,
            "failed": failed,
            "quarantined": quarantined,
            "success": failed == 0,
            "error": last_error,
            "response": self._last_upload_response,
            "items": [
                {
                    "url": item.get("link", ""),
                    "title": item.get("title", ""),
                    "account": item.get("account", ""),
                    "ok": bool(item.get("uploaded")),
                }
                for item in candidates[:50]
            ],
        }
        self._append_upload_log(record)
        logger.info(
            "RSS 上传[%s]: 成功 %d / 共 %d，隔离 %d%s",
            trigger, succeeded, len(candidates), quarantined,
            f"，错误: {last_error}" if last_error else "",
        )

        result.update({
            "success": failed == 0,
            "attempted": True,
            "count": succeeded,
            "succeeded": succeeded,
            "failed": failed,
            "quarantined": quarantined,
            "pending_count": self.count_pending(history, account),
            "error": last_error,
        })
        return result

    def _revive_quarantined(self, history: list, account: str | None = None) -> int:
        """解除隔离：把（指定账号的）已隔离文章重置为可重试状态，返回恢复数量。

        隔离一旦发生便没有自动恢复出口，瞬时故障误隔离的文章会永久漏传；
        强制上传时调用本方法，给这些文章再来一次机会。
        """
        revived = 0
        for item in history:
            if not isinstance(item, dict) or not item.get("upload_quarantined"):
                continue
            if account is not None and item.get("account") != account:
                continue
            item["upload_quarantined"] = False
            item["upload_attempts"] = 0
            item["upload_error"] = None
            revived += 1
        return revived

    def force_upload_all(self, nickname: str) -> dict:
        """强制上传该公众号所有待上传的文章（含此前被隔离的，同步）"""
        from backend.config import load_json as _load_json, save_json as _save_json, DOWNLOAD_HISTORY_FILE

        with self._history_lock:
            history = _load_json(DOWNLOAD_HISTORY_FILE, [])
            revived = self._revive_quarantined(history, nickname)
            if revived:
                logger.info("RSS 强制上传[%s]: 解除隔离 %d 篇，重新尝试上传", nickname, revived)
            result = self._run_upload(history, trigger="manual", account=nickname)
            _save_json(DOWNLOAD_HISTORY_FILE, history)
        if revived:
            result["revived"] = revived
        return result

    def upload_all_pending(self, trigger: str = "sweep") -> dict:
        """全局兜底上传：扫描所有账号(account=None)的待传文章并上传。

        捞回那些 account 不属于当前启用订阅、因而永远不会被按账号触发上传的文章。
        """
        from backend.config import load_json as _load_json, save_json as _save_json, DOWNLOAD_HISTORY_FILE

        with self._history_lock:
            history = _load_json(DOWNLOAD_HISTORY_FILE, [])
            result = self._run_upload(history, trigger=trigger, account=None)
            _save_json(DOWNLOAD_HISTORY_FILE, history)
        return result

    def _global_upload_sweep(self):
        """周期性兜底上传（仅在上传开启且处于抓取窗口内时执行）"""
        from backend.config import get_settings

        if not get_settings().get("rss_upload_enabled", False):
            return
        if not self.is_in_fetch_window():
            return
        self.upload_all_pending(trigger="sweep")

    # ── 抓取逻辑 ──────────────────────────────────────────

    def submit_fetch(self, sub: dict) -> bool:
        """提交抓取任务到线程池（自动去重，同一公众号不会重复提交）"""
        fakeid = sub.get("fakeid", "")
        with self._fetching_lock:
            if fakeid in self._fetching:
                logger.debug("RSS 抓取跳过 [%s]: 已在抓取队列中", sub.get("nickname", fakeid))
                return False
            self._fetching.add(fakeid)
        self._executor.submit(self._fetch_wrapper, sub)
        return True

    def _fetch_wrapper(self, sub: dict):
        """线程池执行入口：执行抓取并在完成后清理 _fetching 状态"""
        try:
            self._fetch_for_account(sub)
        except Exception as e:
            logger.error("RSS 抓取线程异常 [%s]: %s", sub.get("nickname", ""), e)
        finally:
            with self._fetching_lock:
                self._fetching.discard(sub.get("fakeid", ""))

    def _fetch_for_account(self, sub: dict):
        """为单个订阅抓取最新文章并执行离线下载（由线程池调度）"""
        from backend.articles import _fetch_articles_page
        from backend.downloader import download_single_article
        from backend.config import load_json, save_json, DOWNLOAD_HISTORY_FILE, OUTPUT_DIR, get_settings

        fakeid = sub["fakeid"]
        nickname = sub["nickname"]

        last_error = None
        new_count = 0
        total_articles = sub.get("total_articles", 0)
        upload_result = None

        try:
            existing = self.get_articles(nickname)
            existing_links = {a.get("link") for a in existing}
            
            # 加载历史记录，用于去重和录入
            history = load_json(DOWNLOAD_HISTORY_FILE, [])
            history_links = {item.get("link") for item in history if isinstance(item, dict) and item.get("link")}

            new_articles = []
            begin = 0
            count = 10
            max_pages = 5 # 限制最大翻页数，防死循环
            
            try:
                for page_idx in range(max_pages):
                    page_articles, _total = _fetch_articles_page(fakeid, begin=begin, count=count)
                    if not page_articles:
                        break
                    
                    has_old_article = False
                    for art in page_articles:
                        link = art.get("link")
                        if link:
                            # 如果已经在 RSS 缓存或下载历史中，说明后面的文章都是已下载过的老文章了
                            if link in existing_links or link in history_links:
                                has_old_article = True
                            else:
                                new_articles.append(art)
                    
                    # 如果当前页中包含了已有的老文章，或者新文章总数已经太多了，就不需要再往后翻页了
                    if has_old_article or len(page_articles) < count:
                        break
                    
                    begin += count
                    # 翻页间稍作延时，避免被微信风控
                    time.sleep(1.5)

                if new_articles:
                    # 准备下载目录
                    out_dir = OUTPUT_DIR / nickname
                    out_dir.mkdir(parents=True, exist_ok=True)

                    settings = get_settings()
                    delay = settings.get("request_delay", 0.8)
                    max_retries = settings.get("max_retries", 3)

                    # 按时间升序排序（最旧的新文章先下载，保证顺序正确）
                    for i, art in enumerate(reversed(new_articles)):
                        link = art.get("link", "")
                        title = art.get("title", "")
                        if not link:
                            continue

                        # 尝试下载
                        success = False
                        downloaded_path = None
                        error_msg = ""
                        result = {}

                        for attempt in range(1, max_retries + 1):
                            try:
                                result = download_single_article(link, out_dir, title)
                                if result.get("success"):
                                    success = True
                                    downloaded_path = result.get("path")
                                    break
                                error_msg = result.get("error", "未知错误")
                            except Exception as e:
                                error_msg = str(e)
                            time.sleep(1)

                        # 寻找历史记录中是否已存在该链接
                        existing_history_item = None
                        for item in history:
                            if isinstance(item, dict) and item.get("link") == link:
                                existing_history_item = item
                                break

                        is_permanent = result.get("is_permanent", False) if isinstance(result, dict) else False

                        if existing_history_item:
                            # 只有当新结果成功，或者之前也是失败时，才更新（避免用失败覆盖成功）
                            if success or not existing_history_item.get("success"):
                                existing_history_item["success"] = success
                                existing_history_item["time"] = time.time()
                                existing_history_item["error"] = error_msg if not success else None
                                existing_history_item["path"] = downloaded_path
                                if result.get("cover_url"):
                                    existing_history_item["cover_url"] = result.get("cover_url")
                                if result.get("digest"):
                                    existing_history_item["digest"] = result.get("digest")
                                if result.get("publish_time"):
                                    existing_history_item["publish_time"] = result["publish_time"]
                        else:
                            existing_history_item = {
                                "title": result.get("title") or title,
                                "link": link,
                                "account": nickname,
                                "success": success,
                                "time": time.time(),
                                "error": error_msg if not success else None,
                                "path": downloaded_path,
                                "cover_url": result.get("cover_url") or art.get("cover", ""),
                                "digest": result.get("digest") or art.get("digest", ""),
                                "publish_time": result.get("publish_time") or art.get("update_time", int(time.time())),
                            }
                            history.append(existing_history_item)
                            history_links.add(link)

                        # 仅在下载成功，或该失败是永久性失败（如作者已删除/内容被屏蔽）时，才加入到订阅缓存列表中（防止重复重试）
                        if success or is_permanent:
                            rss_item = {
                                "title": result.get("title") or title,
                                "link": link,
                                "cover": result.get("cover_url") or art.get("cover", ""),
                                "digest": result.get("digest") or art.get("digest", ""),
                                "author": nickname,
                                "update_time": result.get("publish_time") or art.get("update_time", int(time.time())),
                                "path": downloaded_path or "",
                            }
                            existing.insert(0, rss_item)
                            new_count += 1

                        # 避免抓取频率过快，加入延迟
                        if i < len(new_articles) - 1:
                            time.sleep(delay)

            except PermissionError as e:
                last_error = "登录已过期"
                logger.warning("RSS 抓取跳过 [%s]: 登录已过期", nickname)
            except Exception as e:
                last_error = str(e)
                logger.warning("RSS 抓取失败 [%s]: %s", nickname, e)

            # 截断保留上限
            existing = existing[:MAX_ARTICLES_PER_ACCOUNT]
            self._save_articles(nickname, existing)
            
            # ── 提交阶段：加锁合并 + 上传 + 保存 ──
            _upload_fields = {"uploaded", "upload_time", "upload_error", "upload_quarantined", "upload_attempts"}
            with self._history_lock:
                # 重新读取磁盘版本，合并其他线程的并发修改
                disk_history = load_json(DOWNLOAD_HISTORY_FILE, [])
                disk_by_link = {
                    it["link"]: it for it in disk_history
                    if isinstance(it, dict) and it.get("link")
                }
                # 将本线程下载阶段的修改合并到磁盘版本中
                for item in history:
                    if not isinstance(item, dict) or not item.get("link"):
                        continue
                    link = item["link"]
                    if link in disk_by_link:
                        disk_item = disk_by_link[link]
                        # 取本线程的下载字段，保留磁盘上的上传状态（可能被其他线程更新）
                        for key, value in item.items():
                            if key not in _upload_fields:
                                disk_item[key] = value
                    else:
                        disk_history.append(item)
                history = disk_history

                total_articles = sum(
                    1 for item in history
                    if isinstance(item, dict) and item.get("account") == nickname and item.get("success")
                )
                # 以下载历史为准上传（本轮新文章 + 历史中所有未上传/未隔离的文章会一并重试）
                upload_result = self._run_upload(history, trigger="auto", account=nickname)
                save_json(DOWNLOAD_HISTORY_FILE, history)

        except Exception as outer_e:
            if not last_error:
                last_error = str(outer_e)
            logger.warning("RSS 抓取失败 [%s]: %s", nickname, outer_e)

        # 写入状态更新
        with self._lock:
            subs = self.get_subscriptions()
            for s in subs:
                if s.get("fakeid") == fakeid:
                    now = time.time()
                    s["last_fetch_time"] = now
                    s["last_fetch_count"] = new_count
                    s["last_error"] = last_error
                    s["total_articles"] = total_articles
                    self._schedule_next_fetch(s, now)
                    if upload_result:
                        if upload_result.get("attempted", False):
                            s["last_upload_time"] = time.time()
                            s["last_upload_count"] = upload_result.get("count", 0)
                        s["last_upload_error"] = upload_result.get("error")
                        # 以该账号在下载历史中的实际状态为准（单一事实来源）
                        s["pending_upload_count"] = self.count_pending(history, nickname)
                        s["quarantined_count"] = self.count_quarantined(history, nickname)
                        s["last_upload_attempted"] = upload_result.get("attempted", False)
                        s["last_upload_disabled"] = upload_result.get("disabled", False)
                    break
            self._save_subscriptions(subs)

        if new_count > 0:
            logger.info("RSS 抓取并下载 [%s]: 新增 %d 篇文章", nickname, new_count)

    # ── 调度循环 ──────────────────────────────────────────

    def _run_loop(self):
        logger.info("RSS 调度器已启动")
        last_sweep = 0.0
        while not self._stop_event.is_set():
            try:
                self._tick()
                now = time.time()
                if now - last_sweep >= GLOBAL_UPLOAD_SWEEP_MINUTES * 60:
                    last_sweep = now
                    self._global_upload_sweep()
            except Exception as e:
                logger.error("RSS 调度器异常: %s", e)
            # 每 30 秒检查一次是否有订阅需要执行
            self._stop_event.wait(30)
        logger.info("RSS 调度器已停止")

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value, default: float = 0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _normalize_interval_minutes(cls, value) -> int:
        return max(15, cls._safe_int(value, 60))

    @classmethod
    def _get_interval_range_minutes(cls, interval_minutes: int) -> tuple[int, int]:
        interval = cls._normalize_interval_minutes(interval_minutes)
        jitter = max(5, round(interval * 0.25))
        return max(5, interval - jitter), interval + jitter

    def _schedule_next_fetch(self, sub: dict, start_time: float | None = None) -> float:
        min_minutes, max_minutes = self._get_interval_range_minutes(sub.get("interval_minutes", 60))
        next_fetch_time = (start_time or time.time()) + random.randint(min_minutes * 60, max_minutes * 60)
        sub["next_fetch_time"] = next_fetch_time
        return next_fetch_time

    def _ensure_next_fetch_time(self, sub: dict, now: float) -> bool:
        if self._safe_float(sub.get("next_fetch_time"), 0) > 0:
            return False

        last_fetch = self._safe_float(sub.get("last_fetch_time"), 0)
        self._schedule_next_fetch(sub, last_fetch or now)
        return True

    def _get_fetch_window_minutes(self) -> tuple[int, int]:
        from backend.config import get_settings

        settings = get_settings()
        start_hour = max(0, min(23, self._safe_int(settings.get("rss_start_hour", 0), 0)))
        start_minute = max(0, min(59, self._safe_int(settings.get("rss_start_minute", 0), 0)))
        end_hour = max(0, min(24, self._safe_int(settings.get("rss_end_hour", 24), 24)))
        end_minute = 0 if end_hour == 24 else max(0, min(59, self._safe_int(settings.get("rss_end_minute", 0), 0)))
        return start_hour * 60 + start_minute, end_hour * 60 + end_minute

    def is_in_fetch_window(self, now=None) -> bool:
        import datetime

        current = now or datetime.datetime.now()
        start_minute, end_minute = self._get_fetch_window_minutes()
        current_minute = current.hour * 60 + current.minute

        if start_minute <= end_minute:
            return start_minute <= current_minute < end_minute
        return current_minute >= start_minute or current_minute < end_minute

    def _tick(self):
        if not self.is_in_fetch_window():
            return

        now = time.time()
        due_subs = []

        with self._lock:
            subs = self.get_subscriptions()
            changed = False

            for sub in subs:
                if not sub.get("enabled"):
                    continue

                interval_minutes = self._normalize_interval_minutes(sub.get("interval_minutes", 60))
                if sub.get("interval_minutes") != interval_minutes:
                    sub["interval_minutes"] = interval_minutes
                    changed = True

                if self._ensure_next_fetch_time(sub, now):
                    changed = True

                if now >= self._safe_float(sub.get("next_fetch_time"), 0):
                    due_subs.append(dict(sub))

            if changed:
                self._save_subscriptions(subs)

        for sub in due_subs:
            self.submit_fetch(sub)


    # ── 启停控制 ──────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="rss-scheduler")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._executor.shutdown(wait=False)


# 全局单例
rss_scheduler = RssScheduler()
