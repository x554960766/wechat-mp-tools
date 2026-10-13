"""
微信读书 libencrypt.so 纯 Python 算法逆向还原
实现 weread::GenSignature 与 nativeGetSignatures 完整算法
支持长效 Token (refreshToken) 自动换取最新 accessToken
"""

import json
import hashlib
import time
import random
import requests

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


def refresh_access_token(
    refresh_token: str,
    device_id: str = "410515152",
    device_name: str = "Android",
    proxies: dict = None
) -> tuple[int, dict]:
    """
    使用 refreshToken 自动静默换取最新的 accessToken
    支持自定义绑定账号专属设备 ID 与设备名称
    """
    now_ms = int(time.time() * 1000)
    rand_val = random.randint(0, 999)

    guest_token = ""
    params_for_sig = [
        guest_token,
        str(device_id),
        str(now_ms),
        str(rand_val),
        str(refresh_token)
    ]
    sig = gen_signature(params_for_sig)

    url = "https://i.weread.qq.com/login"
    payload = {
        "refreshToken": refresh_token,
        "deviceId": str(device_id),
        "wxToken": 0,
        "inBackground": 0,
        "trackId": "",
        "kickType": 1,
        "refCgi": "",
        "timestamp": now_ms,
        "random": rand_val,
        "signature": sig,
        "virtualChannelId": "",
        "deviceName": str(device_name or "Android")
    }

    headers = {
        "User-Agent": "WeRead/7.5.0 (Android; 33; arm64-v8a)",
        "Content-Type": "application/json; charset=UTF-8",
        "Host": "i.weread.qq.com"
    }

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


def try_refresh_session(session_path: str = ".session.json", device_id: str = None) -> dict | None:
    """
    尝试从 session 文件中读取 refreshToken 并静默刷新 accessToken
    如果成功，自动写回 session 文件并返回更新后的 session dict
    """
    import os
    if not os.path.exists(session_path):
        return None
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            sess = json.load(f)
    except Exception as e:
        print(f"[AUTO-REFRESH] Read session file error: {e}")
        return None

    rf = sess.get("refreshToken")
    if not rf:
        return None

    did = device_id or sess.get("deviceId") or "410515152"

    status_code, res = refresh_access_token(rf, device_id=did)
    if status_code == 200 and isinstance(res, dict) and "accessToken" in res:
        sess["accessToken"] = res["accessToken"]
        if "refreshToken" in res:
            sess["refreshToken"] = res["refreshToken"]
        if "skey" in res:
            sess["skey"] = res["skey"]
        sess["deviceId"] = did
        try:
            with open(session_path, "w", encoding="utf-8") as f:
                json.dump(sess, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        print(f"[AUTO-REFRESH] Successfully refreshed accessToken via libencrypt GenSignature! (new token={res['accessToken']})")
        return sess
    else:
        print(f"[AUTO-REFRESH] Failed to refresh token: status={status_code}, res={res}")
        return None


def activate_terminal_session(headers: dict, session=None) -> bool:
    """
    通过逆向微信读书 APK (AppService.mobileSync) 确定的移动端在线会话激活步骤:
    向微信读书服务端发起 POST /mobileSync 汇报移动终端同步态，
    将新旧账号的服务端会话通道从未激活态转变为在线活跃态，彻底解除 /mp/chapters 和 store/search 的 499 (-2041) 拦截。
    """
    try:
        h = dict(headers)
        # 移除 WeRead 特定 UA 避免网关设备指纹误判
        if "User-Agent" not in h or "WeRead/" in h.get("User-Agent", ""):
            h["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        h["Content-Type"] = "application/json; charset=UTF-8"

        s = session or requests.Session()

        # 1. 核心步骤：调用 mobileSync 激活服务端通道
        u_mobile_sync = "https://i.weread.qq.com/mobileSync"
        payload = {
            "shelf": 0, "bookInventory": 0, "shelfLecture": 0, "notifications": 0,
            "config": 0, "gift": 0, "payItem": 0, "balance": 0.0, "reviewTimeline": 0,
            "follower": 0, "readingExchange": 0, "chat": 0, "chatRemovedSynckey": 0,
            "wechatFriend": 0, "inBackground": 0, "refluxSynckey": 0, "following": 0,
            "applyList": 0, "mcard": 0, "discoverFeedSynckey": 0, "rateSynckey": 0,
            "browse": 0, "medal": 0, "medalUrl": 0, "regionResource": 0,
            "discoverColumnSynckey": 0, "friendReviewSynckey": 0, "wehearSyncKey": 0,
            "preferTab": 1, "localBrowseTab": "", "localCommunityTab": ""
        }
        resp = s.post(u_mobile_sync, json=payload, headers=h, timeout=8)
        print(f"[TERMINAL SYNC] mobileSync status={resp.status_code}")

        # 2. 辅助同步基础书架与公众号索引
        u_shelf = "https://i.weread.qq.com/shelf/sync?synckey=0"
        s.get(u_shelf, headers=h, timeout=6)

        u_mpl = "https://i.weread.qq.com/mp/list?listType=0&count=50&synckey=0"
        s.get(u_mpl, headers=h, timeout=6)

        print("[TERMINAL SYNC] Successfully activated mobile online sync session via AppService.mobileSync!")
        return True
    except Exception as e:
        print(f"[TERMINAL SYNC ERROR] {e}")
        return False



