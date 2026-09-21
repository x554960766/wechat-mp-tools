"""
抖音 msToken 生成器与缓存管理
参考并同步自 douyin-downloader (2026-09 最新实践):
- 内置 BUNDLED_MS_TOKEN_CONF (源自 F2 / mssdk 签名配置)
- 避免国内网络环境访问 GitHub 失败导致降级假 Token
- 直连 https://mssdk.bytedance.com/web/r/token 请求真实有效的 msToken
- 支持 60s 内存缓存与线程锁，失败后使用规范 184 位 Token 并在 TTL 内保持稳定，避免频繁换 Token 触发风控
"""

import json
import time
import random
import string
import ssl
import urllib.request
from threading import Lock
from typing import Optional, Dict, Any

# 内置快照配置 (来自 F2 / mssdk)
BUNDLED_MS_TOKEN_CONF: Dict[str, Any] = {
    "url": (
        "https://mssdk.bytedance.com/web/r/token?ms_appid=6383&msToken=T4bNG9W2rKF7hBNwaYssDE"
        "rnJEobDAk641DFaOn4hcsfAM8slpbZeKPM4Ml4rhDQq18iY8nQ0JR3J87SLZtDiDqtZdZawfBjCWAgtolQso"
        "EtG6MLETvo4fwr7F28zGJUFDdJgKEZHibNR0QshVBv28ygsQsJDzerKAtsgj9Pn5WsxyS1vfkiX3I%3D"
    ),
    "magic": 538969122,
    "version": 1,
    "dataType": 8,
    "ulr": 0,
    "strData": (
        "fW15xyeivmE5JAQZdb83gdUCCHlGDBZDeWxqYklwOYciPisi772aWHSG75OFvFZ5zS5RlfrFGzxNzRQllBoI"
        "w2wXT5VvEO9UzRqLMD2kh96/p8aCc56JCdvtz6oZx/j9vRUiy5Hdy4OGKqH7e0VqjP2biY6Zi27XiuWv6ZJ/"
        "owedPUULhR2LmyhLRAm6wZA3zRj6z6XiZQU64oWdAorw2Q03RCFp7AF9WPmXdgRDCQl/33NPthRL/TBLdJkE"
        "xG3c0r1Zg9Yk04Wd5wD71/kY36y6hN61j15v+9qU1Fk65oUdA4rw2Q03RCFp7AF9WPmXdgRDCQl/33NPthRL"
        "/TBLdJkExG3c0r1Zg9Yk04Wd5wD71/kY36y6hN61j15v+9qU1Fk65oUdA4rw2Q03RCFp7AF9WPmXdgRDCQl/"
        "33NPthRL/TBLdJkExG3c0r1Zg9Yk04Wd5wD71/kY36y6hN61j15v+9qU1Fk65oUdA4rw2Q03RCFp7AF9WPmX"
        "dgRDCQl/33NPthRL/TBLdJkExG3c0r1Zg9Yk04Wd5wD71/kY36y6hN61j15v+9qU1Fk65oUdA4rw2Q03RCFp"
        "7AF9WPmXdgRDCQl/33NPthRL/TBLdJkExG3c0r1Zg9Yk04Wd5wD71/kY36y6hN61j15v+9qU1Fk6"
    ),
}

_cached_token: Optional[str] = None
_cached_until: float = 0.0
_lock = Lock()
_last_failure_until: float = 0.0

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)


def _gen_fallback_ms_token() -> str:
    """生成合乎规范的 184 位 token (182 随机字符 + ==)"""
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(182)) + "=="


def _extract_ms_token_from_headers(headers: Any) -> Optional[str]:
    """从 HTTP 响应头提取 Set-Cookie 中的 msToken"""
    set_cookies = headers.get_all("Set-Cookie") if hasattr(headers, "get_all") else []
    if not set_cookies and hasattr(headers, "get"):
        sc = headers.get("Set-Cookie")
        if sc:
            set_cookies = [sc]

    if not set_cookies:
        return None

    for item in set_cookies:
        for part in item.split(";"):
            part = part.strip()
            if part.startswith("msToken="):
                val = part[len("msToken="):].strip()
                if val:
                    return val
    return None


def get_real_ms_token(user_agent: str = DEFAULT_USER_AGENT, timeout: float = 3.0) -> str:
    """获取有效 msToken，带 60s 内存缓存与失败降级保护"""
    global _cached_token, _cached_until, _last_failure_until

    now = time.monotonic()
    with _lock:
        if _cached_token and now < _cached_until:
            return _cached_token

        # 处于失败静默期内直接走 fallback，并在 TTL 期间复用
        if now < _last_failure_until:
            fallback = _gen_fallback_ms_token()
            _cached_token = fallback
            _cached_until = now + 60.0
            return fallback

    payload = {
        "magic": BUNDLED_MS_TOKEN_CONF["magic"],
        "version": BUNDLED_MS_TOKEN_CONF["version"],
        "dataType": BUNDLED_MS_TOKEN_CONF["dataType"],
        "strData": BUNDLED_MS_TOKEN_CONF["strData"],
        "ulr": BUNDLED_MS_TOKEN_CONF["ulr"],
        "tspFromClient": int(time.time() * 1000),
    }

    token = None
    try:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            BUNDLED_MS_TOKEN_CONF["url"],
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": user_agent,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            token = _extract_ms_token_from_headers(resp.headers)
    except Exception:
        pass

    now = time.monotonic()
    with _lock:
        if token and len(token) in (164, 184):
            _cached_token = token
            _cached_until = now + 60.0
            _last_failure_until = 0.0
            return token

        # 请求不成功时进入 120s 冷却，且本次生成的规范 fallback Token 也缓存 60s 保持会话稳定
        _last_failure_until = now + 120.0
        fallback = _gen_fallback_ms_token()
        _cached_token = fallback
        _cached_until = now + 60.0
        return fallback
