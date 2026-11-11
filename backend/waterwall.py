"""
腾讯防水墙 (TCaptcha) 拦截识别、安全凭证同步与失败取证模块
基于微信读书官方客户端风控交互机制实现
"""

import time
import re
import urllib.parse
from flask import Blueprint, jsonify, request

waterwall_bp = Blueprint("waterwall", __name__, url_prefix="/api")

# ══════════════════════════════════════════════════════════════════════════
#  腾讯防水墙（TCaptcha）拦截识别特征词表
# ══════════════════════════════════════════════════════════════════════════
WATERWALL_MARKERS = (
    "tcaptcha",
    "captcha.qq.com",
    "randstr",
    "防水墙",
    "安全验证",
    "请完成验证",
    "risk_control",
    "verify_code",
    "-2041",
)

WATERWALL = {
    "active": False,
    "since": "",
    "hits": 0,
    "last_url": "",
    "last_status": 0,
    "last_marker": "",
    "blocked_until": 0,
}

# 缓存人工完成验证后返回的微信读书安全票据 (wr_ticket & wr_randstr)
CAPTCHA_TICKET = {
    "ticket": "",
    "randstr": "",
    "updated_at": 0,
}

# 命中后冷却 300 秒（5分钟）：期间避免盲目重复重发导致风控加剧
WATERWALL_COOLDOWN = 300

# ══════════════════════════════════════════════════════════════════════════
#  上游失败取证缓存（供 /api/diagnose 诊断分析）
# ══════════════════════════════════════════════════════════════════════════
UPSTREAM_FAILURES = []
MAX_FAILURES = 20

SENSITIVE_KEYS = (
    "accesstoken", "refreshtoken", "wxaccesstoken", "skey",
    "openid", "vid", "token", "cookie", "authorization"
)

VERDICT_HINTS = {
    "captcha": "腾讯防水墙：需要人工验证。工具已停止自动重试，请完成网页滑块验证或在手机 App 开启阅读后重试。",
    "session_inactive": "终端会话休眠（499 / -2041），非验证码。工具会自动尝试 mobileSync 激活在线态重试；若反复出现，需在微信读书 App 中打开任意公众号主页激活。",
    "auth_expired": "登录态过期（401）。账号池已自动触发静默换票续期。",
    "rate_limited": "被微信读书网关限流（-2014 / 429）。工具已自动退避降频重试，请勿瞬间连续高频请求。",
    "forbidden": "403 禁止访问，可能为账号权限受限或风控静默拒绝。",
    "server_error": "微信读书官方服务器错误（5xx），稍后重试即可。",
    "other": "未归类的异常，请查看响应详细特征与 body_head。",
}


def is_waterwall_active() -> bool:
    """检查当前是否处于防水墙拦截生效期"""
    if not WATERWALL["active"]:
        return False
    if time.time() > WATERWALL["blocked_until"]:
        waterwall_reset()
        return False
    return True


def waterwall_snapshot() -> dict:
    """获取当前拦截状态脱敏快照"""
    return {
        "active": bool(WATERWALL["active"]),
        "since": WATERWALL["since"],
        "hits": WATERWALL["hits"],
        "last_url": WATERWALL["last_url"],
        "last_status": WATERWALL["last_status"],
        "last_marker": WATERWALL["last_marker"],
        "cooldown_left": max(0, int(WATERWALL["blocked_until"] - time.time())),
        "has_ticket": bool(CAPTCHA_TICKET["ticket"] and CAPTCHA_TICKET["randstr"]),
    }


def detect_waterwall(status: int, body: str) -> str:
    """
    检测响应是否命中腾讯防水墙特征
    只在 HTTP 4xx/5xx 或响应正文较短 (< 4KB) 像验证拦截页时采信
    """
    if not body:
        return ""
    low = body.lower()
    for marker in WATERWALL_MARKERS:
        if marker.lower() in low:
            if status >= 400 or len(body) < 4096:
                return marker
    return ""


def waterwall_hit(url: str, status: int, marker: str):
    """记录一次防水墙拦截事件"""
    first = not WATERWALL["active"]
    WATERWALL["active"] = True
    WATERWALL["hits"] += 1
    if first:
        WATERWALL["since"] = time.strftime("%Y-%m-%d %H:%M:%S")
    WATERWALL["last_url"] = _mask_url(url)
    WATERWALL["last_status"] = status
    WATERWALL["last_marker"] = marker
    WATERWALL["blocked_until"] = time.time() + WATERWALL_COOLDOWN
    if first:
        print(f"[WATERWALL] 命中腾讯防水墙（特征={marker}，HTTP {status}）：{WATERWALL['last_url']}", flush=True)
        print("[WATERWALL] 微信读书官方风控拦截触发！工具已暂停频繁重试，前端将自动拉起滑块验证。", flush=True)


def waterwall_reset():
    """重置拦截状态"""
    WATERWALL.update({
        "active": False,
        "since": "",
        "hits": 0,
        "last_url": "",
        "last_status": 0,
        "last_marker": "",
        "blocked_until": 0,
    })


def get_captcha_headers() -> dict:
    """获取已注入的验证码请求头 (wr_ticket & wr_randstr)"""
    h = {}
    if CAPTCHA_TICKET.get("ticket") and CAPTCHA_TICKET.get("randstr"):
        h["wr_ticket"] = str(CAPTCHA_TICKET["ticket"])
        h["wr_randstr"] = str(CAPTCHA_TICKET["randstr"])
    return h


def set_captcha_ticket(ticket: str, randstr: str):
    """设置人工滑动验证通过后签发的票据"""
    CAPTCHA_TICKET["ticket"] = ticket
    CAPTCHA_TICKET["randstr"] = randstr
    CAPTCHA_TICKET["updated_at"] = time.time()
    waterwall_reset()


# ══════════════════════════════════════════════════════════════════════════
#  脱敏与失败归类
# ══════════════════════════════════════════════════════════════════════════

def _mask_url(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url)
        if not p.query:
            return url
        qs = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        masked = []
        for k, v in qs:
            if k.lower() in SENSITIVE_KEYS:
                masked.append((k, "***"))
            else:
                masked.append((k, v))
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(masked), p.fragment))
    except Exception:
        return url


def _mask_body(body: str) -> str:
    if not body:
        return ""
    txt = body[:800]
    for k in SENSITIVE_KEYS:
        txt = re.sub(rf'("{k}"\s*:\s*)"[^"]*"', r'\1"***"', txt, flags=re.I)
        txt = re.sub(rf'({k}=)[^&;\s]+', r'\1***', txt, flags=re.I)
    txt = re.sub(r'\b[a-f0-9]{32,64}\b', '***', txt, flags=re.I)
    return txt


def classify_failure(status: int, body: str, marker: str = "") -> str:
    if marker:
        return "captcha"
    b_str = body or ""
    if status == 401:
        return "auth_expired"
    if "-2014" in b_str or status == 429:
        return "rate_limited"
    if status == 499 or "-2041" in b_str:
        return "session_inactive"
    if status == 403:
        return "forbidden"
    if 500 <= status < 600:
        return "server_error"
    return "other"


def record_failure(url: str, status: int, headers: dict, body: str, marker: str):
    """记录一次上游失败请求供取证分析"""
    ct = ""
    if hasattr(headers, "get"):
        ct = headers.get("Content-Type", "") or ""
    elif isinstance(headers, dict):
        ct = headers.get("Content-Type", "") or ""

    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "url": _mask_url(url),
        "content_type": ct,
        "bytes": len(body.encode("utf-8")) if body else 0,
        "marker": marker,
        "verdict": classify_failure(status, body, marker),
        "body_head": _mask_body(body),
    }
    UPSTREAM_FAILURES.append(entry)
    if len(UPSTREAM_FAILURES) > MAX_FAILURES:
        UPSTREAM_FAILURES.pop(0)


# ══════════════════════════════════════════════════════════════════════════
#  Blueprint 路由接口
# ══════════════════════════════════════════════════════════════════════════

@waterwall_bp.route("/waterwall", methods=["GET"])
def get_waterwall_status():
    """获取当前腾讯防水墙拦截状态"""
    snap = waterwall_snapshot()
    return jsonify({
        "need_verify": snap["active"],
        "active": snap["active"],
        "waterwall": snap,
        "app_id": "2044038556", # 微信读书官方 TCaptcha 客户端 AppId
    })


@waterwall_bp.route("/waterwall/verify", methods=["POST"])
def verify_waterwall():
    """接收前端完成腾讯滑块验证后返回的 ticket 与 randstr"""
    data = request.get_json() or {}
    ticket = str(data.get("ticket", "")).strip()
    randstr = str(data.get("randstr", "")).strip()

    if not ticket or not randstr:
        return jsonify({"ok": False, "error": "验证码凭证 ticket 或 randstr 不能为空"}), 400

    set_captcha_ticket(ticket, randstr)
    print(f"[WATERWALL] 前端人机滑动验证成功！写入票据: ticket={ticket[:10]}***, randstr={randstr}，拦截已彻底解除！", flush=True)
    return jsonify({
        "ok": True,
        "message": "安全验证成功，拦截已解除",
        "ticket_prefix": ticket[:10],
    })


@waterwall_bp.route("/waterwall/clear", methods=["POST"])
def clear_waterwall():
    """用户手动解除防水墙拦截态（例如已在微信读书 App 中完成滑动验证）"""
    was_active = WATERWALL["active"]
    waterwall_reset()
    print("[WATERWALL] 拦截态已由用户手动解除", flush=True)
    return jsonify({
        "ok": True,
        "was_active": was_active,
        "message": "已手动重置拦截状态",
    })


@waterwall_bp.route("/diagnose", methods=["GET"])
def diagnose_upstream():
    """获取最近 20 条上游微信读书请求失败取证、归类与智能诊断建议"""
    rows = list(reversed(UPSTREAM_FAILURES))
    counts = {}
    for r in rows:
        v = r["verdict"]
        counts[v] = counts.get(v, 0) + 1

    hints = [
        {"verdict": v, "count": n, "hint": VERDICT_HINTS.get(v, "")}
        for v, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]

    suggestion = "系统运行稳定，未检测到频繁上游拦截。"
    if is_waterwall_active():
        suggestion = "【确认命中腾讯防水墙】系统已暂停重试，请直接在前端弹出的滑块窗口中完成拼图，或在微信读书 App 中打开任意文章验证。"
    elif counts.get("captcha", 0) > 0:
        suggestion = "检测到近期命中过腾讯安全验证，请完成滑块验证以恢复顺畅拉取。"
    elif counts.get("session_inactive", 0) > 0:
        suggestion = "检测到终端会话休眠 (-2041)，系统已自动执行 mobileSync 尝试激活并轮询健康账号。"

    return jsonify({
        "summary": counts,
        "hints": hints,
        "suggestion": suggestion,
        "failures": rows,
        "waterwall": waterwall_snapshot(),
    })
