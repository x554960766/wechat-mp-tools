"""
RSS 自动订阅调度模块
后台定时抓取已订阅公众号的最新文章，供 RSS Feed 输出
"""

import time
import threading
import logging
import base64
import binascii
import json
import random
import requests
from pathlib import Path

from backend.config import DATA_DIR, DEFAULT_RSS_UPLOAD_URL, load_json, save_json

logger = logging.getLogger(__name__)

RSS_SUBSCRIPTIONS_FILE = DATA_DIR / "rss_subscriptions.json"
RSS_ARTICLES_FILE = DATA_DIR / "rss_articles.json"
RSS_UPLOAD_PENDING_FILE = DATA_DIR / "rss_upload_pending.json"
RSS_UPLOAD_URL = DEFAULT_RSS_UPLOAD_URL

# 每个公众号 RSS 最多保留的文章数
MAX_ARTICLES_PER_ACCOUNT = 200


class RssScheduler:
    """RSS 自动抓取调度器"""

    def __init__(self):
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

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

    def _load_pending_upload_articles(self) -> list:
        pending = load_json(RSS_UPLOAD_PENDING_FILE, [])
        return pending if isinstance(pending, list) else []

    def _save_pending_upload_articles(self, articles: list):
        save_json(RSS_UPLOAD_PENDING_FILE, self._dedupe_upload_articles(articles, encode_content=False))

    def _read_downloaded_article_data(self, article_dir: str, fallback: dict) -> dict | None:
        data_path = Path(article_dir) / "data.json"
        data = load_json(data_path, {})
        if isinstance(data, dict) and data:
            return data
        if fallback.get("title") and fallback.get("url"):
            return fallback
        return None

    def _upload_new_articles(self, new_articles: list) -> dict:
        from backend.config import get_settings, get_proxies_dict

        settings = get_settings()
        pending_articles = self._dedupe_upload_articles(
            self._load_pending_upload_articles(),
            encode_content=False,
        )
        if not settings.get("rss_upload_enabled", False):
            return {
                "success": True,
                "attempted": False,
                "count": 0,
                "pending_count": len(pending_articles),
                "error": None,
                "disabled": True,
            }

        pending_to_save = self._dedupe_upload_articles(
            pending_articles + new_articles,
            encode_content=False,
        )
        upload_articles = self._dedupe_upload_articles(pending_to_save)
        if not upload_articles:
            return {
                "success": True,
                "attempted": False,
                "count": 0,
                "pending_count": 0,
                "error": None,
                "disabled": False,
            }

        upload_url = (settings.get("rss_upload_url") or "").strip()
        if not upload_url:
            return self._defer_upload_articles(new_articles, "RSS 上传接口地址未配置")

        payload = {
            "articles": upload_articles,
            "deviceId": settings.get("device_id") or "公众号_caiji100",
        }

        try:
            resp = requests.post(
                upload_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                proxies=get_proxies_dict(),
                timeout=30,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
                if isinstance(data, dict) and data.get("success") is False:
                    raise RuntimeError(data.get("message") or data.get("error") or "远端接口返回失败")
            except ValueError:
                pass
            self._save_pending_upload_articles([])
            logger.info("RSS 新文章上传成功: %d 篇", len(upload_articles))
            return {
                "success": True,
                "attempted": True,
                "count": len(upload_articles),
                "pending_count": 0,
                "error": None,
                "disabled": False,
            }
        except Exception as e:
            self._save_pending_upload_articles(pending_to_save)
            logger.warning("RSS 新文章上传失败，已加入待重试队列: %s", e)
            return {
                "success": False,
                "attempted": True,
                "count": 0,
                "pending_count": len(pending_to_save),
                "error": str(e),
                "disabled": False,
            }

    def _defer_upload_articles(self, new_articles: list, reason: str) -> dict:
        upload_articles = self._dedupe_upload_articles(
            self._load_pending_upload_articles() + new_articles,
            encode_content=False,
        )
        self._save_pending_upload_articles(upload_articles)
        logger.info("RSS 新文章上传暂缓: %s，待上传 %d 篇", reason, len(upload_articles))
        return {
            "success": False,
            "attempted": False,
            "count": 0,
            "pending_count": len(upload_articles),
            "error": reason,
            "disabled": False,
        }

    # ── 抓取逻辑 ──────────────────────────────────────────

    def _fetch_for_account(self, sub: dict):
        """为单个订阅抓取最新文章并执行离线下载"""
        from backend.articles import _fetch_articles_page
        from backend.downloader import download_single_article
        from backend.config import load_json, save_json, DOWNLOAD_HISTORY_FILE, OUTPUT_DIR, get_settings

        fakeid = sub["fakeid"]
        nickname = sub["nickname"]

        last_error = None
        new_count = 0
        total_articles = 0
        upload_articles = []
        all_downloads_success = True
        upload_result = None

        try:
            articles, _total = _fetch_articles_page(fakeid, begin=0, count=10)
            existing = self.get_articles(nickname)
            existing_links = {a.get("link") for a in existing}

            # 过滤出真正需要下载的新文章（最新拉取的 10 篇里，不在我们 RSS 缓存列表中的文章）
            new_articles = []
            for art in articles:
                if art.get("link") and art["link"] not in existing_links:
                    new_articles.append(art)

            if new_articles:
                # 准备下载目录
                out_dir = OUTPUT_DIR / nickname
                out_dir.mkdir(parents=True, exist_ok=True)

                # 加载历史记录，用于去重和录入
                history = load_json(DOWNLOAD_HISTORY_FILE, [])
                history_links = {item.get("link") for item in history if isinstance(item, dict) and item.get("link")}

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
                    if not success and not is_permanent:
                        all_downloads_success = False

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

                    if success and downloaded_path:
                        article_data = self._read_downloaded_article_data(
                            downloaded_path,
                            {
                                "source": nickname,
                                "title": result.get("title") or title,
                                "url": link,
                            },
                        )
                        if article_data:
                            upload_articles.append(article_data)

                    # 避免抓取频率过快，加入延迟
                    if i < len(new_articles) - 1:
                        time.sleep(delay)

                # 保存历史记录
                save_json(DOWNLOAD_HISTORY_FILE, history)

            # 截断保留上限
            existing = existing[:MAX_ARTICLES_PER_ACCOUNT]
            self._save_articles(nickname, existing)
            total_articles = len(existing)
            if all_downloads_success:
                upload_result = self._upload_new_articles(upload_articles)
            else:
                upload_result = self._defer_upload_articles(
                    upload_articles,
                    "等待本轮 RSS 新文章全部下载成功后再上传",
                )
        except PermissionError:
            last_error = "登录已过期"
            logger.warning("RSS 抓取跳过 [%s]: 登录已过期", nickname)
        except Exception as e:
            last_error = str(e)
            logger.warning("RSS 抓取失败 [%s]: %s", nickname, e)

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
                        s["pending_upload_count"] = upload_result.get("pending_count", 0)
                        s["last_upload_attempted"] = upload_result.get("attempted", False)
                        s["last_upload_disabled"] = upload_result.get("disabled", False)
                    break
            self._save_subscriptions(subs)

        if new_count > 0:
            logger.info("RSS 抓取并下载 [%s]: 新增 %d 篇文章", nickname, new_count)

    # ── 调度循环 ──────────────────────────────────────────

    def _run_loop(self):
        logger.info("RSS 调度器已启动")
        while not self._stop_event.is_set():
            try:
                self._tick()
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
            self._fetch_for_account(sub)


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


# 全局单例
rss_scheduler = RssScheduler()
