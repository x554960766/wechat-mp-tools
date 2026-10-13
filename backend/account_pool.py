"""
账号池模块
管理多个微信读书账号凭证的存储、调度（acquire）、状态上报（report）、自动刷新（refresh）与增删改查。
支持长效 Token (refreshToken) 纯 Python 毫秒级自动静默续期。
"""

import os
import json
import time
import uuid
import hashlib
import logging
import threading
from pathlib import Path

from backend.config import (
    ACCOUNT_POOL_FILE, CONFIG_FILE, DATA_DIR,
    load_json, save_json, get_proxies_dict
)
from backend.weread_signer import refresh_access_token

logger = logging.getLogger(__name__)

LOGIN_VALID_SECONDS = 30 * 24 * 60 * 60   # 凭证保存周期
COOLDOWN_SECONDS = 5 * 60                 # 频控单次冷却 5 分钟
FAILURE_KICK_THRESHOLD = 5                # 连续失败 5 次踢出


def _gen_id(vid: str = "") -> str:
    """生成稳定唯一的账号 id"""
    if vid:
        return f"acc_weread_{vid}"
    suffix = uuid.uuid4().hex[:6]
    return f"acc_{int(time.time())}_{suffix}"


RANDOM_DEVICE_MODELS = [
    ("Xiaomi 14", "Xiaomi", "34"),
    ("Xiaomi 14 Pro", "Xiaomi", "34"),
    ("Xiaomi 13", "Xiaomi", "33"),
    ("Redmi K70 Pro", "Xiaomi", "34"),
    ("HUAWEI Mate 60 Pro", "HUAWEI", "33"),
    ("HUAWEI Pura 70 Pro", "HUAWEI", "34"),
    ("vivo X100", "vivo", "34"),
    ("vivo X90 Pro", "vivo", "33"),
    ("OPPO Find X7", "OPPO", "34"),
    ("OPPO Reno11 Pro", "OPPO", "34"),
    ("Honor Magic6 Pro", "HONOR", "34"),
    ("OnePlus 12", "OnePlus", "34"),
    ("Samsung Galaxy S24", "samsung", "34"),
    ("Samsung Galaxy S23", "samsung", "33"),
]


def generate_random_device_profile() -> dict:
    """为账号生成独一无二的随机 Android 设备指纹（一号一机，防关联）"""
    import random
    model, brand, _ = random.choice(RANDOM_DEVICE_MODELS)
    device_id = hashlib.md5(uuid.uuid4().bytes).hexdigest()
    install_id = str(uuid.uuid4())
    ua = "WeRead/7.5.0 (Android; 33; arm64-v8a)"
    return {
        "deviceId": device_id,
        "deviceName": model,
        "brand": brand,
        "installId": install_id,
        "deviceType": 1,
        "userAgent": ua,
    }


class AccountPool:
    """微信读书账号池：存储、调度、状态上报、自动静默续期、增删改查。全局单例。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._kick_events: list[dict] = []
        self._recently_removed_ids: dict[str, float] = {}

    # ── 存储与加载 ─────────────────────────────────────

    def _load(self) -> list:
        accounts = load_json(ACCOUNT_POOL_FILE, [])
        if not isinstance(accounts, list):
            accounts = []

        # 过滤掉旧版非微信读书账号 (既无 vid 也无 refreshToken)
        weread_accounts = [a for a in accounts if isinstance(a, dict) and (a.get("vid") or a.get("refreshToken"))]
        if len(weread_accounts) != len(accounts):
            accounts = weread_accounts
            save_json(ACCOUNT_POOL_FILE, accounts)

        # 检查是否需要从本地 weread_web/.session.json 自动初始化导入已有登录会话
        if not accounts:
            weread_session = self._try_find_local_session()
            if weread_session:
                accounts.append(weread_session)
                save_json(ACCOUNT_POOL_FILE, accounts)

        return accounts

    def _save(self, accounts: list):
        save_json(ACCOUNT_POOL_FILE, accounts)

    def _try_find_local_session(self) -> dict | None:
        """尝试读取用户本地破甲一键通 weread_web 的 session 文件快速导入"""
        candidate_paths = [
            Path("/Users/apple/Downloads/破甲一键通-v7.5/weread_web/.session.json"),
            DATA_DIR / ".session.json",
        ]
        for p in candidate_paths:
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    vid = str(data.get("vid", ""))
                    token = data.get("accessToken", "") or data.get("skey", "")
                    refresh_token = data.get("refreshToken", "")
                    user = data.get("user", {})
                    if vid and (token or refresh_token):
                        did = data.get("deviceId", "410515152")
                        return {
                            "id": _gen_id(vid),
                            "vid": vid,
                            "token": token,
                            "accessToken": token,
                            "refreshToken": refresh_token,
                            "skey": data.get("skey", token),
                            "deviceId": did,
                            "nickname": user.get("name") or f"微信读书用户_{vid}",
                            "avatar": user.get("avatar", ""),
                            "cookie_str": f"wr_vid={vid}; wr_skey={token}",
                            "status": "active",
                            "failures": 0,
                            "save_time": time.time(),
                            "last_used": time.time(),
                            "last_error": None,
                        }
                except Exception as e:
                    logger.debug("读取本地 session 异常: %s", e)
        return None

    # ── 调度 ──────────────────────────────────────────

    def acquire(self) -> dict | None:
        """
        选出一个可用微信读书账号并返回其副本。
        支持冷却自愈与自动静默刷新。
        """
        now = time.time()
        with self._lock:
            accounts = self._load()
            changed = False

            for acc in accounts:
                if acc.get("status") == "cooldown" and now >= acc.get("cooldown_until", 0):
                    acc["status"] = "active"
                    acc["cooldown_until"] = 0
                    changed = True

            active = [a for a in accounts if a.get("status") == "active"]
            if not active:
                # 尝试自动刷新 invalid 账号（只要有 refreshToken）
                invalid_with_rf = [a for a in accounts if a.get("refreshToken") and a.get("status") != "cooldown"]
                for acc in invalid_with_rf:
                    if self._refresh_account_locked(acc):
                        active.append(acc)
                        changed = True
                        break

            if not active:
                # 若所有活跃账号均处于临时冷却中，取冷却剩余时间最短的账号兜底保活，避免阻断 mp/cover 等保底通道
                cooldown_accs = [a for a in accounts if a.get("status") == "cooldown"]
                if cooldown_accs:
                    cooldown_accs.sort(key=lambda a: a.get("cooldown_until", 0))
                    selected = cooldown_accs[0]
                    selected["last_used"] = now
                    changed = True
                    if changed:
                        self._save(accounts)
                    return dict(selected)
                if changed:
                    self._save(accounts)
                return None

            # 按 (失败次数, 最久未用) 排序调度
            active.sort(key=lambda a: (a.get("failures", 0), a.get("last_used", 0)))
            selected = active[0]
            selected["last_used"] = now
            changed = True

            if changed:
                self._save(accounts)
            return dict(selected)

    def acquire_for_biz(self, biz: str) -> dict | None:
        """所有微信读书账号对各公众号均为通用读取，直接调用 acquire()"""
        return self.acquire()

    # ── 状态上报与熔断 ────────────────────────────────

    def report(self, account_id: str, *, ret: int | None = None,
               status_code: int | None = None, is_success: bool | None = None,
               error_msg: str | None = None, need_refresh: bool = False):
        """
        上报账号使用结果。遇到 401 自动触发静默刷新。
        """
        now = time.time()
        with self._lock:
            accounts = self._load()
            acc = next((a for a in accounts if a.get("id") == account_id), None)
            if not acc:
                return

            if is_success:
                acc["failures"] = 0
                acc["last_error"] = None
                self._save(accounts)
                return

            # 处理 401 认证过期 / 显式请求刷新
            if status_code == 401 or ret in (401, -2012) or need_refresh:
                logger.info("账号 [%s] 认证过期，正在执行自动静默续期...", acc.get("nickname"))
                refreshed = self._refresh_account_locked(acc)
                if refreshed:
                    logger.info("账号 [%s] 静默续期成功！", acc.get("nickname"))
                    self._save(accounts)
                    return
                else:
                    acc["status"] = "invalid"
                    acc["last_error"] = error_msg or "登录过期且自动续期失败，请重新扫码"
                    self._kick_events.append({
                        "id": acc.get("id"),
                        "nickname": acc.get("nickname"),
                        "reason": "Token 过期续期失败",
                    })
                    self._save(accounts)
                    return

            # 处理 499 -2041 会话未激活：自动发起 mobileSync 激活会话
            if ret == -2041 or (status_code == 499 and "2041" in str(error_msg or "")):
                try:
                    from backend.weread_signer import activate_terminal_session
                    h = {
                        "Cookie": acc.get("cookie_str", f"wr_vid={acc.get('vid')}; wr_skey={acc.get('skey')}"),
                        "vid": str(acc.get("vid")),
                        "skey": str(acc.get("skey")),
                        "accessToken": str(acc.get("accessToken")),
                    }
                    if activate_terminal_session(h):
                        logger.info("账号 [%s] 触发 -2041，已自动执行 mobileSync 激活服务端通道", acc.get("nickname"))
                        acc["last_error"] = None
                        self._save(accounts)
                        return
                except Exception as ex:
                    logger.debug("自动激活异常: %s", ex)

            # 处理频控或风控 (如 429 或限流或 499 临时抖动)
            if status_code in (429, 403, 499) or ret in (200013, -2013, -2041):
                acc["status"] = "cooldown"
                acc["cooldown_until"] = now + 120.0  # 临时冷却 120 秒后自动恢复
                acc["last_error"] = error_msg or f"请求频控或连接异常 (HTTP {status_code})，临时冷却"
                self._save(accounts)
                return

            # 普通失败累加
            acc["failures"] = acc.get("failures", 0) + 1
            acc["last_error"] = error_msg or f"请求异常 (HTTP {status_code})"
            if acc["failures"] >= FAILURE_KICK_THRESHOLD:
                acc["status"] = "invalid"
                self._kick_events.append({
                    "id": acc.get("id"),
                    "nickname": acc.get("nickname"),
                    "reason": f"连续失败超过 {FAILURE_KICK_THRESHOLD} 次",
                })
            self._save(accounts)

    # ── 纯 Python 自动刷新核心逻辑 ───────────────────

    def _refresh_account_locked(self, acc: dict) -> bool:
        """持锁状态下调用 weread_signer 换取最新 accessToken 并就地更新"""
        rf = acc.get("refreshToken", "")
        if not rf:
            return False

        did = acc.get("deviceId") or "410515152"
        dname = acc.get("deviceName") or "Android"
        proxies = get_proxies_dict()

        status, res = refresh_access_token(rf, device_id=did, device_name="Android", proxies=proxies)
        if status == 200 and isinstance(res, dict) and res.get("accessToken"):
            new_token = res["accessToken"]
            acc["accessToken"] = new_token
            acc["token"] = new_token
            acc["skey"] = res.get("skey", new_token)
            if res.get("refreshToken"):
                acc["refreshToken"] = res["refreshToken"]
            vid = str(acc.get("vid") or res.get("vid", ""))
            if vid:
                acc["vid"] = vid
                acc["cookie_str"] = f"wr_vid={vid}; wr_skey={acc['skey']}"

            acc["userAgent"] = "WeRead/7.5.0 (Android; 33; arm64-v8a)"

            user = res.get("user")
            if isinstance(user, dict):
                if user.get("name"):
                    acc["nickname"] = user["name"]
                if user.get("avatar"):
                    acc["avatar"] = user["avatar"]

            acc["status"] = "active"
            acc["failures"] = 0
            acc["last_error"] = None
            acc["save_time"] = time.time()

            # 同步写回本地 weread_web/.session.json，确保双向一致
            try:
                local_sess = Path("/Users/apple/Downloads/破甲一键通-v7.5/weread_web/.session.json")
                if local_sess.exists():
                    sess_data = json.loads(local_sess.read_text(encoding="utf-8"))
                    sess_data["accessToken"] = new_token
                    if res.get("refreshToken"):
                        sess_data["refreshToken"] = res["refreshToken"]
                    if res.get("skey"):
                        sess_data["skey"] = res["skey"]
                    if did:
                        sess_data["deviceId"] = did
                    if vid:
                        sess_data["vid"] = int(vid) if str(vid).isdigit() else vid
                    local_sess.write_text(json.dumps(sess_data, ensure_ascii=False, indent=2), encoding="utf-8")
                    logger.info("已将刷新后的 token 同步写入 weread_web/.session.json")
            except Exception as e:
                logger.debug("同步 weread_web/.session.json 失败: %s", e)

            # 换票成功后立即激活移动端会话 (shelf/sync 与 mp/list)，解除 -2041 拦截
            try:
                from backend.weread_signer import activate_terminal_session
                auth_h = {
                    "accessToken": new_token,
                    "vid": str(vid),
                    "basekey": new_token,
                    "deviceId": did,
                    "User-Agent": acc.get("userAgent", "WeRead/7.5.0 (Android; 33; arm64-v8a)"),
                    "Cookie": acc.get("cookie_str", f"wr_vid={vid}; wr_skey={new_token}")
                }
                activate_terminal_session(auth_h)
            except Exception as e:
                logger.debug("激活终端会话异常: %s", e)

            return True
        else:
            logger.warning("账号 [%s] 刷新 Token 失败: status=%s, res=%s", acc.get("nickname"), status, res)
            return False

    def try_refresh_account(self, account_id: str) -> bool:
        """对外接口：刷新指定账号"""
        with self._lock:
            accounts = self._load()
            acc = next((a for a in accounts if a.get("id") == account_id), None)
            if not acc:
                return False
            ok = self._refresh_account_locked(acc)
            if ok:
                self._save(accounts)
            return ok

    def verify_account(self, account_id: str) -> dict:
        """验证并更新单个账号状态"""
        with self._lock:
            accounts = self._load()
            acc = next((a for a in accounts if a.get("id") == account_id), None)
            if not acc:
                return {"status": "not_found", "message": "未找到该账号"}

            rf = acc.get("refreshToken", "")
            if rf:
                ok = self._refresh_account_locked(acc)
                self._save(accounts)
                if ok:
                    return {
                        "status": "active",
                        "valid": True,
                        "message": "Token 自动刷新成功，账号正常可用",
                        "account": self._desensitize(acc),
                    }

            # 如果没有 refreshToken，简单通过接口探活
            token = acc.get("accessToken") or acc.get("token")
            vid = acc.get("vid")
            if token and vid:
                headers = {
                    "User-Agent": "WeRead/7.5.0 (Android; 33; arm64-v8a)",
                    "vid": str(vid),
                    "accessToken": str(token),
                }
                try:
                    s = get_weread_session()
                    resp = s.get("https://i.weread.qq.com/mp/list?listType=0&count=1", headers=headers, timeout=8)
                    if resp.status_code == 200:
                        acc["status"] = "active"
                        acc["failures"] = 0
                        self._save(accounts)
                        return {"status": "active", "valid": True, "message": "凭据验证有效"}
                except Exception as e:
                    return {"status": acc.get("status"), "valid": False, "message": f"探活请求失败: {e}"}

            acc["status"] = "invalid"
            acc["last_error"] = "凭据已失效且无 refreshToken，需重新扫码"
            self._save(accounts)
            return {"status": "invalid", "valid": False, "message": "凭据已失效，请重新扫码登录"}

    def browser_refresh_account(self, account_id: str) -> dict:
        """兼容接口：执行毫秒级纯 Python 算法刷新"""
        ok = self.try_refresh_account(account_id)
        return {
            "ok": ok,
            "message": "账号 Token 刷新成功" if ok else "刷新失败，请重新扫码登录",
            "needs_scan": not ok,
        }

    def verify_all(self) -> list:
        """批量检测并刷新所有账号"""
        with self._lock:
            accounts = self._load()
            results = []
            for acc in accounts:
                if acc.get("refreshToken"):
                    self._refresh_account_locked(acc)
                results.append(self._desensitize(acc))
            self._save(accounts)
            return results

    # ── 增删改查 ──────────────────────────────────────

    def add_or_update(self, cred: dict) -> dict:
        """添加或更新微信读书账号"""
        with self._lock:
            accounts = self._load()
            vid = str(cred.get("vid", "")).strip()
            token = cred.get("accessToken") or cred.get("token") or cred.get("skey", "")
            rf = cred.get("refreshToken", "")
            nickname = cred.get("nickname") or cred.get("user", {}).get("name") or (f"微信读书用户_{vid}" if vid else "微信读书账号")
            avatar = cred.get("avatar") or cred.get("user", {}).get("avatar", "")
            # 设备指纹：一号一机，若无则自动随机生成专属机型与设备号绑定
            device_profile = cred.get("device_profile")
            if not device_profile and (not cred.get("deviceName") or not cred.get("deviceId")):
                device_profile = generate_random_device_profile()
            else:
                device_profile = device_profile or {}

            did = cred.get("deviceId") or device_profile.get("deviceId") or "410515152"
            dname = cred.get("deviceName") or device_profile.get("deviceName") or "Xiaomi 14"
            install_id = cred.get("installId") or device_profile.get("installId") or str(uuid.uuid4())
            ua = cred.get("userAgent") or device_profile.get("userAgent") or "WeRead/7.5.0 (Android; 33; arm64-v8a)"

            target_acc = None
            if vid:
                target_acc = next((a for a in accounts if str(a.get("vid")) == vid), None)
            if not target_acc and rf:
                target_acc = next((a for a in accounts if a.get("refreshToken") == rf), None)

            now = time.time()
            if target_acc:
                target_acc["vid"] = vid or target_acc.get("vid", "")
                target_acc["accessToken"] = token or target_acc.get("accessToken", "")
                target_acc["token"] = token or target_acc.get("token", "")
                target_acc["skey"] = cred.get("skey") or target_acc.get("skey", token)
                if rf:
                    target_acc["refreshToken"] = rf
                if did:
                    target_acc["deviceId"] = did
                if not target_acc.get("deviceName"):
                    target_acc["deviceName"] = dname
                if not target_acc.get("installId"):
                    target_acc["installId"] = install_id
                if not target_acc.get("userAgent"):
                    target_acc["userAgent"] = ua
                if nickname:
                    target_acc["nickname"] = nickname
                if avatar:
                    target_acc["avatar"] = avatar
                target_acc["cookie_str"] = f"wr_vid={target_acc.get('vid')}; wr_skey={target_acc.get('skey')}"
                target_acc["status"] = "active"
                target_acc["failures"] = 0
                target_acc["last_error"] = None
                target_acc["save_time"] = now
                self._save(accounts)
                logger.info("已更新微信读书账号: %s (VID: %s, 设备: %s)", nickname, vid, target_acc.get("deviceName"))
                return target_acc
            else:
                acc_id = _gen_id(vid)
                new_acc = {
                    "id": acc_id,
                    "vid": vid,
                    "accessToken": token,
                    "token": token,
                    "refreshToken": rf,
                    "skey": cred.get("skey", token),
                    "deviceId": did,
                    "deviceName": dname,
                    "installId": install_id,
                    "userAgent": ua,
                    "nickname": nickname,
                    "avatar": avatar,
                    "cookie_str": f"wr_vid={vid}; wr_skey={token}",
                    "status": "active",
                    "failures": 0,
                    "save_time": now,
                    "last_used": now,
                    "cooldown_until": 0.0,
                    "last_error": None,
                }
                accounts.insert(0, new_acc)
                self._save(accounts)
                logger.info("已新增微信读书账号: %s (VID: %s, 专属绑定设备: %s)", nickname, vid, dname)
                return new_acc

    def list_accounts(self) -> list:
        """获取所有账号（脱敏输出）"""
        with self._lock:
            accounts = self._load()
            return [self._desensitize(a) for a in accounts]

    def remove(self, account_id: str) -> bool:
        """删除指定账号"""
        with self._lock:
            accounts = self._load()
            before_len = len(accounts)
            accounts = [a for a in accounts if a.get("id") != account_id]
            if len(accounts) < before_len:
                self._recently_removed_ids[account_id] = time.time()
                self._save(accounts)
                return True
            return False

    def revive(self, account_id: str) -> bool:
        """重新激活账号"""
        with self._lock:
            accounts = self._load()
            acc = next((a for a in accounts if a.get("id") == account_id), None)
            if not acc:
                return False
            acc["status"] = "active"
            acc["failures"] = 0
            acc["cooldown_until"] = 0
            acc["last_error"] = None
            self._save(accounts)
            return True

    def update_account_info(self, account_id: str, data: dict) -> dict | None:
        """更新账号备注名等信息"""
        with self._lock:
            accounts = self._load()
            acc = next((a for a in accounts if a.get("id") == account_id), None)
            if not acc:
                return None
            if "nickname" in data:
                acc["nickname"] = str(data["nickname"]).strip()
            self._save(accounts)
            return self._desensitize(acc)

    def get_active_count(self) -> int:
        with self._lock:
            accounts = self._load()
            return sum(1 for a in accounts if a.get("status") == "active")

    def get_active_accounts(self) -> list:
        with self._lock:
            accounts = self._load()
            return [a for a in accounts if a.get("status") == "active"]

    def get_summary(self) -> dict:
        with self._lock:
            accounts = self._load()
            total = len(accounts)
            active = sum(1 for a in accounts if a.get("status") == "active")
            cooldown = sum(1 for a in accounts if a.get("status") == "cooldown")
            invalid = sum(1 for a in accounts if a.get("status") == "invalid")
            return {
                "total": total,
                "active": active,
                "cooldown": cooldown,
                "invalid": invalid,
                "banned": 0,
            }

    def pop_kick_events(self) -> list:
        with self._lock:
            events = list(self._kick_events)
            self._kick_events.clear()
            return events

    @staticmethod
    def get_biz_credential(account_data: dict, biz: str) -> dict:
        """兼容层：微信读书账号返回基础鉴权"""
        token = account_data.get("accessToken") or account_data.get("token", "")
        return {
            "token": token,
            "appmsg_token": token,
            "cookie_str": account_data.get("cookie_str", ""),
            "vid": account_data.get("vid", ""),
        }

    def _desensitize(self, acc: dict) -> dict:
        token = acc.get("accessToken") or acc.get("token") or ""
        token_preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "***"
        rf = acc.get("refreshToken", "")
        has_refresh = bool(rf)
        return {
            "id": acc.get("id"),
            "vid": acc.get("vid", ""),
            "nickname": acc.get("nickname", "微信读书账号"),
            "avatar": acc.get("avatar", ""),
            "status": acc.get("status", "active"),
            "deviceName": acc.get("deviceName", "Xiaomi 14"),
            "deviceId": acc.get("deviceId", ""),
            "token_preview": token_preview,
            "has_refresh_token": has_refresh,
            "failures": acc.get("failures", 0),
            "save_time": acc.get("save_time", 0),
            "last_used": acc.get("last_used", 0),
            "cooldown_until": acc.get("cooldown_until", 0),
            "last_error": acc.get("last_error"),
        }


# ── 全局单例 ──────────────────────────────────────────

account_pool = AccountPool()


def borrow_weread_session() -> tuple[str, str, str, dict]:
    """
    调度可用账号，返回 (account_id, vid, accessToken, auth_headers)。
    遇到无可用账号抛出 RuntimeError。
    """
    acc = account_pool.acquire()
    if not acc:
        raise RuntimeError("微信读书账号池中暂无可用账号，请先在『账号池』页面扫码登录")
    account_id = acc["id"]
    vid = str(acc.get("vid", ""))
    token = acc.get("accessToken") or acc.get("token", "")
    skey = acc.get("skey") or token
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    headers = {
        "User-Agent": ua,
        "Content-Type": "application/json; charset=UTF-8",
        "vid": vid,
        "accessToken": token,
        "skey": skey,
        "Cookie": f"wr_vid={vid}; wr_skey={skey}",
    }
    return account_id, vid, token, headers


def borrow_session() -> tuple[str, str, str]:
    """向后兼容返回 (account_id, token, cookie_str)"""
    account_id, vid, token, headers = borrow_weread_session()
    cookie_str = headers.get("Cookie", f"wr_vid={vid}; wr_skey={token}")
    return account_id, token, cookie_str


def migrate_legacy_config():
    """应用启动时执行一次：检查是否已有微信读书账号，若无则尝试迁移本地会话"""
    try:
        pool = account_pool._load()
        if not pool:
            imported = account_pool._try_find_local_session()
            if imported:
                logger.info("已自动载入本地微信读书会话 (VID: %s)", imported.get("vid"))
    except Exception as e:
        logger.debug("迁移旧配置异常: %s", e)


_weread_session = None
_weread_session_lock = threading.Lock()


def get_weread_session():
    """
    获取专用于微信读书请求的 requests.Session。
    关键：强制设置 trust_env=False 绕过本地代理（如 Clash 7897），
    并配置长连接池，彻底避免并发请求时因代理中转引发连接重置或 HTTP 499 报错。
    """
    global _weread_session
    if _weread_session is None:
        with _weread_session_lock:
            if _weread_session is None:
                import requests
                from requests.adapters import HTTPAdapter
                s = requests.Session()
                s.trust_env = False
                adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                _weread_session = s
    return _weread_session
