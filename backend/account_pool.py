"""
账号池模块
管理多个微信公众平台账号凭证的存储、调度（acquire）、状态上报（report）、增删改查。
调度算法照搬代理池范式（backend/config.py: get_proxy_url / report_proxy_status）。
"""

import time
import threading
import logging
import urllib.parse

from backend.config import (
    ACCOUNT_POOL_FILE, CONFIG_FILE,
    load_json, save_json, get_default_wechat_ua
)

logger = logging.getLogger(__name__)

# ── 调度参数 ──────────────────────────────────────────
LOGIN_VALID_SECONDS = 4 * 24 * 60 * 60   # 凭证 4 天有效（与 auth.py 保持一致）
COOLDOWN_SECONDS = 10 * 60               # 单次风控冷却 10 分钟
RISK_KICK_THRESHOLD = 3                  # 累计风控(200013)达 3 次 → banned
FAILURE_KICK_THRESHOLD = 8               # 连续普通失败达 8 次 → invalid
BIZ_FRESH_SECONDS = 90 * 60              # biz 专属凭证新鲜阈值：90 分钟（与保活 worker 的 STALE_THRESHOLD 对齐）
BIZ_GETMSG_PROTECT_SECONDS = 2 * 60 * 60  # 已验证的 getmsg key 保护窗口：2 小时（客户端 key 寿命）


def _gen_id() -> str:
    """生成稳定唯一的账号 id"""
    import random
    import string
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"acc_{int(time.time())}_{suffix}"


def _normalize_uin(uin_str: str) -> str:
    """规范化 UIN：自动把 base64 编码的 UIN (如 MTQ1NDk1MDMyMA==) 解码为纯数字文本"""
    if not uin_str:
        return ""
    uin_str = str(uin_str).strip()
    if uin_str.endswith("==") or (len(uin_str) >= 12 and uin_str.isalnum()):
        try:
            import base64
            decoded = base64.b64decode(uin_str).decode('utf-8', errors='ignore').strip()
            if decoded.isdigit():
                return decoded
        except Exception:
            pass
    return uin_str


class AccountPool:
    """账号池：存储、调度、状态上报、增删改查。全局单例。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._kick_events: list[dict] = []  # 踢出事件队列
        self._recently_removed_tokens: dict[str, float] = {}  # token/uin -> timestamp (防止被未停用的代理秒级重复回写)

    # ── 存储 ──────────────────────────────────────────

    def _load(self) -> list:
        accounts = load_json(ACCOUNT_POOL_FILE, [])
        cleaned = False
        for acc in accounts:
            biz_tokens = acc.get("biz_tokens")
            if isinstance(biz_tokens, dict) and biz_tokens:
                new_biz_tokens = {}
                for biz, entry in biz_tokens.items():
                    name = (entry.get("name") if isinstance(entry, dict) else "") or self._resolve_biz_name(biz)
                    if name and name not in ("未命名公众号", "公众号未命名", "未命名") and self._is_favorite_biz(biz):
                        new_biz_tokens[biz] = entry
                    else:
                        cleaned = True
                acc["biz_tokens"] = new_biz_tokens
        if cleaned:
            save_json(ACCOUNT_POOL_FILE, accounts)
        return accounts

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

                # 凭证状态保持 (微信读书 Token 长期有效，按需熔断)
                pass

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

    def acquire_for_biz(self, biz: str) -> dict | None:
        """为指定公众号选号（解决多账号池下 acquire() 选错账号的问题）：
        优先选【持有该 biz 最新专属凭证】的 active 账号——凭证捕获写在哪条账号上，
        就用哪条账号去抓；无任何账号持有该 biz 凭证时，选全局凭证最新（save_time 最大）
        的账号；无 active 账号返回 None。biz 键匹配兼容 URL 编码差异。"""
        if not biz:
            return self.acquire()
        import urllib.parse
        now = time.time()
        with self._lock:
            accounts = self._load()
            changed = False

            for acc in accounts:
                if acc["status"] == "cooldown" and now >= acc.get("cooldown_until", 0):
                    acc["status"] = "active"
                    changed = True

            active = [a for a in accounts if a["status"] == "active"]
            if not active:
                if changed:
                    self._save(accounts)
                return None

            ubiz = urllib.parse.unquote(biz)

            def _biz_info(acc: dict) -> tuple:
                """该账号持有此 biz 专属凭证的 (是否已验证可拉列表, 最新时间)；无则 (False, 0)"""
                ts, ready = 0.0, False
                for k, entry in (acc.get("biz_tokens") or {}).items():
                    if k == biz or k == ubiz or (k and urllib.parse.unquote(k) == biz):
                        if isinstance(entry, dict):
                            ts = max(ts, entry.get("updated_at", 0) or 0)
                            if entry.get("getmsg_ready"):
                                ready = True
                        else:
                            ts = max(ts, acc.get("save_time", 0) or 0)
                return ready, ts

            best, best_key = None, None
            for acc in active:
                ready, ts = _biz_info(acc)
                # 元组越小越优：0=持有 biz 凭证（已验证 getmsg 的优先，再比新旧），1=仅有全局凭证
                if ts > 0:
                    cand = (0, 0 if ready else 1, -ts)
                else:
                    cand = (1, 1, -(acc.get("save_time", 0) or 0))
                if best_key is None or cand < best_key:
                    best, best_key = acc, cand

            best["last_used"] = now
            self._save(accounts)
            return dict(best)

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
                # 成功：清零失败计数
                acc["failures"] = 0
                acc["last_error"] = None
            elif ret == 200013 or "WeReadError429" in err_str:
                # 风控 / 请求频繁
                acc["risk_hits"] = acc.get("risk_hits", 0) + 1
                acc["failures"] = acc.get("failures", 0) + 1
                acc["last_error"] = error or "触发频率控制 (WeRead429)"
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
            elif ret in (-3, -4, -5, -6, 200003) or "WeReadError401" in err_str:
                # 凭证过期类失败按"自动跳过 + 及时补凭证"策略处理：
                # 若账号全局凭证仍在有效期内，说明只是该公众号的独立会话过期，
                # 不计失败、不踢出——由 articles.py 把该 biz 加入刷新队列，
                # UI 自动化续期成功后，下一轮采集自动补齐数据（最终一致）。
                is_fresh = (now - acc.get("save_time", 0)) < LOGIN_VALID_SECONDS
                if is_fresh:
                    acc["last_error"] = error or f"该公众号会话已过期 (ret={ret})，已进入凭证刷新队列等待续期"
                else:
                    acc["status"] = "invalid"
                    acc["kicked_time"] = now
                    acc["last_error"] = error or f"客户端凭证已超期失效 (ret={ret})"
                    self._kick_events.append({
                        "id": acc["id"],
                        "nickname": acc.get("nickname", ""),
                        "reason": acc["last_error"],
                        "time": now,
                        "status": "invalid",
                    })
                    logger.warning("账号 [%s] 凭证已失效(invalid): %s", acc.get("nickname"), acc["last_error"])
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

    @staticmethod
    def _is_favorite_biz(biz: str) -> bool:
        """判断 biz 是否属于用户已收藏的公众号"""
        if not biz:
            return False
        try:
            from backend.config import ACCOUNTS_FILE, load_json
            import urllib.parse
            accounts_sub = load_json(ACCOUNTS_FILE, [])
            if not accounts_sub:
                return False
            u_biz = urllib.parse.unquote(str(biz))
            for acc in accounts_sub:
                fid = str(acc.get("fakeid") or "")
                u_fid = urllib.parse.unquote(fid)
                alias = str(acc.get("alias") or "")
                if str(biz) in (fid, u_fid) or u_biz in (fid, u_fid) or (alias and str(biz) == alias):
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _resolve_biz_name(biz: str) -> str:
        """按 fakeid 从已收藏公众号列表反查名称（仅限已收藏公众号有效名称）。
        注意：会在 add_or_update 的锁内被调用，此处不得再获取 self._lock。"""
        if not biz:
            return ""
        try:
            from backend.config import ACCOUNTS_FILE, load_json
            import urllib.parse
            accounts_sub = load_json(ACCOUNTS_FILE, [])
            u_biz = urllib.parse.unquote(str(biz))
            for acc in accounts_sub:
                fid = str(acc.get("fakeid") or "")
                u_fid = urllib.parse.unquote(fid)
                alias = str(acc.get("alias") or "")
                if str(biz) in (fid, u_fid) or u_biz in (fid, u_fid) or (alias and str(biz) == alias):
                    name = acc.get("nickname") or acc.get("name") or ""
                    if name and name not in ("未命名公众号", "公众号未命名", "未命名"):
                        return name
        except Exception:
            pass
        return ""

    def list_accounts(self) -> list:
        """返回脱敏列表（不含完整 cookie/token），附带每个账号下的已订阅公众号(biz)专属凭证清单"""
        now = time.time()
        accounts = self._load()
        from backend.config import ACCOUNTS_FILE, load_json
        import urllib.parse
        accounts_sub = load_json(ACCOUNTS_FILE, [])
        subscribed_map = {}
        for a in accounts_sub:
            fid = str(a.get("fakeid") or "")
            name = a.get("nickname") or a.get("name") or ""
            if fid and name and name not in ("未命名公众号", "公众号未命名", "未命名"):
                subscribed_map[fid] = name
                subscribed_map[urllib.parse.unquote(fid)] = name
                if a.get("alias"):
                    subscribed_map[str(a.get("alias"))] = name

        result = []
        for acc in accounts:
            save_time = acc.get("save_time", 0)
            expires_at = save_time + LOGIN_VALID_SECONDS if save_time else 0
            remaining = max(0, int(expires_at - now)) if expires_at else 0

            # 展开 biz 专属凭证：严格只展示已收藏且有名有姓的公众号凭证，绝不展示未命名凭证
            biz_credentials = []
            for biz, entry in (acc.get("biz_tokens") or {}).items():
                if not isinstance(entry, dict):
                    continue

                # 必须属于已收藏公众号且解析出名称
                resolved_name = (
                    subscribed_map.get(str(biz)) or
                    subscribed_map.get(urllib.parse.unquote(str(biz))) or
                    self._resolve_biz_name(biz)
                )
                if not resolved_name or resolved_name in ("未命名公众号", "公众号未命名", "未命名"):
                    continue

                updated_at = entry.get("updated_at", 0)
                age = max(0, int(now - updated_at)) if updated_at else None
                biz_credentials.append({
                    "biz": (biz[:10] + "...") if len(str(biz)) > 10 else str(biz),
                    "fakeid": biz,
                    "name": resolved_name,
                    "updated_at": updated_at,
                    "age_seconds": age,
                    "fresh": age is not None and age < BIZ_FRESH_SECONDS,
                    "has_key": bool(entry.get("key")),
                    "getmsg_ready": bool(entry.get("getmsg_ready")),
                    "profile_url": f"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz={urllib.parse.quote(str(biz))}#wechat_redirect",
                })
            # 最近更新的排前面
            biz_credentials.sort(key=lambda x: -(x.get("updated_at") or 0))

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
                "biz_count": len(biz_credentials),
                "biz_credentials": biz_credentials,
            })
        return result

    def add_or_update(self, cred: dict) -> dict | None:
        """登录成功或动态抓包后写入/更新凭证（按 uin, token 或 nickname 去重与更新）"""
        with self._lock:
            token = cred.get("token", "")
            raw_uin = cred.get("uin", "")
            uin = _normalize_uin(raw_uin)
            nickname = cred.get("nickname", "公众号未命名")
            is_explicit = cred.get("is_explicit_login", False)
            now = time.time()

            # 清理过期的删除记录（> 5 分钟）
            self._recently_removed_tokens = {k: v for k, v in self._recently_removed_tokens.items() if now - v < 300}

            # 若此凭证近期刚被用户手动删除，且非主动扫码登录或打开新文章(带key)，则忽略被动抓包回写
            has_new_key = bool(cred.get("key"))
            if not is_explicit and not has_new_key:
                if (token and str(token) in self._recently_removed_tokens) or (uin and str(uin) in self._recently_removed_tokens):
                    return None
            else:
                # 显式登录或打开新文章时，自动解除删除保护
                if token and str(token) in self._recently_removed_tokens:
                    self._recently_removed_tokens.pop(str(token), None)
                if uin and str(uin) in self._recently_removed_tokens:
                    self._recently_removed_tokens.pop(str(uin), None)

            accounts = self._load()

            # 0. 尝试按 uin 匹配
            uin_match_acc = None
            if uin:
                for acc in accounts:
                    if acc.get("uin") and _normalize_uin(acc.get("uin")) == uin:
                        uin_match_acc = acc
                        break

            # 1. 尝试按 token 匹配
            token_match_acc = None
            if token:
                for acc in accounts:
                    if acc.get("token") and acc.get("token") == token:
                        token_match_acc = acc
                        break

            # 2. 如果 nickname 不是默认值，尝试按 nickname 匹配其他账号
            nickname_match_acc = None
            if nickname and nickname not in ("公众号未命名", "动态微信凭证", "PC微信动态凭证"):
                for acc in accounts:
                    if acc.get("nickname") == nickname:
                        nickname_match_acc = acc
                        break

            # 优先选择匹配到的账号，若未匹配到，优先复活处于 invalid 状态的账号，否则回退到第一个账号
            invalid_acc = next((a for a in accounts if a.get("status") == "invalid"), None)
            target_acc = uin_match_acc or token_match_acc or nickname_match_acc or invalid_acc or (accounts[0] if accounts else None)

            if target_acc:
                # 覆盖并升级已有账号的凭证（自愈恢复为 active）
                if token:
                    target_acc["token"] = token
                if cred.get("appmsg_token"):
                    target_acc["appmsg_token"] = cred.get("appmsg_token")
                if cred.get("cookie_str"):
                    target_acc["cookie_str"] = cred.get("cookie_str")
                if cred.get("cookies"):
                    target_acc["cookies"] = cred.get("cookies")
                if nickname and nickname not in ("公众号未命名", "动态微信凭证", "PC微信动态凭证"):
                    target_acc["nickname"] = nickname
                if cred.get("avatar"):
                    target_acc["avatar"] = cred.get("avatar")
                
                # 更新专属 biz 凭证（严格只同步已收藏且命名的公众号凭证，绝不同步未命名或非收藏公众号）
                biz = cred.get("biz")
                if biz and self._is_favorite_biz(biz):
                    resolved_biz_name = self._resolve_biz_name(biz) or cred.get("biz_name")
                    if resolved_biz_name and resolved_biz_name not in ("未命名公众号", "公众号未命名", "未命名"):
                        biz_tokens = target_acc.setdefault("biz_tokens", {})
                        app_token = cred.get("appmsg_token") or token or (biz_tokens.get(biz, {}).get("token") if isinstance(biz_tokens.get(biz), dict) else "")
                        old_entry = biz_tokens.get(biz) if isinstance(biz_tokens.get(biz), dict) else {}
                        getmsg_ready = cred.get("biz_source") == "profile_ext"

                        if (old_entry.get("getmsg_ready") and not getmsg_ready
                                and (time.time() - (old_entry.get("updated_at") or 0)) < BIZ_GETMSG_PROTECT_SECONDS):
                            old_entry["name"] = resolved_biz_name
                            logger.debug("biz [%s] 保留已验证的 getmsg key，忽略 %s 来源捕获",
                                         resolved_biz_name, cred.get("biz_source"))
                        else:
                            biz_tokens[biz] = {
                                "token": app_token,
                                "appmsg_token": app_token,
                                "key": cred.get("key") or old_entry.get("key", "") or target_acc.get("key", ""),
                                "pass_ticket": cred.get("pass_ticket") or old_entry.get("pass_ticket", "") or target_acc.get("pass_ticket", ""),
                                "poc_token": cred.get("poc_token") or old_entry.get("poc_token", "") or target_acc.get("poc_token", ""),
                                "poc_sid": cred.get("poc_sid") or old_entry.get("poc_sid", "") or target_acc.get("poc_sid", ""),
                                "wxtoken": cred.get("wxtoken") or old_entry.get("wxtoken", "") or target_acc.get("wxtoken", "777"),
                                "updated_at": cred.get("save_time", time.time()),
                                "name": resolved_biz_name,
                                "getmsg_ready": getmsg_ready or old_entry.get("getmsg_ready", False),
                            }
                has_new_key = bool(cred.get("key"))
                has_web_login = bool(is_explicit or (token and str(token).isdigit()))

                if cred.get("key"):
                    target_acc["key"] = cred.get("key")
                if cred.get("pass_ticket"):
                    target_acc["pass_ticket"] = cred.get("pass_ticket")
                if cred.get("poc_token"):
                    target_acc["poc_token"] = cred.get("poc_token")
                if cred.get("poc_sid"):
                    target_acc["poc_sid"] = cred.get("poc_sid")
                if cred.get("wxtoken"):
                    target_acc["wxtoken"] = cred.get("wxtoken")
                if uin:
                    target_acc["uin"] = uin
                if cred.get("user_agent"):
                    target_acc["user_agent"] = cred.get("user_agent")

                if has_new_key or has_web_login:
                    target_acc["save_time"] = cred.get("save_time", time.time())
                    target_acc["status"] = "active"
                    target_acc["failures"] = 0
                    target_acc["risk_hits"] = 0
                    target_acc["last_error"] = None
                    target_acc["cooldown_until"] = 0
                    target_acc["kicked_time"] = 0

                # 自动清理由于格式不同产生的多余重复条目
                cleaned_accounts = [a for a in accounts if a == target_acc or not (
                    (_normalize_uin(a.get("uin")) and _normalize_uin(a.get("uin")) == uin) or
                    (a.get("token") and a.get("token") == token)
                )]

                self._save(cleaned_accounts)
                logger.info("账号池更新凭证并恢复: [%s] (ID: %s, UIN: %s)", target_acc.get("nickname"), target_acc["id"], target_acc.get("uin"))
                return target_acc

            else:
                # 全新账号，新增
                biz_tokens_map = {}
                biz = cred.get("biz")
                if biz and self._is_favorite_biz(biz):
                    resolved_biz_name = self._resolve_biz_name(biz) or cred.get("biz_name")
                    if resolved_biz_name and resolved_biz_name not in ("未命名公众号", "公众号未命名", "未命名"):
                        app_token = cred.get("appmsg_token", token)
                        biz_tokens_map[biz] = {
                            "token": app_token,
                            "appmsg_token": app_token,
                            "key": cred.get("key", ""),
                            "pass_ticket": cred.get("pass_ticket", ""),
                            "updated_at": cred.get("save_time", time.time()),
                            "name": resolved_biz_name,
                            "getmsg_ready": cred.get("biz_source") == "profile_ext",
                        }

                new_acc = {
                    "id": _gen_id(),
                    "token": token,
                    "appmsg_token": cred.get("appmsg_token", token),
                    "cookie_str": cred.get("cookie_str", ""),
                    "cookies": cred.get("cookies", []),
                    "nickname": nickname,
                    "avatar": cred.get("avatar", ""),
                    "save_time": cred.get("save_time", time.time()),
                    "key": cred.get("key", ""),
                    "pass_ticket": cred.get("pass_ticket", ""),
                    "uin": uin,
                    "biz_tokens": biz_tokens_map,
                    "user_agent": cred.get("user_agent", ""),
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

    @staticmethod
    def get_biz_credential(account_data: dict, biz: str) -> dict:
        """从账号中提取针对特定 biz 的【专属】凭据。
        严格隔离：key / pass_ticket / appmsg_token 只使用该公众号自己的会话凭证，
        绝不回退账号级字段——账号级 key 来自最近一次打开的公众号（可能是其他号），
        跨号使用会被微信以 ret=-3 拒绝。无专属凭证时返回 {}，由调用方引导建立会话。"""
        if not account_data or not biz:
            return {}
        biz_tokens = account_data.get("biz_tokens", {})
        biz_entry = biz_tokens.get(biz)
        if biz_entry is None:
            # 兼容 URL 编码差异（捕获侧与订阅侧的 __biz 编码可能不一致）
            import urllib.parse
            ubiz = urllib.parse.unquote(biz)
            if ubiz != biz:
                biz_entry = biz_tokens.get(ubiz)
            if biz_entry is None:
                for k, v in biz_tokens.items():
                    if k and urllib.parse.unquote(k) == biz:
                        biz_entry = v
                        break
        if isinstance(biz_entry, dict):
            return {
                "token": biz_entry.get("appmsg_token") or biz_entry.get("token") or "",
                "appmsg_token": biz_entry.get("appmsg_token") or biz_entry.get("token") or "",
                "key": biz_entry.get("key", ""),
                "pass_ticket": biz_entry.get("pass_ticket", ""),
                "poc_token": biz_entry.get("poc_token") or account_data.get("poc_token", ""),
                "poc_sid": biz_entry.get("poc_sid") or account_data.get("poc_sid", ""),
                "wxtoken": biz_entry.get("wxtoken") or account_data.get("wxtoken", "777"),
                "uin": account_data.get("uin", ""),
                "cookie_str": account_data.get("cookie_str", ""),
                "user_agent": account_data.get("user_agent", ""),
                "updated_at": biz_entry.get("updated_at", 0),
                "getmsg_ready": bool(biz_entry.get("getmsg_ready")),
            }
        elif isinstance(biz_entry, str) and biz_entry:
            return {
                "token": biz_entry,
                "appmsg_token": biz_entry,
                "key": "",
                "pass_ticket": "",
                "uin": account_data.get("uin", ""),
                "cookie_str": account_data.get("cookie_str", ""),
                "user_agent": account_data.get("user_agent", ""),
                "updated_at": account_data.get("save_time", 0)
            }
        # 无该公众号专属凭证：返回空，调用方不得借用其他公众号的会话凭证
        return {}

    def get_web_token_session(self) -> tuple:
        """全池查找公众平台 Web 后台会话（纯数字 token 的账号），供 /cgi-bin/appmsg 备用通道使用。
        Web 登录（浏览器登录 mp.weixin.qq.com）与客户端凭证是不同通道：客户端 profile_ext 通道
        被微信服务端封锁后，Web token 是 API 拉列表的唯一恢复路径。
        返回 (token, cookie_str, account_id)，无可用返回 (None, None, None)。"""
        with self._lock:
            accounts = self._load()
            best, best_ts = None, 0
            for acc in accounts:
                token = str(acc.get("token") or "")
                if not token.isdigit() or not token:
                    continue
                if acc.get("status") not in ("active", "cooldown"):
                    continue
                cookie = acc.get("cookie_str") or ""
                if not cookie:
                    continue
                ts = acc.get("save_time", 0)
                if ts > best_ts:
                    best, best_ts = (token, cookie, acc["id"]), ts
            return best or (None, None, None)

    def get_biz_age(self, biz: str) -> float | None:
        """返回指定 biz 专属凭证的年龄（秒，取所有账号中最新的那份）。
        返回 None 表示尚无该公众号的专属凭证（优先级最高，应尽快建立会话）。
        供保活守护线程做主动续期排序使用。"""
        if not biz:
            return None
        with self._lock:
            accounts = self._load()
            now = time.time()
            newest_age = None
            for acc in accounts:
                entry = (acc.get("biz_tokens") or {}).get(biz)
                if isinstance(entry, dict):
                    ts = entry.get("updated_at", 0)
                    if ts:
                        age = now - ts
                        if newest_age is None or age < newest_age:
                            newest_age = age
            return newest_age

    def remove(self, account_id: str) -> bool:
        with self._lock:
            accounts = self._load()
            to_remove = next((a for a in accounts if a["id"] == account_id), None)
            if not to_remove:
                return False

            new_accounts = [a for a in accounts if a["id"] != account_id]
            self._save(new_accounts)

            # 记录被删除的特征，5 分钟内防止被后台代理被动请求立即回写
            now = time.time()
            if to_remove.get("token"):
                self._recently_removed_tokens[str(to_remove["token"])] = now
            if to_remove.get("uin"):
                self._recently_removed_tokens[str(to_remove["uin"])] = now

            # 同步清理旧 legacy 配置文件中的残留凭证，避免重启时被自动迁移复活
            try:
                legacy = load_json(CONFIG_FILE, {})
                if legacy:
                    legacy_token = legacy.get("token")
                    if not new_accounts or (legacy_token and legacy_token == to_remove.get("token")):
                        save_json(CONFIG_FILE, {})
            except Exception as ex:
                logger.warning("同步清理旧配置文件异常: %s", ex)

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

    def try_refresh_account(self, account_id: str) -> bool:
        """当账号失效时，尝试通过代理对微信后台发起轻量探针请求，触发 mitm_proxy 捕获最新的 Set-Cookie / Token。
        仅当探针触发 mitm_proxy 捕获了新凭证或验证 Session 真实有效时才重置为 active。"""
        with self._lock:
            accounts = self._load()
            acc = next((a for a in accounts if a["id"] == account_id), None)
            if not acc:
                return False
            cookie_str = acc.get("cookie_str", "")
            ua = acc.get("user_agent") or get_default_wechat_ua()
            old_save_time = acc.get("save_time", 0)

        is_valid_session = False
        if cookie_str:
            try:
                from backend.config import get_proxies_dict
                import requests
                headers = {"User-Agent": ua, "Cookie": cookie_str}
                proxies = get_proxies_dict()
                resp = requests.get(
                    "https://mp.weixin.qq.com/cgi-bin/home?t=home/index",
                    headers=headers,
                    proxies=proxies,
                    timeout=8,
                    verify=False
                )

                resp_text = resp.text if hasattr(resp, "text") else ""
                is_valid_session = (
                    resp.status_code == 200 and
                    "redirect_url" not in resp_text and
                    "/cgi-bin/bizlogin" not in resp_text and
                    "window.cgiData" in resp_text
                )
            except Exception as e:
                logger.debug("账号 [%s] HTTP 刷新探针执行失败: %s", account_id, e)

        with self._lock:
            accs = self._load()
            updated_acc = next((a for a in accs if a["id"] == account_id), None)
            if updated_acc:
                new_save_time = updated_acc.get("save_time", 0)
                # 1. 如果请求探针过程中 mitm_proxy 成功捕获到了最新凭证（save_time 发生更新）
                if new_save_time > old_save_time:
                    updated_acc["status"] = "active"
                    updated_acc["failures"] = 0
                    updated_acc["last_error"] = None
                    self._save(accs)
                    logger.info("账号 [%s] 探针成功触发 mitm_proxy 捕获新凭证并恢复 active", updated_acc.get("nickname"))
                    return True

                # 2. 如果探针响应证实 Web Session 真实有效
                if is_valid_session:
                    updated_acc["save_time"] = time.time()
                    updated_acc["status"] = "active"
                    updated_acc["failures"] = 0
                    updated_acc["last_error"] = None
                    self._save(accs)
                    logger.info("账号 [%s] 探针验证 Web Session 有效，恢复 active", updated_acc.get("nickname"))
                    return True

                # 3. 若探针未成功捕获新凭证，且开启了主动自动化刷新，才尝试调用客户端 UI 自动化刷新脚本
                try:
                    from scripts.auto_refresh_pc_wechat import trigger_pc_wechat_refresh, ENABLE_BACKGROUND_ACTIVE_REFRESH
                    if ENABLE_BACKGROUND_ACTIVE_REFRESH:
                        trigger_pc_wechat_refresh()
                        time.sleep(1.5)
                        # 再次检测 save_time 是否因为 UI 刷新被 mitm_proxy 捕获更新
                        accs_recheck = self._load()
                        recheck_acc = next((a for a in accs_recheck if a["id"] == account_id), None)
                        if recheck_acc and recheck_acc.get("save_time", 0) > old_save_time:
                            recheck_acc["status"] = "active"
                            recheck_acc["failures"] = 0
                            recheck_acc["last_error"] = None
                            self._save(accs_recheck)
                            logger.info("账号 [%s] UI 自动化刷新成功捕获新凭证并恢复 active", recheck_acc.get("nickname"))
                            return True
                except Exception as refresh_err:
                    logger.debug("触发客户端自动化刷新失败: %s", refresh_err)

                # 4. 探针与 UI 自动化均未抓到新凭证：确认为 invalid
                updated_acc["status"] = "invalid"
                updated_acc["last_error"] = "客户端凭证/Session已失效，请在 PC 微信或浏览器中刷新页面更新 key/token"
                self._save(accs)
                return False

        return False


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
    # 只要 account_pool.json 存在过（即使为空列表 []），说明用户已经在使用账号池管理，不再进行旧配置回退迁移
    if ACCOUNT_POOL_FILE.exists():
        return

    legacy = load_json(CONFIG_FILE, {})
    if legacy and legacy.get("token"):
        account_info = legacy.get("account_info", {})
        account_pool.add_or_update({
            "token": legacy["token"],
            "cookie_str": legacy.get("cookie_str", ""),
            "cookies": legacy.get("cookies", []),
            "nickname": account_info.get("nickname", "公众号未命名"),
            "avatar": account_info.get("avatar", ""),
            "save_time": legacy.get("save_time", time.time()),
            "is_explicit_login": True,
        })
        logger.info("已将旧 wechat_mp_config.json 迁移到账号池")

