"""
账号池模块
管理多个微信公众平台账号凭证的存储、调度（acquire）、状态上报（report）、增删改查。
调度算法照搬代理池范式（backend/config.py: get_proxy_url / report_proxy_status）。
"""

import time
import threading
import logging

from backend.config import (
    ACCOUNT_POOL_FILE, CONFIG_FILE,
    load_json, save_json,
)

logger = logging.getLogger(__name__)

# ── 调度参数 ──────────────────────────────────────────
LOGIN_VALID_SECONDS = 4 * 24 * 60 * 60   # 凭证 4 天有效（与 auth.py 保持一致）
COOLDOWN_SECONDS = 10 * 60               # 单次风控冷却 10 分钟
RISK_KICK_THRESHOLD = 3                  # 累计风控(200013)达 3 次 → banned
FAILURE_KICK_THRESHOLD = 8               # 连续普通失败达 8 次 → invalid


def _gen_id() -> str:
    """生成稳定唯一的账号 id"""
    import random
    import string
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"acc_{int(time.time())}_{suffix}"


class AccountPool:
    """账号池：存储、调度、状态上报、增删改查。全局单例。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._kick_events: list[dict] = []  # 踢出事件队列

    # ── 存储 ──────────────────────────────────────────

    def _load(self) -> list:
        return load_json(ACCOUNT_POOL_FILE, [])

    def _save(self, accounts: list):
        save_json(ACCOUNT_POOL_FILE, accounts)

    # ── 调度 ──────────────────────────────────────────

    def acquire(self) -> dict | None:
        """
        选出一个可用账号并返回其副本（含 token/cookie_str）。
        规则（照搬 get_proxy_url 的逻辑）：
          1. 把 cooldown_until 已过期的 cooldown 账号恢复为 active；
          2. 把凭证已过期的 active 账号标记为 invalid；
          3. 过滤 status==active 的账号；
          4. 按 (failures, last_used) 升序，取第一个；
          5. 更新其 last_used；
          6. 全部不可用 → 返回 None。
        """
        now = time.time()
        with self._lock:
            accounts = self._load()
            changed = False

            for acc in accounts:
                # 冷却自愈
                if acc["status"] == "cooldown" and now >= acc.get("cooldown_until", 0):
                    acc["status"] = "active"
                    changed = True

                # 凭证过期判断
                if acc["status"] == "active":
                    save_time = acc.get("save_time", 0)
                    if save_time and (now - save_time > LOGIN_VALID_SECONDS):
                        acc["status"] = "invalid"
                        acc["last_error"] = "凭证已过期"
                        acc["kicked_time"] = now
                        self._kick_events.append({
                            "id": acc["id"],
                            "nickname": acc.get("nickname", ""),
                            "reason": "凭证已过期",
                            "time": now,
                            "status": "invalid",
                        })
                        changed = True

            active = [a for a in accounts if a["status"] == "active"]

            if not active:
                if changed:
                    self._save(accounts)
                return None

            # 按 (失败次数, 最久未用) 排序
            active.sort(key=lambda a: (a.get("failures", 0), a.get("last_used", 0)))
            selected = active[0]
            selected["last_used"] = now
            changed = True

            self._save(accounts)
            return dict(selected)  # 返回副本

    def report(self, account_id: str, *, ret: int | None = None,
               http_ok: bool = True, error: str | None = None):
        """
        采集结果回写（照搬 report_proxy_status 的范式）。
        """
        now = time.time()
        with self._lock:
            accounts = self._load()
            acc = None
            for a in accounts:
                if a["id"] == account_id:
                    acc = a
                    break
            if not acc:
                return

            if ret == 0:
                # 成功：清零失败计数
                acc["failures"] = 0
                acc["last_error"] = None
            elif ret == 200013:
                # 风控
                acc["risk_hits"] = acc.get("risk_hits", 0) + 1
                acc["failures"] = acc.get("failures", 0) + 1
                acc["last_error"] = error or "触发频率控制(200013)"
                if acc["risk_hits"] >= RISK_KICK_THRESHOLD:
                    acc["status"] = "banned"
                    acc["kicked_time"] = now
                    acc["last_error"] = f"累计风控 {acc['risk_hits']} 次，已被踢出"
                    self._kick_events.append({
                        "id": acc["id"],
                        "nickname": acc.get("nickname", ""),
                        "reason": acc["last_error"],
                        "time": now,
                        "status": "banned",
                    })
                    logger.warning("账号 [%s] 被踢出(banned): %s", acc.get("nickname"), acc["last_error"])
                else:
                    acc["status"] = "cooldown"
                    acc["cooldown_until"] = now + COOLDOWN_SECONDS
                    logger.info("账号 [%s] 进入冷却 %ds", acc.get("nickname"), COOLDOWN_SECONDS)
            elif ret == 200003:
                # 登录态失效
                acc["status"] = "invalid"
                acc["kicked_time"] = now
                acc["last_error"] = error or "登录态失效(200003)"
                self._kick_events.append({
                    "id": acc["id"],
                    "nickname": acc.get("nickname", ""),
                    "reason": acc["last_error"],
                    "time": now,
                    "status": "invalid",
                })
                logger.warning("账号 [%s] 被踢出(invalid): %s", acc.get("nickname"), acc["last_error"])
            elif not http_ok:
                # 网络层失败
                acc["failures"] = acc.get("failures", 0) + 1
                acc["last_error"] = error or "网络请求失败"
            else:
                # 其他非 0 ret
                acc["failures"] = acc.get("failures", 0) + 1
                acc["last_error"] = error or f"API错误(ret={ret})"
                if acc["failures"] >= FAILURE_KICK_THRESHOLD:
                    acc["status"] = "invalid"
                    acc["kicked_time"] = now
                    acc["last_error"] = f"连续失败 {acc['failures']} 次，已被踢出"
                    self._kick_events.append({
                        "id": acc["id"],
                        "nickname": acc.get("nickname", ""),
                        "reason": acc["last_error"],
                        "time": now,
                        "status": "invalid",
                    })
                    logger.warning("账号 [%s] 被踢出(invalid): %s", acc.get("nickname"), acc["last_error"])

            self._save(accounts)

    # ── 增删改查 ──────────────────────────────────────

    def list_accounts(self) -> list:
        """返回脱敏列表（不含完整 cookie/token）"""
        now = time.time()
        accounts = self._load()
        result = []
        for acc in accounts:
            save_time = acc.get("save_time", 0)
            expires_at = save_time + LOGIN_VALID_SECONDS if save_time else 0
            remaining = max(0, int(expires_at - now)) if expires_at else 0
            result.append({
                "id": acc["id"],
                "nickname": acc.get("nickname", ""),
                "avatar": acc.get("avatar", ""),
                "token_preview": (acc.get("token", "") or "")[:8] + "..." if acc.get("token") else "",
                "status": acc.get("status", "active"),
                "failures": acc.get("failures", 0),
                "risk_hits": acc.get("risk_hits", 0),
                "last_used": acc.get("last_used", 0),
                "cooldown_until": acc.get("cooldown_until", 0),
                "last_error": acc.get("last_error"),
                "kicked_time": acc.get("kicked_time", 0),
                "remaining_seconds": remaining,
                "expired": remaining <= 0,
                "save_time": save_time,
            })
        return result

    def add_or_update(self, cred: dict) -> dict:
        """登录成功后写入/更新（按 token 去重）"""
        with self._lock:
            accounts = self._load()
            token = cred.get("token", "")
            nickname = cred.get("nickname", "公众号未命名")

            # 按 token 去重
            for acc in accounts:
                if acc.get("token") == token:
                    acc["cookie_str"] = cred.get("cookie_str", "")
                    acc["cookies"] = cred.get("cookies", [])
                    acc["nickname"] = nickname
                    acc["avatar"] = cred.get("avatar", "")
                    acc["save_time"] = cred.get("save_time", time.time())
                    acc["status"] = "active"
                    acc["failures"] = 0
                    acc["risk_hits"] = 0
                    acc["last_error"] = None
                    acc["cooldown_until"] = 0
                    acc["kicked_time"] = 0
                    self._save(accounts)
                    logger.info("账号池更新: [%s]", nickname)
                    return acc

            # 新增
            new_acc = {
                "id": _gen_id(),
                "token": token,
                "cookie_str": cred.get("cookie_str", ""),
                "cookies": cred.get("cookies", []),
                "nickname": nickname,
                "avatar": cred.get("avatar", ""),
                "save_time": cred.get("save_time", time.time()),
                "status": "active",
                "failures": 0,
                "risk_hits": 0,
                "last_used": 0.0,
                "cooldown_until": 0.0,
                "last_error": None,
                "kicked_time": 0.0,
            }
            accounts.append(new_acc)
            self._save(accounts)
            logger.info("账号池新增: [%s]", nickname)
            return new_acc

    def remove(self, account_id: str) -> bool:
        with self._lock:
            accounts = self._load()
            new_accounts = [a for a in accounts if a["id"] != account_id]
            if len(new_accounts) == len(accounts):
                return False
            self._save(new_accounts)
            return True

    def revive(self, account_id: str) -> bool:
        """手动复活：status=active, 清零计数"""
        with self._lock:
            accounts = self._load()
            for acc in accounts:
                if acc["id"] == account_id:
                    acc["status"] = "active"
                    acc["failures"] = 0
                    acc["risk_hits"] = 0
                    acc["last_error"] = None
                    acc["cooldown_until"] = 0
                    acc["kicked_time"] = 0
                    self._save(accounts)
                    return True
            return False

    def get_active_count(self) -> int:
        now = time.time()
        accounts = self._load()
        count = 0
        for acc in accounts:
            if acc.get("status") == "active":
                save_time = acc.get("save_time", 0)
                if save_time and (now - save_time <= LOGIN_VALID_SECONDS):
                    count += 1
            elif acc.get("status") == "cooldown" and now >= acc.get("cooldown_until", 0):
                save_time = acc.get("save_time", 0)
                if save_time and (now - save_time <= LOGIN_VALID_SECONDS):
                    count += 1
        return count

    def get_summary(self) -> dict:
        """返回概要统计"""
        accounts = self._load()
        summary = {"total": 0, "active": 0, "cooldown": 0, "banned": 0, "invalid": 0}
        now = time.time()
        for acc in accounts:
            summary["total"] += 1
            status = acc.get("status", "active")
            # 冷却自愈计入 active
            if status == "cooldown" and now >= acc.get("cooldown_until", 0):
                summary["active"] += 1
            elif status in summary:
                summary[status] += 1
        return summary

    def pop_kick_events(self) -> list:
        """取出自上次查询以来新发生的踢出事件，供前端弹提示"""
        with self._lock:
            events = list(self._kick_events)
            self._kick_events.clear()
            return events


# ── 全局单例 ──────────────────────────────────────────

account_pool = AccountPool()


def borrow_session() -> tuple[str, str, str]:
    """
    返回 (account_id, token, cookie_str)。
    无可用账号时抛 RuntimeError。
    """
    acc = account_pool.acquire()
    if not acc:
        raise RuntimeError("账号池中无可用账号，请先在『账号池』页面添加/登录账号")
    return acc["id"], acc["token"], acc["cookie_str"]


def migrate_legacy_config():
    """应用启动时执行一次：将旧 wechat_mp_config.json 迁移到账号池"""
    if ACCOUNT_POOL_FILE.exists():
        pool = load_json(ACCOUNT_POOL_FILE, [])
        if pool:
            return  # 已有池数据，不迁移

    legacy = load_json(CONFIG_FILE)
    if legacy and legacy.get("token"):
        account_info = legacy.get("account_info", {})
        account_pool.add_or_update({
            "token": legacy["token"],
            "cookie_str": legacy.get("cookie_str", ""),
            "cookies": legacy.get("cookies", []),
            "nickname": account_info.get("nickname", "公众号未命名"),
            "avatar": account_info.get("avatar", ""),
            "save_time": legacy.get("save_time", time.time()),
        })
        logger.info("已将旧 wechat_mp_config.json 迁移到账号池")
