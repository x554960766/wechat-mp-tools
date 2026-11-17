"""
微信读书加签算法纯 Python 实现
实现 weread::GenSignature 与 nativeGetSignatures 完整算法
支持长效 Token (refreshToken) 自动换取最新 accessToken、guest_token 同步、主动续期与单飞刷新
"""

import os
import json
import hashlib
import time
import random
import threading
import requests

# 对齐微信读书官方客户端 (10.2.2 / 10167650) 请求头规范
APK_VERSION = "10.2.2"
APK_VERSION_CODE = "10167650"
APK_FULL_VERSION = f"{APK_VERSION}.{APK_VERSION_CODE}"
DEFAULT_DEVICE_NAME = "Xiaomi 13"
# 源自 FeatureGuestTokenWrapper / GET /feature?synckey=0 默认值
DEFAULT_GUEST_TOKEN = "5ecdcfd7f"
GUEST_TOKEN_TTL = 6 * 3600
# >40min 后台切回主动刷新 & 10s 单飞刷新去重
PROACTIVE_REFRESH_INTERVAL = 40 * 60
MIN_REFRESH_DEDUP_SECONDS = 10

APK_USER_AGENT = (
    f"WeRead/{APK_VERSION} WRBrand/Xiaomi "
    "Dalvik/2.1.0 (Linux; U; Android 13; 2211133C Build/TKQ1.220905.001)"
)
APK_CLIENT_HEADERS = {
    "User-Agent": APK_USER_AGENT,
    "osver": "13",
    "appver": APK_FULL_VERSION,
    "basever": APK_FULL_VERSION,
    "channelId": "990",
    "baseapi": "33",
    "Content-Type": "application/json; charset=UTF-8",
}

_GUEST_TOKEN_LOCK = threading.Lock()
_GUEST_TOKEN_CACHE = {
    "token": DEFAULT_GUEST_TOKEN,
    "ts": 0.0,
}

_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH_META: dict[str, dict] = {}

_ACTIVE_SYNC_LOCK = threading.Lock()
_LAST_ACTIVE_SYNC_AT: dict[str, float] = {}

# 对齐官方客户端全局节流与短时频控冷却，避免瞬时并发冲击 i.weread.qq.com
WEREAD_MIN_INTERVAL = 0.35
_PACE_LOCK = threading.Lock()
_LAST_WEREAD_REQ_AT = 0.0

RATE_LIMIT_COOLDOWN = 15
_RATE_LIMIT_LOCK = threading.Lock()
RATE_LIMIT = {
    "until": 0.0,
    "hits": 0,
    "last_url": "",
}


class WeReadRateLimitError(RuntimeError):
    """触发微信读书 -2014 ErrorOverFreq 频控时的显式异常。"""

    def __init__(self, msg='HTTP 499: {"errcode":-2014,"errmsg":"请求频率过高"}', cooldown_left=0):
        super().__init__(msg)
        self.errcode = -2014
        self.cooldown_left = int(max(0, cooldown_left))


def pace_weread_request(url: str):
    """对 i.weread.qq.com 请求施加最小间隔 + 随机抖动，避免并发冲击频控阈值。"""
    if "i.weread.qq.com" not in (url or ""):
        return
    global _LAST_WEREAD_REQ_AT
    interval = WEREAD_MIN_INTERVAL + random.uniform(0.05, 0.18)
    with _PACE_LOCK:
        now = time.time()
        target = max(now, _LAST_WEREAD_REQ_AT + interval)
        _LAST_WEREAD_REQ_AT = target
        wait_s = target - now
    if wait_s > 0:
        time.sleep(wait_s)


def get_rate_limit_status() -> tuple[bool, int]:
    """返回当前是否处于 -2014 本地冷却窗口及剩余秒数。"""
    with _RATE_LIMIT_LOCK:
        left = max(0, int(round(RATE_LIMIT["until"] - time.time())))
        return left > 0, left


def mark_rate_limited(url: str, cooldown: int = RATE_LIMIT_COOLDOWN) -> int:
    """登记 -2014 频控冷却窗口（对齐官方重试策略，不盲目重放且不误弹防水墙）。"""
    with _RATE_LIMIT_LOCK:
        RATE_LIMIT["until"] = max(RATE_LIMIT["until"], time.time() + cooldown)
        RATE_LIMIT["hits"] += 1
        RATE_LIMIT["last_url"] = url or ""
        return max(0, int(round(RATE_LIMIT["until"] - time.time())))


def clear_rate_limit():
    """重置本地 -2014 频控冷却窗口。"""
    with _RATE_LIMIT_LOCK:
        RATE_LIMIT["until"] = 0.0


def build_apk_headers(extra: dict = None) -> dict:
    """构建对齐微信读书官方客户端的基础请求头。"""
    headers = dict(APK_CLIENT_HEADERS)
    if extra:
        headers.update(extra)
    return headers


def sanitize_apk_headers(headers: dict = None) -> dict:
    """确保传给 i.weread.qq.com 的请求头与官方客户端登录态规范一致。"""
    h = build_apk_headers(headers)
    if h.get("accessToken"):
        # 官方客户端接口仅传 vid 与 accessToken，不附带 Web 端冲突的 skey/Cookie
        if h.get("skey") == h.get("accessToken"):
            h.pop("skey", None)
        cookie = h.get("Cookie", "")
        if "wr_vid=" in cookie or "wr_skey=" in cookie:
            h.pop("Cookie", None)
    return h


# libencrypt.so 内置 256 字节 S-Box 置换盒 (偏移 0xd9fc)
SBOX = [
    52, 202, 85, 64, 29, 182, 147, 198, 49, 48, 41, 53, 50, 167, 184, 17,
    194, 181, 22, 250, 139, 177, 36, 164, 16, 144, 4, 233, 8, 248, 59, 138,
    156, 140, 68, 249, 188, 92, 105, 226, 161, 218, 210, 211, 117, 137, 247, 30,
    45, 80, 86, 215, 114, 83, 191, 34, 251, 32, 15, 1, 46, 69, 135, 110,
    102, 72, 242, 224, 205, 254, 103, 169, 67, 244, 148, 81, 206, 165, 74, 238,
    19, 38, 142, 204, 170, 51, 20, 93, 14, 57, 187, 207, 145, 43, 129, 77,
    234, 153, 236, 26, 44, 133, 197, 217, 54, 116, 75, 24, 225, 241, 61, 157,
    65, 159, 180, 23, 13, 214, 76, 190, 220, 175, 151, 40, 119, 240, 98, 255,
    113, 193, 200, 39, 143, 108, 104, 168, 155, 230, 89, 28, 27, 18, 9, 152,
    78, 63, 6, 55, 0, 186, 31, 10, 25, 47, 201, 213, 208, 87, 73, 111,
    253, 37, 228, 97, 12, 66, 203, 150, 100, 95, 219, 173, 96, 35, 141, 154,
    109, 195, 196, 94, 62, 185, 146, 106, 189, 91, 7, 127, 118, 149, 237, 79,
    171, 132, 122, 128, 231, 120, 199, 229, 235, 115, 131, 107, 252, 56, 70, 125,
    71, 101, 179, 82, 99, 58, 5, 209, 239, 163, 166, 222, 158, 60, 2, 174,
    178, 123, 160, 246, 243, 42, 192, 172, 134, 3, 90, 84, 11, 245, 130, 212,
    126, 227, 223, 176, 216, 221, 33, 232, 124, 136, 162, 121, 88, 112, 183, 21
]

# libencrypt.so 偏移 0xdafc 处的固定私钥盐
SALT = b"5a6f1"


def remap_string(s: str) -> bytes:
    """weread::RemapString"""
    raw_bytes = s.encode("utf-8")
    return bytes(SBOX[b] for b in raw_bytes)


def gen_signature(str_list: list) -> str:
    """
    纯 Python 复刻 Java_weread_encrypt_EncryptUtils_nativeGetSignatures
    """
    # 1. 外部字符串逐个通过 SBOX 字节置换
    remapped_items = [remap_string(s) for s in str_list]

    # 2. 插入内置私钥盐值
    remapped_items.append(SALT)

    # 3. 按 C++ std::string 字节序字典升序排序 (std::sort)
    remapped_items.sort()

    # 4. 拼接
    joined = b"".join(remapped_items)
    n = len(joined)
    if n == 0:
        return ""

    # 5. 第一轮异或累加和与循环移位
    xor1 = 0
    for b in joined:
        xor1 ^= b
    shift1 = xor1 % 11

    shifted1 = bytearray(n)
    for i in range(n):
        shifted1[(i + shift1) % n] = joined[i]

    # 6. 第一轮 SHA256 并格式化为 64 字节小写 hex
    hash1 = hashlib.sha256(shifted1).hexdigest().encode("ascii")

    # 7. 第二轮异或累加和与循环移位
    n2 = len(hash1)  # 64
    xor2 = 0
    for b in hash1:
        xor2 ^= b
    shift2 = xor2 % 11

    shifted2 = bytearray(n2)
    for i in range(n2):
        shifted2[(i + shift2) % n2] = hash1[i]

    # 8. 第二轮 SHA256 最终签名
    signature = hashlib.sha256(shifted2).hexdigest()
    return signature


def _atomic_write_json(path: str, payload: dict) -> None:
    tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def update_guest_token(token: str) -> str:
    """更新内存中缓存的 guest_token (源自 /feature?synckey=0)。"""
    clean = str(token or "").strip()
    if not clean:
        return get_guest_token(sync_remote=False)
    with _GUEST_TOKEN_LOCK:
        _GUEST_TOKEN_CACHE["token"] = clean
        _GUEST_TOKEN_CACHE["ts"] = time.time()
    return clean


def sync_guest_token(
    headers: dict = None,
    force: bool = False,
    timeout: float = 5.0,
    proxies: dict = None,
) -> str:
    """对齐官方客户端 AccountService.syncFeature：从 GET /feature?synckey=0 同步最新 guest_token。"""
    now = time.time()
    with _GUEST_TOKEN_LOCK:
        cached = _GUEST_TOKEN_CACHE.get("token") or DEFAULT_GUEST_TOKEN
        ts = float(_GUEST_TOKEN_CACHE.get("ts") or 0.0)
        if not force and ts > 0 and (now - ts) < GUEST_TOKEN_TTL:
            return cached

    req_headers = build_apk_headers({"Host": "i.weread.qq.com"})
    if isinstance(headers, dict):
        for k in ("vid", "accessToken"):
            if headers.get(k):
                req_headers[k] = str(headers[k])
    try:
        s = requests.Session()
        if not proxies:
            s.trust_env = False
        u_feature = "https://i.weread.qq.com/feature?synckey=0"
        pace_weread_request(u_feature)
        resp = s.get(u_feature, headers=req_headers, proxies=proxies, timeout=timeout)
        data = resp.json() if resp.status_code == 200 else {}
        if isinstance(data, dict):
            gt = (
                data.get("guest_token")
                or data.get("guestToken")
                or (data.get("feature") or {}).get("guest_token")
            )
            if gt:
                return update_guest_token(str(gt))
    except Exception as e:
        print(f"[GUEST-TOKEN] sync /feature fallback to cached token: {e}")
    return get_guest_token(sync_remote=False)


def get_guest_token(sync_remote: bool = False, force: bool = False, proxies: dict = None) -> str:
    if sync_remote or force:
        return sync_guest_token(force=force, proxies=proxies)
    with _GUEST_TOKEN_LOCK:
        return _GUEST_TOKEN_CACHE.get("token") or DEFAULT_GUEST_TOKEN


def maybe_sync_guest_token_on_retry(prev_guest_token: str, proxies: dict = None) -> str | None:
    """当刷新签名可能过期时按 300s 节流重新同步 /feature?synckey=0，有变化返回新 guest_token。"""
    with _GUEST_TOKEN_LOCK:
        gt_ts = float(_GUEST_TOKEN_CACHE.get("ts") or 0.0)
    if gt_ts == 0.0 or (time.time() - gt_ts) > 300:
        new_gt = sync_guest_token(force=True, proxies=proxies)
        if new_gt and new_gt != prev_guest_token:
            return new_gt
    return None


def refresh_access_token(
    refresh_token: str,
    device_id: str = "410515152",
    device_name: str = DEFAULT_DEVICE_NAME,
    guest_token: str = None,
    wx_token: int = 0,
    in_background: int = 0,
    proxies: dict = None,
) -> tuple[int, dict]:
    """
    使用 refreshToken 自动静默换取最新的 accessToken (1:1 对齐 LoginService.refreshToken)
    支持自定义绑定账号专属设备 ID、设备名称及动态 guest_token
    """
    now_ms = int(time.time() * 1000)
    rand_val = random.randint(0, 999)

    # LoginService.getGuestAntiReplaySignature:
    # strArr = [guestToken (/feature?synckey=0 或默认 "5ecdcfd7f"), deviceId, timestamp, random, refreshToken]
    gt = get_guest_token(sync_remote=False) if guest_token is None else str(guest_token)
    params_for_sig = [
        gt,
        str(device_id),
        str(now_ms),
        str(rand_val),
        str(refresh_token),
    ]
    sig = gen_signature(params_for_sig)

    url = "https://i.weread.qq.com/login"
    payload = {
        "refreshToken": refresh_token,
        "deviceId": str(device_id),
        "wxToken": int(wx_token or 0),
        "inBackground": 1 if in_background else 0,
        "trackId": "",
        "kickType": 1,
        "refCgi": "",
        "timestamp": now_ms,
        "random": rand_val,
        "signature": sig,
        "virtualChannelId": "",
        "deviceName": str(device_name or DEFAULT_DEVICE_NAME),
    }

    headers = build_apk_headers({
        "Host": "i.weread.qq.com"
    })

    try:
        s = requests.Session()
        if not proxies:
            s.trust_env = False
        resp = s.post(url, json=payload, headers=headers, proxies=proxies, timeout=12)
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        return resp.status_code, data
    except Exception as e:
        return 0, {"error": str(e)}


def needs_proactive_refresh(
    sess: dict,
    max_age_seconds: int = PROACTIVE_REFRESH_INTERVAL,
    session_path: str = None,
) -> bool:
    """
    对齐 WRApplicationContext.onActivityStarted:
    只要存在有效 refreshToken、未标记 refreshTokenExpired，且距上次刷新/登录超过 40 分钟即触发主动续期。
    """
    if not isinstance(sess, dict) or not sess.get("refreshToken") or sess.get("refreshTokenExpired"):
        return False
    ref_ts = sess.get("refreshedAt") or sess.get("loginAt") or sess.get("save_time")
    try:
        last_ts = float(ref_ts) if ref_ts else 0.0
    except Exception:
        last_ts = 0.0
    if last_ts <= 0 and session_path and os.path.exists(session_path):
        try:
            last_ts = float(os.path.getmtime(session_path))
        except Exception:
            last_ts = 0.0
    if last_ts <= 0:
        return True
    return (time.time() - last_ts) >= max_age_seconds


def try_refresh_session(
    session_path: str = ".session.json",
    device_id: str = None,
    fallback_device_id: str = None,
    expected_access_token: str = None,
    force: bool = False,
    device_name: str = None,
    in_background: int = 0,
    sync_feature_on_retry: bool = True,
    proxies: dict = None,
) -> dict | None:
    """
    从 session 文件读取 refreshToken 并通过单飞锁静默刷新 accessToken:
    - 并发 401 单飞去重 + 10s 最小刷新冷却 (ReadHttpErrorHandler.minRefreshTokenDuration)
    - guest_token 自动使用 /feature?synckey=0 与 "5ecdcfd7f"，失败时自动同步一次重试
    - 对齐 LoginService.handleRefreshTokenError: -2013 带 forceAccessToken 时保留并标记 refreshTokenExpired
    """
    cache_key = os.path.abspath(session_path)
    with _REFRESH_LOCK:
        if not os.path.exists(session_path):
            print(f"[AUTO-REFRESH] Session file not found: {session_path}")
            return None
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                sess = json.load(f)
        except Exception as e:
            print(f"[AUTO-REFRESH] Read session file error: {e}")
            return None

        if not isinstance(sess, dict):
            return None

        current_tok = sess.get("accessToken") or ""
        # 单飞去重：若另一线程刚完成刷新且 accessToken 已变更，直接复用最新会话
        if (
            expected_access_token
            and current_tok
            and current_tok != expected_access_token
            and not sess.get("refreshTokenExpired")
        ):
            return sess

        if sess.get("refreshTokenExpired") and not force:
            print("[AUTO-REFRESH] refreshToken 已标记失效 (refreshTokenExpired)，等待重新扫码登录")
            return None

        rf = sess.get("refreshToken")
        if not rf:
            print("[AUTO-REFRESH] No refreshToken in session file")
            return None

        meta = _LAST_REFRESH_META.setdefault(cache_key, {})
        now = time.time()
        last_ok_ts = float(meta.get("last_ok_ts") or 0.0)
        last_fail_ts = float(meta.get("last_fail_ts") or 0.0)
        if not force and not expected_access_token and last_ok_ts > 0 and (now - last_ok_ts) < MIN_REFRESH_DEDUP_SECONDS and current_tok:
            return sess
        if not force and last_fail_ts > 0 and (now - last_fail_ts) < MIN_REFRESH_DEDUP_SECONDS:
            if not expected_access_token or meta.get("last_fail_token") == current_tok:
                return None

        did = str(device_id or sess.get("deviceId") or fallback_device_id or "410515152")
        dname = str(device_name or sess.get("deviceName") or DEFAULT_DEVICE_NAME)

        gt_used = get_guest_token(sync_remote=False)
        status_code, res = refresh_access_token(
            rf,
            device_id=did,
            device_name=dname,
            guest_token=gt_used,
            wx_token=0,
            in_background=in_background,
            proxies=proxies,
        )

        # 若签名拒绝且本地 guest_token 可能过时，立即同步一次 /feature?synckey=0；若确实变化则重签重试一次
        errcode = res.get("errcode") if isinstance(res, dict) else None
        if sync_feature_on_retry and not (status_code == 200 and isinstance(res, dict) and res.get("accessToken") and not errcode):
            new_gt = maybe_sync_guest_token_on_retry(gt_used, proxies=proxies)
            if new_gt:
                status_code, res = refresh_access_token(
                    rf,
                    device_id=did,
                    device_name=dname,
                    guest_token=new_gt,
                    wx_token=0,
                    in_background=in_background,
                    proxies=proxies,
                )
                errcode = res.get("errcode") if isinstance(res, dict) else None

        if status_code == 200 and isinstance(res, dict) and res.get("accessToken") and not errcode:
            sess["accessToken"] = res["accessToken"]
            if res.get("refreshToken"):
                sess["refreshToken"] = res["refreshToken"]
            if res.get("skey"):
                sess["skey"] = res["skey"]
            if res.get("wxAccessToken"):
                sess["wxAccessToken"] = res["wxAccessToken"]
            if res.get("openId"):
                sess["openId"] = res["openId"]
            if "pf" in res:
                sess["pf"] = res["pf"]
            if isinstance(res.get("user"), dict) and res["user"]:
                sess["user"] = res["user"]
            sess["deviceId"] = did
            sess["deviceName"] = dname
            sess["refreshTokenExpired"] = False
            sess.pop("lastRefreshError", None)
            now_done = time.time()
            sess["refreshedAt"] = now_done
            sess["lastActiveAt"] = now_done
            _atomic_write_json(session_path, sess)
            meta["last_ok_ts"] = now_done
            meta["last_fail_ts"] = 0.0
            tok_prefix = str(res["accessToken"])[:6]
            print(f"[AUTO-REFRESH] Successfully refreshed accessToken via libencrypt GenSignature! (token={tok_prefix}***)")
            return sess

        # 对齐客户端 LoginService.handleRefreshTokenError (-2013):
        # 仅当明确返回 -2013 (refreshToken 失效/被顶) 时标记 refreshTokenExpired=True，
        # 若响应附带兜底 accessToken (forceAccessToken) 则先保留该短期 accessToken
        if errcode == -2013:
            force_tok = (res.get("accessToken") or "") if isinstance(res, dict) else ""
            now_fail = time.time()
            sess["deviceId"] = did
            sess["deviceName"] = dname
            sess["refreshTokenExpired"] = True
            sess["lastRefreshError"] = "-2013"
            if force_tok:
                sess["accessToken"] = force_tok
                sess["refreshedAt"] = now_fail
                _atomic_write_json(session_path, sess)
                print("[AUTO-REFRESH] -2013 forceAccessToken applied; refreshToken marked expired.")
                return sess
            _atomic_write_json(session_path, sess)
            meta["last_fail_ts"] = now_fail
            meta["last_fail_token"] = current_tok
            meta["last_errcode"] = errcode
            print("[AUTO-REFRESH] Refresh rejected (-2013); marked refreshTokenExpired.")
            return None

        meta["last_fail_ts"] = time.time()
        meta["last_fail_token"] = current_tok
        meta["last_errcode"] = errcode
        print(f"[AUTO-REFRESH] Failed to refresh token: status={status_code}, errcode={errcode}")
        return None


def activate_terminal_session(
    headers: dict,
    session=None,
    include_feature: bool = False,
    in_background: int = 0,
    min_interval: int = 0,
    mp_synckey: int = 0,
    proxies: dict = None,
) -> bool:
    """
    通过微信读书移动端在线会话同步协议 (AppService.mobileSync) 确定的激活步骤:
    向微信读书服务端发起 POST /mobileSync 汇报移动终端同步态，
    将新旧账号的服务端会话通道从未激活态转变为在线活跃态，彻底解除 /mp/chapters 和 store/search 的 499 (-2041) 拦截；
    可选同步 /feature?synckey=0 刷新签名所用 guest_token。
    """
    try:
        h = sanitize_apk_headers(headers)
        vid = str(h.get("vid") or "")
        if min_interval > 0 and vid:
            with _ACTIVE_SYNC_LOCK:
                last_at = float(_LAST_ACTIVE_SYNC_AT.get(vid) or 0.0)
                if last_at > 0 and (time.time() - last_at) < min_interval:
                    return True

        h["Content-Type"] = "application/json; charset=UTF-8"

        if session is not None:
            s = session
        else:
            s = requests.Session()
            if not proxies:
                s.trust_env = False

        # 1. 核心步骤：调用 mobileSync 激活服务端通道 (字段严格对齐官方 AppService.mobileSync)
        u_mobile_sync = "https://i.weread.qq.com/mobileSync"
        payload = {
            "shelf": 0, "booklist": 0, "shelfLecture": 0, "notifications": 0,
            "config": 0, "gift": 0, "payItem": 0, "balance": 0.0, "reviewTimeline": 0,
            "follower": 0, "readingExchange": 0, "chat": 0, "chatRemovedSynckey": 0,
            "wechatFriend": 0, "inBackground": 1 if in_background else 0, "refluxSynckey": 0, "following": 0,
            "applyList": 0, "mcard": 0, "discoverFeedSynckey": 0, "rateSynckey": 0,
            "browse": 0, "medal": 0, "medalUrl": 0, "regionResource": 0,
            "discoverColumnSynckey": 0, "friendReviewSynckey": 0, "wehearSyncKey": 0,
            "preferTab": 1, "localBrowseTab": "", "localCommunityTab": ""
        }
        pace_weread_request(u_mobile_sync)
        resp = s.post(u_mobile_sync, json=payload, headers=h, proxies=proxies, timeout=8)
        print(f"[TERMINAL SYNC] mobileSync status={resp.status_code}")

        # 2. 对齐 WRApplicationContext.loginSuccess/handleOfflineAndPreload: 同步 /feature?synckey=0 与 guest_token
        if include_feature:
            u_feature = "https://i.weread.qq.com/feature?synckey=0"
            try:
                pace_weread_request(u_feature)
                feat_resp = s.get(u_feature, headers=h, proxies=proxies, timeout=5)
                if feat_resp.status_code == 200:
                    feat_data = feat_resp.json()
                    if isinstance(feat_data, dict):
                        gt = (
                            feat_data.get("guest_token")
                            or feat_data.get("guestToken")
                            or (feat_data.get("feature") or {}).get("guest_token")
                        )
                        if gt:
                            update_guest_token(str(gt))
            except Exception as feat_err:
                print(f"[TERMINAL SYNC] syncFeature skipped: {feat_err}")

        # 3. 辅助同步基础书架与公众号索引 (带节流间隔与官方客户端查询参数)
        u_shelf = "https://i.weread.qq.com/shelf/sync?synckey=0&teenmode=0&album=1&onlyBookid=1&localBookCount=0"
        pace_weread_request(u_shelf)
        s.get(u_shelf, headers=h, proxies=proxies, timeout=6)

        u_mpl = f"https://i.weread.qq.com/mp/list?listType=0&count=50&synckey={int(mp_synckey or 0)}"
        pace_weread_request(u_mpl)
        s.get(u_mpl, headers=h, proxies=proxies, timeout=6)

        if vid:
            with _ACTIVE_SYNC_LOCK:
                _LAST_ACTIVE_SYNC_AT[vid] = time.time()

        print("[TERMINAL SYNC] Successfully activated mobile online sync session via AppService.mobileSync!")
        return True
    except Exception as e:
        print(f"[TERMINAL SYNC ERROR] {e}")
        return False
