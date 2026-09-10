"""凭证脱敏工具：日志/控制台输出统一走脱敏版，完整凭证仅落盘于账号池。

参考 Access_wechat_article 的双份输出设计：本地保留完整数据可用，
对外（日志/摘要）剥离 key / pass_ticket / appmsg_token / uin 等短时效敏感参数。
"""
from __future__ import annotations

import re

# URL query 中需要脱敏的短时效凭证参数（与 Access_wechat_article 的脱敏清单对齐并扩展）
SENSITIVE_QUERY_KEYS = {
    "key", "pass_ticket", "appmsg_token", "token", "wxtoken", "poc_token",
    "exportkey", "sessionid", "ticket", "uin", "wxuin", "data_bizuin",
}

# Cookie 中需要脱敏值的字段名
SENSITIVE_COOKIE_NAMES = {
    "appmsg_token", "pass_ticket", "key", "wxtoken", "poc_token",
    "sessionid", "uin", "wxuin", "data_bizuin", "slave_sid", "slave_user",
}


def mask_secret(value, keep: int = 4, max_len: int = 96) -> str:
    """敏感值脱敏：保留前 keep 个字符 + 长度提示，例如 'eu9TkX...(23)'。
    空值返回 ''；过短的值全遮蔽。"""
    s = str(value or "")
    if not s:
        return ""
    if len(s) <= keep * 2:
        return f"***({len(s)})"
    masked = s[:keep] + "...(" + str(len(s)) + ")"
    return masked[:max_len]


def redact_url(url: str) -> str:
    """把 URL 中敏感 query 参数的值替换为 ***（参数名保留，便于排查）。"""
    if not url:
        return ""
    try:
        return re.sub(
            r'([?&])((' + "|".join(re.escape(k) for k in SENSITIVE_QUERY_KEYS) + r')=)[^&]*',
            lambda m: m.group(1) + m.group(3) + "=***",
            url,
            flags=re.IGNORECASE,
        )
    except Exception:
        return "<url>"


def redact_cookie(cookie_str: str) -> str:
    """Cookie 脱敏：敏感字段的值只保留前 4 个字符，其余字段原样保留（名称不脱敏）。"""
    if not cookie_str:
        return ""
    try:
        parts = re.split(r';\s*', cookie_str)
        out = []
        for p in parts:
            if "=" not in p:
                out.append(p)
                continue
            name, _, val = p.partition("=")
            if name.strip().lower() in SENSITIVE_COOKIE_NAMES:
                out.append(f"{name}={mask_secret(val)}")
            else:
                out.append(p)
        return "; ".join(out[:8]) + (f" ...共{len(parts)}项" if len(parts) > 8 else "")
    except Exception:
        return "<cookie>"
