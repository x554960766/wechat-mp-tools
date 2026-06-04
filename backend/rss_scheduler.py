"""
RSS 自动订阅调度模块
后台定时抓取已订阅公众号的最新文章，供 RSS Feed 输出
"""

import time
import threading
import logging

from backend.config import DATA_DIR, load_json, save_json

logger = logging.getLogger(__name__)

RSS_SUBSCRIPTIONS_FILE = DATA_DIR / "rss_subscriptions.json"
RSS_ARTICLES_FILE = DATA_DIR / "rss_articles.json"

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
            subs = self.get_subscriptions()
            for sub in subs:
                if sub.get("fakeid") == fakeid:
                    sub["nickname"] = nickname
                    sub["interval_minutes"] = interval_minutes
                    sub["enabled"] = True
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
            }
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

    # ── 抓取逻辑 ──────────────────────────────────────────

    def _fetch_for_account(self, sub: dict):
        """为单个订阅抓取最新文章"""
        from backend.articles import _fetch_articles_page

        fakeid = sub["fakeid"]
        nickname = sub["nickname"]

        last_error = None
        new_count = 0
        total_articles = 0

        try:
            articles, _total = _fetch_articles_page(fakeid, begin=0, count=10)
            existing = self.get_articles(nickname)
            existing_links = {a.get("link") for a in existing}

            for art in articles:
                if art.get("link") and art["link"] not in existing_links:
                    existing.insert(0, art)
                    existing_links.add(art["link"])
                    new_count += 1

            # 截断保留上限
            existing = existing[:MAX_ARTICLES_PER_ACCOUNT]
            self._save_articles(nickname, existing)
            total_articles = len(existing)
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
                    s["last_fetch_time"] = time.time()
                    s["last_fetch_count"] = new_count
                    s["last_error"] = last_error
                    s["total_articles"] = total_articles
                    break
            self._save_subscriptions(subs)

        if new_count > 0:
            logger.info("RSS 抓取 [%s]: 新增 %d 篇文章", nickname, new_count)

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

    def _tick(self):
        subs = self.get_subscriptions()
        now = time.time()

        for sub in subs:
            if not sub.get("enabled"):
                continue
            interval_sec = sub.get("interval_minutes", 60) * 60
            last_fetch = sub.get("last_fetch_time", 0)
            if now - last_fetch >= interval_sec:
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
