"""
账号池模块
管理多个微信公众平台/微信读书账号凭证的存储、调度（acquire）、状态上报（report）、探活验证与增删改查。
调度算法照搬代理池范式（backend/config.py: get_proxy_url / report_proxy_status）。
"""

import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor

from backend.config import (
    ACCOUNT_POOL_FILE, CONFIG_FILE,
    load_json, save_json, get_settings, WEREAD_PLATFORM_URL, get_proxies_dict, report_proxy_status
)

logger = logging.getLogger(__name__)

# ── 调度参数 ──────────────────────────────────────────
COOLDOWN_SECONDS = 10 * 60               # 单次风控冷却 10 分钟
RISK_KICK_THRESHOLD = 3                  # 累计风控(200013 / 429)达 3 次 → banned
FAILURE_KICK_THRESHOLD = 8               # 连续普通失败达 8 次 → invalid


def _gen_id() -> str:
    """生成稳定唯一的账号 id"""
    import random
    import string
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"acc_{int(time.time())}_{suffix}"


class AccountPool:
    """账号池：存储、调度、状态上报、探活验证、增删改查。全局单例。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._kick_events: list[dict] = []  # 踢出事件队列
        self._keepalive_thread = None
        self._stop_keepalive = threading.Event()

    # ── 存储 ──────────────────────────────────────────

    def _load(self) -> list:
        return load_json(ACCOUNT_POOL_FILE, [])

    def _save(self, accounts: list):
        save_json(ACCOUNT_POOL_FILE, accounts)

    # ── 调度 ──────────────────────────────────────────

    def acquire(self) -> dict | None:
        """
        选出一个可用账号并返回其副本（含 token/cookie_str）。
        规则：
          1. 把 cooldown_until 已过期的 cooldown 账号恢复为 active；
          2. 过滤 status==active 的账号；
          3. 按 (failures, last_used) 升序，取最久未使用的可用账号；
          4. 更新其 last_used；
          5. 全部不可用 → 返回 None。
        """
        now = time.time()
        with self._lock:
            accounts = self._load()
            changed = False

            for acc in accounts:
                # 冷却自愈
                if acc.get("status") == "cooldown" and now >= acc.get("cooldown_until", 0):
                    acc["status"] = "active"
                    acc["health_status"] = "valid"
                    changed = True

            active = [a for a in accounts if a.get("status") == "active"]

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

            err_str = str(error or "")
            if ret == 0:
                # 成功：清零失败计数，标记健康
                acc["failures"] = 0
                acc["last_error"] = None
                acc["health_status"] = "valid"
                acc["last_verified_at"] = now
                acc["last_verified_result"] = "调用成功"
            elif ret == 200013 or "WeReadError429" in err_str or "429" in err_str:
                # 风控 / 请求频繁
                acc["risk_hits"] = acc.get("risk_hits", 0) + 1
                acc["failures"] = acc.get("failures", 0) + 1
                acc["last_error"] = error or "触发频率控制 (429)"
                acc["health_status"] = "cooldown"
                if acc["risk_hits"] >= RISK_KICK_THRESHOLD:
                    acc["status"] = "banned"
                    acc["health_status"] = "invalid"
                    acc["kicked_time"] = now
                    acc["last_error"] = f"累计风控 {acc['risk_hits']} 次，已被移出调度池"
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
            elif ret == 200003 or "WeReadError401" in err_str or "401" in err_str or "Unauthorized" in err_str:
                # 登录态失效
                acc["status"] = "invalid"
                acc["health_status"] = "invalid"
                acc["kicked_time"] = now
                acc["last_error"] = error or "登录态已失效 (401 Unauthorized)"
                acc["last_verified_at"] = now
                acc["last_verified_result"] = "凭证已失效"
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
                    acc["health_status"] = "invalid"
                    acc["kicked_time"] = now
                    acc["last_error"] = f"连续失败 {acc['failures']} 次，已被移出调度池"
                    self._kick_events.append({
                        "id": acc["id"],
                        "nickname": acc.get("nickname", ""),
                        "reason": acc["last_error"],
                        "time": now,
                        "status": "invalid",
                    })
                    logger.warning("账号 [%s] 被踢出(invalid): %s", acc.get("nickname"), acc["last_error"])

            self._save(accounts)

    # ── 探活与自动刷新 (Verify-First) ──────────────────

    def verify_account(self, account_id: str) -> dict:
        """
        对单个账号执行真实接口探活与状态刷新。
        借鉴 we-mp-rss 的轻量验证策略，直接请求微信读书平台 API 验证 Token 有效性。
        """
        with self._lock:
            accounts = self._load()
            acc = None
            for a in accounts:
                if a["id"] == account_id:
                    acc = a
                    break
            if not acc:
                return {"valid": False, "status": "not_found", "message": "账号不存在"}

        now = time.time()
        token = acc.get("token", "")
        platform_url = get_settings().get("weread_platform_url") or WEREAD_PLATFORM_URL
        headers = {
            "xid": str(account_id),
            "Authorization": f"Bearer {token}",
        }
        proxies = get_proxies_dict()
        proxy_url = proxies.get("http") if proxies else None

        valid = False
        status = "unknown"
        message = ""

        try:
            import requests as req
            resp = req.get(
                f"{platform_url}/api/v2/platform/mps/MP_WXS_3528995129/articles",
                params={"page": 1},
                headers=headers,
                proxies=proxies,
                timeout=12,
            )
            if resp.status_code == 200:
                valid = True
                status = "active"
                message = "凭证验证通过，账号状态正常"
                if proxy_url:
                    report_proxy_status(proxy_url, success=True)
            elif resp.status_code == 401 or "Unauthorized" in resp.text or "WeReadError401" in resp.text:
                valid = False
                status = "invalid"
                message = "登录凭证已过期或失效，需要重新扫码登录"
            elif resp.status_code == 429 or "WeReadError429" in resp.text:
                valid = False
                status = "cooldown"
                message = "触发微信读书频率限制(429)，已进入冷却状态"
            else:
                valid = False
                status = acc.get("status", "active")
                message = f"探活响应异常 (HTTP {resp.status_code}): {resp.text[:100]}"
        except Exception as e:
            valid = False
            status = acc.get("status", "active")
            message = f"网络连接探活超时或失败: {str(e)}"
            if proxy_url:
                report_proxy_status(proxy_url, success=False)

        # 回写探活结果
        with self._lock:
            accounts = self._load()
            for a in accounts:
                if a["id"] == account_id:
                    a["last_verified_at"] = now
                    a["last_verified_result"] = message
                    if valid:
                        a["status"] = "active"
                        a["health_status"] = "valid"
                        a["failures"] = 0
                        a["last_error"] = None
                    elif status in ("invalid", "cooldown", "banned"):
                        a["status"] = status
                        a["health_status"] = "invalid" if status in ("invalid", "banned") else "cooldown"
                        a["last_error"] = message
                        if status == "cooldown":
                            a["cooldown_until"] = now + COOLDOWN_SECONDS
                    break
            self._save(accounts)

        return {
            "account_id": account_id,
            "nickname": acc.get("nickname", ""),
            "valid": valid,
            "status": status,
            "message": message,
            "last_verified_at": now,
        }

    def verify_all(self) -> list[dict]:
        """批量并发检测账号池中所有账号的状态"""
        accounts = self._load()
        if not accounts:
            return []

        results = []
        with ThreadPoolExecutor(max_workers=min(5, len(accounts))) as executor:
            futures = {executor.submit(self.verify_account, a["id"]): a["id"] for a in accounts}
            for future in futures:
                try:
                    res = future.result(timeout=25)
                    results.append(res)
                except Exception as exc:
                    results.append({
                        "account_id": futures[future],
                        "valid": False,
                        "status": "error",
                        "message": f"检测异常: {str(exc)}",
                    })
        return results

    # ── 增删改查 ──────────────────────────────────────

    def list_accounts(self) -> list:
        """返回脱敏列表（不含敏感完整 token/cookie，包含多账号元数据与真实状态）"""
        now = time.time()
        accounts = self._load()
        result = []
        for acc in accounts:
            save_time = acc.get("save_time", 0)
            last_verified_at = acc.get("last_verified_at", 0)
            status = acc.get("status", "active")

            # 冷却自愈判断
            if status == "cooldown" and now >= acc.get("cooldown_until", 0):
                status = "active"

            result.append({
                "id": acc["id"],
                "type": acc.get("type", "weread_platform"),
                "vid": acc.get("vid", ""),
                "nickname": acc.get("nickname", "微信读书用户"),
                "remark": acc.get("remark", ""),
                "avatar": acc.get("avatar", ""),
                "token_preview": (acc.get("token", "") or "")[:8] + "..." if acc.get("token") else "",
                "status": status,
                "health_status": acc.get("health_status", "valid" if status == "active" else "invalid"),
                "failures": acc.get("failures", 0),
                "risk_hits": acc.get("risk_hits", 0),
                "last_used": acc.get("last_used", 0),
                "last_verified_at": last_verified_at,
                "last_verified_result": acc.get("last_verified_result", ""),
                "last_browser_refreshed_at": acc.get("last_browser_refreshed_at", 0),
                "cooldown_until": acc.get("cooldown_until", 0),
                "last_error": acc.get("last_error"),
                "kicked_time": acc.get("kicked_time", 0),
                "save_time": save_time,
            })
        return result

    def browser_refresh_account(self, account_id: str) -> dict:
        """使用专属独立 Profile 启动无头浏览器，深度刷新微信读书登录态 Cookie"""
        with self._lock:
            accounts = self._load()
            target = None
            for a in accounts:
                if a["id"] == account_id:
                    target = a
                    break
            if not target:
                return {"ok": False, "message": "账号不存在"}
            existing_cookie = target.get("cookie_str", "")
            acc_type = target.get("type", "weread_platform")
            token = target.get("token", "")

        # 如果账号属于 weread_platform (Token 模式) 且无本地浏览器 Cookie
        # 无头浏览器无法凭空生成 Cookie，直接执行真实 API 探活与状态续期
        if acc_type == "weread_platform" and not existing_cookie:
            verify_res = self.verify_account(account_id)
            if verify_res.get("valid"):
                now = time.time()
                with self._lock:
                    accounts = self._load()
                    for a in accounts:
                        if a["id"] == account_id:
                            a["last_browser_refreshed_at"] = now
                            a["last_verified_at"] = now
                            a["last_verified_result"] = "Token 探活验证通过（中转模式无需浏览器 Profile）"
                            a["status"] = "active"
                            a["health_status"] = "valid"
                            break
                    self._save(accounts)
                return {
                    "ok": True,
                    "vid": target.get("vid", ""),
                    "message": "账号为中转 Token 凭证模式，接口验证通过，状态正常（无需浏览器 Profile 换新）",
                }
            else:
                return {
                    "ok": False,
                    "needs_scan": True,
                    "message": verify_res.get("message", "登录凭证已失效，请重新扫码登录"),
                }

        from backend.weread_browser import refresh_weread_account_browser
        res = refresh_weread_account_browser(account_id, existing_cookie=existing_cookie, headless=True)

        now = time.time()
        with self._lock:
            accounts = self._load()
            for a in accounts:
                if a["id"] == account_id:
                    if res.get("ok"):
                        a["cookie_str"] = res.get("cookie", a.get("cookie_str", ""))
                        a["last_browser_refreshed_at"] = now
                        a["last_verified_at"] = now
                        a["last_verified_result"] = "浏览器保活刷新成功"
                        a["status"] = "active"
                        a["health_status"] = "valid"
                        a["failures"] = 0
                        a["last_error"] = None
                    elif res.get("needs_scan"):
                        # 二次防线：如果该账号还有有效的 Token，不轻易置为 invalid
                        token_valid = False
                        if a.get("token"):
                            v_res = self.verify_account(account_id)
                            if v_res.get("valid"):
                                token_valid = True
                        if not token_valid:
                            a["status"] = "invalid"
                            a["health_status"] = "invalid"
                            a["last_error"] = res.get("message", "登录凭证已过期，需重新扫码")
                            a["last_verified_at"] = now
                            a["last_verified_result"] = a["last_error"]
                    break
            self._save(accounts)

        return res

    def add_or_update(self, cred: dict) -> dict:
        """登录成功后写入/更新（按 token / vid / nickname 去重合并）"""
        with self._lock:
            accounts = self._load()
            token = cred.get("token", "")
            vid = str(cred.get("vid", "") or "")
            nickname = cred.get("nickname", "微信读书用户")
            acc_type = cred.get("type", "weread_platform")
            now = time.time()

            # 1. 尝试按 token 或 vid 匹配
            matched_acc = None
            for acc in accounts:
                if token and acc.get("token") == token:
                    matched_acc = acc
                    break
                if vid and str(acc.get("vid", "")) == vid:
                    matched_acc = acc
                    break

            # 2. 如果未匹配到，尝试按 nickname 匹配（排除默认名）
            if not matched_acc and nickname and nickname not in ("微信读书用户", "公众号未命名"):
                for acc in accounts:
                    if acc.get("nickname") == nickname:
                        matched_acc = acc
                        break

            if matched_acc:
                # 更新已有账号凭证
                matched_acc["token"] = token
                matched_acc["cookie_str"] = cred.get("cookie_str", "")
                matched_acc["cookies"] = cred.get("cookies", [])
                if vid:
                    matched_acc["vid"] = vid
                if cred.get("avatar"):
                    matched_acc["avatar"] = cred.get("avatar")
                if nickname and nickname != "微信读书用户":
                    matched_acc["nickname"] = nickname
                if cred.get("remark") and not matched_acc.get("remark"):
                    matched_acc["remark"] = cred.get("remark")
                matched_acc["type"] = acc_type
                matched_acc["save_time"] = cred.get("save_time", now)
                matched_acc["last_verified_at"] = now
                matched_acc["last_verified_result"] = "登录验证成功"
                matched_acc["status"] = "active"
                matched_acc["health_status"] = "valid"
                matched_acc["failures"] = 0
                matched_acc["risk_hits"] = 0
                matched_acc["last_error"] = None
                matched_acc["cooldown_until"] = 0
                matched_acc["kicked_time"] = 0
                self._save(accounts)
                logger.info("账号池更新: [%s] (ID %s, VID %s)", nickname, matched_acc["id"], vid)
                return matched_acc

            # 3. 全新账号，新增
            new_acc = {
                "id": _gen_id(),
                "type": acc_type,
                "vid": vid,
                "token": token,
                "cookie_str": cred.get("cookie_str", ""),
                "cookies": cred.get("cookies", []),
                "nickname": nickname,
                "remark": cred.get("remark", ""),
                "avatar": cred.get("avatar", ""),
                "save_time": cred.get("save_time", now),
                "last_verified_at": now,
                "last_verified_result": "登录验证成功",
                "status": "active",
                "health_status": "valid",
                "failures": 0,
                "risk_hits": 0,
                "last_used": 0.0,
                "cooldown_until": 0.0,
                "last_error": None,
                "kicked_time": 0.0,
            }
            accounts.append(new_acc)
            self._save(accounts)
            logger.info("账号池新增: [%s] (ID %s, VID %s)", nickname, new_acc["id"], vid)
            return new_acc

    def update_account_info(self, account_id: str, patch: dict) -> dict | None:
        """更新账号备注、别名等元数据"""
        with self._lock:
            accounts = self._load()
            target = None
            for acc in accounts:
                if acc["id"] == account_id:
                    target = acc
                    break
            if not target:
                return None

            if "remark" in patch:
                target["remark"] = str(patch["remark"] or "").strip()
            if "nickname" in patch and patch["nickname"]:
                target["nickname"] = str(patch["nickname"]).strip()
            if "status" in patch and patch["status"] in ("active", "cooldown", "banned", "invalid"):
                target["status"] = patch["status"]

            self._save(accounts)
            return dict(target)

    def remove(self, account_id: str) -> bool:
        """从池中移除账号，并清理该账号的独立 Profile 目录"""
        with self._lock:
            accounts = self._load()
            new_accounts = [a for a in accounts if a["id"] != account_id]
            if len(new_accounts) == len(accounts):
                return False
            self._save(new_accounts)

        from backend.weread_browser import clean_weread_profile
        clean_weread_profile(account_id)
        return True

    def revive(self, account_id: str) -> bool:
        """手动复活/重新激活：恢复 status=active, 清零失败和风控计数，设为待探活状态"""
        with self._lock:
            accounts = self._load()
            for acc in accounts:
                if acc["id"] == account_id:
                    acc["status"] = "active"
                    acc["health_status"] = "valid"
                    acc["failures"] = 0
                    acc["risk_hits"] = 0
                    acc["last_error"] = None
                    acc["cooldown_until"] = 0
                    acc["kicked_time"] = 0
                    self._save(accounts)
                    logger.info("账号已手动重新激活: [%s] (ID: %s)", acc.get("nickname"), account_id)
                    return True
            return False

    def get_active_count(self) -> int:
        now = time.time()
        accounts = self._load()
        count = 0
        for acc in accounts:
            if acc.get("status") == "active":
                count += 1
            elif acc.get("status") == "cooldown" and now >= acc.get("cooldown_until", 0):
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

    # ── 后台自动保活与心跳巡检 ────────────────────────────

    def start_keepalive(self):
        """启动后台自动保活与心跳巡检线程"""
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            return
        self._stop_keepalive.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name="account-pool-keepalive",
            daemon=True,
        )
        self._keepalive_thread.start()
        logger.info("账号池后台自动保活与心跳巡检线程已启动")

    def _keepalive_loop(self):
        """后台保活循环：定期巡检账号健康、自愈冷却、发送轻量保活心跳与浏览器换新"""
        # 启动后先等待 15 秒（避开应用启动高峰）
        if self._stop_keepalive.wait(15):
            return

        while not self._stop_keepalive.is_set():
            try:
                self._run_keepalive_round()
            except Exception as e:
                logger.error("账号池保活巡检异常: %s", e)

            # 每 15 分钟巡检一轮（贴合微信读书 1~2 小时凭证周期）
            if self._stop_keepalive.wait(15 * 60):
                break

    def _run_keepalive_round(self):
        """执行一轮双阶梯保活：
        1. 冷却自愈与轻量心跳探活（仅对有效活跃账号）
        2. 定期（>45分钟）按需无头浏览器深度换新 Cookie（严格单实例串行，失效账号彻底跳过）
        """
        now = time.time()
        with self._lock:
            accounts = self._load()
            changed = False
            for acc in accounts:
                if acc.get("status") == "cooldown" and now >= acc.get("cooldown_until", 0):
                    acc["status"] = "active"
                    acc["health_status"] = "valid"
                    changed = True
            if changed:
                self._save(accounts)

        # 阶段一：轻量 HTTP 心跳探活（仅针对活跃健康账号，已失效/已封禁账号彻底跳过）
        accounts = self._load()
        for acc in accounts:
            if self._stop_keepalive.is_set():
                break
            # 严格过滤：失效/封禁账号不重复发送心跳
            if acc.get("status") in ("invalid", "banned") or acc.get("health_status") == "invalid":
                continue
            last_ver = acc.get("last_verified_at", 0)
            if now - last_ver >= 15 * 60:  # 距上次验证超 15 分钟才发送心跳
                logger.info("账号池自动心跳探活: [%s] (ID: %s)", acc.get("nickname"), acc["id"])
                self.verify_account(acc["id"])
                time.sleep(2)  # 账号间间隔 2 秒避开频控

        # 阶段二：对含本地浏览器会话的健康活跃账号进行定期浏览器深度会话换新（>45分钟自动换新一次，串行单实例执行）
        accounts = self._load()
        for acc in accounts:
            if self._stop_keepalive.is_set():
                break
            # 严格过滤：仅对正常活跃且含有 Cookie 的账号启动无头浏览器保活（纯 Token 账号由阶段一负责探活）
            if acc.get("status") != "active" or acc.get("health_status") != "valid":
                continue
            if not acc.get("cookie_str") and acc.get("type") == "weread_platform":
                continue
            last_browser_ref = acc.get("last_browser_refreshed_at", 0)
            # 微信读书凭证周期约 1~2 小时，设置 45 分钟深度换新一次，保障始终处于新鲜有效期
            if now - last_browser_ref >= 45 * 60:
                logger.info("账号池自动浏览器深度保活换新: [%s] (ID: %s)", acc.get("nickname"), acc["id"])
                try:
                    self.browser_refresh_account(acc["id"])
                except Exception as e:
                    logger.warning("账号 [%s] 浏览器保活换新异常: %s", acc.get("nickname"), e)
                time.sleep(5)  # 账号间间隔 5 秒，充分释放浏览器资源


# ── 全局单例 ──────────────────────────────────────────

account_pool = AccountPool()
account_pool.start_keepalive()


def borrow_session() -> tuple[str, str, str]:
    """
    返回 (account_id, token, cookie_str)。
    无可用账号时抛 RuntimeError。
    """
    acc = account_pool.acquire()
    if not acc:
        raise RuntimeError("账号池中无可用账号，请在『账号池』页面添加或重新登录账号")
    return acc["id"], acc["token"], acc.get("cookie_str", "")


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
            "vid": str(legacy.get("vid", "")),
            "cookie_str": legacy.get("cookie_str", ""),
            "cookies": legacy.get("cookies", []),
            "nickname": account_info.get("nickname") or legacy.get("nickname", "微信读书用户"),
            "avatar": account_info.get("avatar", ""),
            "save_time": legacy.get("save_time", time.time()),
        })
        logger.info("已将旧 wechat_mp_config.json 迁移到账号池")
