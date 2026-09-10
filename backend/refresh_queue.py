"""凭证刷新队列：过期/失败的公众号按"自动跳过 + 及时补凭证"策略排入队列，
由唯一的 UI 自动化流程逐个消化（最终一致：本轮失败不影响下轮，数据不丢）。

设计要点：
- 线程安全：采集线程池并发 enqueue，worker 单线程 pop；
- 去重冷却：同一 biz 短时间内不重复入队，防止采集线程连环失败打爆队列；
- 尝试上限：单个 biz 累计尝试超过上限后放弃并告警（通常是账号不存在/搜不到），
  避免死循环；用户手动触发不受此限制。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

logger = logging.getLogger("refresh_queue")

DEDUP_COOLDOWN_SECONDS = 600    # 同一 biz 10 分钟内不重复入队
MAX_ATTEMPTS = 15               # 单个 biz 最大自动尝试次数，超过后放弃


class RefreshQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._items: "OrderedDict[str, dict]" = OrderedDict()  # biz -> item

    def enqueue(self, biz: str, name: str = "", reason: str = "", force: bool = False) -> bool:
        """把公众号加入刷新队列。返回 True 表示成功入队。
        force=True 时绕过去重冷却与尝试上限（供主动续期/手动触发使用）。"""
        if not biz:
            return False
        now = time.time()
        with self._lock:
            old = self._items.get(biz)
            if old is not None:
                if not force:
                    if now - old.get("enqueued_at", 0) < DEDUP_COOLDOWN_SECONDS:
                        return False          # 去重冷却期内，跳过
                    if old.get("attempts", 0) >= MAX_ATTEMPTS:
                        return False          # 超过尝试上限，等人工介入
                # 已在队列中：刷新名称/原因，保持原位置与计数
                old["name"] = name or old.get("name", "")
                old["reason"] = reason or old.get("reason", "")
                old["enqueued_at"] = now
                return True
            self._items[biz] = {
                "biz": biz,
                "name": name,
                "reason": reason,
                "enqueued_at": now,
                "attempts": 0,
            }
            logger.info("📥 刷新队列入队: [%s] biz=%s (%s)", name or "未命名", biz[:14], reason or "-")
            return True

    def requeue(self, item: dict) -> bool:
        """一次定向续期失败后放回队列（尝试计数已在 pop 时 +1，此处不重复累计；
        超过上限则放弃，避免死循环）。"""
        if not item or not item.get("biz"):
            return False
        biz = item["biz"]
        with self._lock:
            if item.get("attempts", 0) >= MAX_ATTEMPTS:
                logger.warning("🚫 刷新队列放弃: [%s] biz=%s 已尝试 %d 次仍未成功，请人工检查该公众号",
                               item.get("name", "未命名"), biz[:14], item.get("attempts", 0))
                self._items.pop(biz, None)
                return False
            # 放回队尾，等待下一轮（退避由 worker 的 pacing 间隔提供）
            self._items.pop(biz, None)
            item = dict(item)
            item["enqueued_at"] = time.time()
            self._items[biz] = item
            return True

    def pop(self) -> dict | None:
        """取出队首目标（FIFO），并把尝试计数 +1。"""
        with self._lock:
            while self._items:
                biz, item = self._items.popitem(last=False)
                item["attempts"] = item.get("attempts", 0) + 1
                return item
            return None

    def remove(self, biz: str) -> None:
        with self._lock:
            self._items.pop(biz, None)

    def pending(self) -> list:
        with self._lock:
            return list(self._items.values())

    def size(self) -> int:
        with self._lock:
            return len(self._items)


# 全局单例（与 account_pool 同范式）
refresh_queue = RefreshQueue()
