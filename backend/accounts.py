"""
公众号管理模块
搜索、收藏、管理公众号列表
"""

import time
import requests as req
from flask import Blueprint, jsonify, request

from backend.config import (
    ACCOUNTS_FILE, CONFIG_FILE, BASE_URL, DEFAULT_HEADERS,
    load_json, save_json, get_proxies_dict, report_proxy_status,
    get_default_wechat_ua
)
from backend.account_pool import borrow_session, account_pool

accounts_bp = Blueprint("accounts", __name__, url_prefix="/api/accounts")


def _get_session():
    """获取凭证（通过账号池）"""
    account_id, token, cookie_str = borrow_session()
    return token, cookie_str


def is_legacy_fakeid(fakeid: str) -> bool:
    """判断是否为无效的 fakeid 标识"""
    if not fakeid:
        return True
    fid = str(fakeid).strip()
    if fid == "${window.biz}" or "${" in fid:
        return True
    return False


def _load_accounts() -> list:
    """加载已收藏的公众号列表（自动剔除旧版不兼容的 fakeid 记录）"""
    raw_accounts = load_json(ACCOUNTS_FILE, [])
    if not isinstance(raw_accounts, list):
        return []
    valid_accounts = [a for a in raw_accounts if isinstance(a, dict) and not is_legacy_fakeid(a.get("fakeid"))]
    if len(valid_accounts) != len(raw_accounts):
        save_json(ACCOUNTS_FILE, valid_accounts)
    return valid_accounts


def _save_accounts(accounts: list):
    """保存公众号列表"""
    save_json(ACCOUNTS_FILE, accounts)


@accounts_bp.route("", methods=["GET"])
def list_accounts():
    """获取已收藏的公众号列表"""
    accounts = _load_accounts()
    return jsonify({"accounts": accounts, "total": len(accounts)})


@accounts_bp.route("/search", methods=["POST"])
def search_accounts():
    """搜索/解析公众号（通过微信公众号文章链接解析 mpId 和元数据）"""
    data = request.get_json() or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "请输入微信公众号文章链接"}), 400

    if not (keyword.startswith("http://") or keyword.startswith("https://")):
        return jsonify({
            "error": "请粘贴该公众号的任意文章链接（例如 https://mp.weixin.qq.com/s/...）以解析添加"
        }), 400

    import urllib.parse, re, html
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # 1. 尝试从输入 URL Query 中提取 __biz
        parsed = urllib.parse.urlparse(keyword)
        qs = urllib.parse.parse_qs(parsed.query)
        fakeid = None
        if qs.get("__biz") and qs.get("__biz")[0]:
            b = qs.get("__biz")[0].strip()
            if not b.startswith("${") and len(b) >= 8:
                fakeid = b

        # 2. 借用账号池 Session Cookie 请求文章页面，规避 verify 验证码重定向
        cookie_str = ""
        try:
            _, _, cookie_str = borrow_session()
        except Exception:
            pass

        headers = {
            "User-Agent": get_default_wechat_ua()
        }
        if cookie_str:
            headers["Cookie"] = cookie_str

        try:
            from curl_cffi import requests as c_req
            resp = c_req.get(keyword, headers=headers, impersonate="chrome", allow_redirects=True, timeout=15, verify=False)
        except Exception:
            resp = req.get(keyword, headers=headers, timeout=15, verify=False)

        if resp.status_code != 200:
            return jsonify({"error": f"请求文章页面失败 (HTTP {resp.status_code})"}), 400

        html_text = resp.text

        # 3. 若 URL 无 __biz，从重定向后的 URL 中提取
        if not fakeid:
            resp_qs = urllib.parse.parse_qs(urllib.parse.urlparse(resp.url).query)
            if resp_qs.get("__biz") and resp_qs.get("__biz")[0]:
                b = resp_qs.get("__biz")[0].strip()
                if not b.startswith("${") and len(b) >= 8:
                    fakeid = b

        # 4. 从 HTML 源码中正则匹配干净的 base64 __biz
        if not fakeid:
            m = (re.search(r'__biz=([A-Za-z0-9+/=]{10,})', html_text) or
                 re.search(r'var\s+biz\s*=\s*"([A-Za-z0-9+/=]{10,})"', html_text) or
                 re.search(r'biz\s*:\s*"([A-Za-z0-9+/=]{10,})"', html_text))
            if m:
                fakeid = m.group(1)

        # 5. 清理 fakeid 中可能的尾部转义符或多余参数
        if fakeid:
            fakeid = re.split(r'[\\&"\'#\s]', fakeid)[0].strip()

        if not fakeid or is_legacy_fakeid(fakeid):
            return jsonify({"error": "未能从文章页面解析出有效的公众号标识 (__biz)，请确认链接是否为真实的微信公众号文章！"}), 400

        # 6. 解析公众号昵称
        nickname = "公众号"
        m_nick = (re.search(r'var\s+nickname\s*=\s*htmlDecode\("([^"]+)"\)', html_text) or
                  re.search(r'var\s+nickname\s*=\s*"([^"]+)"', html_text) or
                  re.search(r'nickname\s*:\s*\'([^\']+)\'', html_text) or
                  re.search(r'class="profile_nickname">([^<]+)<', html_text) or
                  re.search(r'id="js_name">\s*([^\s<]+)', html_text) or
                  re.search(r'class="account_nickname_inner">([^<]+)<', html_text) or
                  re.search(r'<meta\s+property="og:article:author"\s+content="([^"]+)"', html_text))
        if m_nick and m_nick.group(1).strip():
            nickname = html.unescape(m_nick.group(1).strip())

        # 7. 解析头像
        head_img = ""
        m_img = (re.search(r'var\s+hd_head_img\s*=\s*"([^"]+)"', html_text) or
                 re.search(r'var\s+msg_cdn_url\s*=\s*"([^"]+)"', html_text) or
                 re.search(r'class="account_avatar">\s*<img\s+src="([^"]+)"', html_text) or
                 re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html_text))
        if m_img and m_img.group(1).strip():
            head_img = m_img.group(1).strip().replace("\\/", "/")

        # 8. 解析签名
        signature = ""
        m_sig = re.search(r'var\s+profile_signature\s*=\s*"([^"]+)"', html_text)
        if m_sig and m_sig.group(1).strip():
            signature = html.unescape(m_sig.group(1).strip()).replace("\\x0a", " ")

        results = [{
            "fakeid": fakeid,
            "nickname": nickname,
            "alias": fakeid,
            "round_head_img": head_img,
            "service_type": 1,
            "signature": signature,
            "update_time": int(time.time()),
        }]
        return jsonify({"results": results, "total": len(results)})
    except Exception as e:
        return jsonify({"error": f"解析文章链接发生异常: {str(e)}"}), 500


@accounts_bp.route("/weread/login-url", methods=["POST"])
def get_weread_login_url():
    return jsonify({"error": "第三方 API 已弃用，请使用微信公众平台账号登录凭证"}), 400


@accounts_bp.route("/weread/login-status/<uuid_str>", methods=["GET"])
def check_weread_login_status(uuid_str):
    return jsonify({"error": "第三方 API 已弃用，请使用微信公众平台账号登录凭证"}), 400


@accounts_bp.route("", methods=["POST"])
def add_account():
    """添加公众号到收藏（若已存在同名旧账号，自动升级其 mpId）"""
    data = request.get_json() or {}
    fakeid = data.get("fakeid", "").strip()
    nickname = data.get("nickname", "").strip()

    if not fakeid or not nickname:
        return jsonify({"error": "fakeid 和 nickname 不能为空"}), 400

    accounts = _load_accounts()

    # 1. 检查 fakeid 是否完全一致
    for acc in accounts:
        if acc.get("fakeid") == fakeid:
            return jsonify({"error": "该公众号已在收藏中"}), 400

    # 2. 检查是否有同名旧账号，自动升级为微信读书 mpId
    for acc in accounts:
        if acc.get("nickname") == nickname:
            acc["fakeid"] = fakeid
            if data.get("round_head_img"):
                acc["round_head_img"] = data.get("round_head_img")
            if data.get("signature"):
                acc["signature"] = data.get("signature")
            acc["updated_time"] = time.time()
            _save_accounts(accounts)
            return jsonify({"message": "公众号已升级至微信读书模式", "account": acc})

    new_account = {
        "fakeid": fakeid,
        "nickname": nickname,
        "alias": data.get("alias", ""),
        "round_head_img": data.get("round_head_img", ""),
        "signature": data.get("signature", ""),
        "service_type": data.get("service_type", 0),
        "added_time": time.time(),
    }

    accounts.append(new_account)
    _save_accounts(accounts)

    return jsonify({"message": "添加成功", "account": new_account})


@accounts_bp.route("/<fakeid>", methods=["DELETE"])
def remove_account(fakeid):
    """从收藏中删除公众号"""
    accounts = _load_accounts()
    new_accounts = [a for a in accounts if a.get("fakeid") != fakeid]

    if len(new_accounts) == len(accounts):
        return jsonify({"error": "未找到该公众号"}), 404

    _save_accounts(new_accounts)
    return jsonify({"message": "删除成功"})


@accounts_bp.route("/<fakeid>", methods=["PUT"])
def update_account(fakeid):
    """更新公众号信息"""
    data = request.get_json() or {}
    accounts = _load_accounts()

    for acc in accounts:
        if acc.get("fakeid") == fakeid:
            for key in ["nickname", "alias", "signature", "round_head_img"]:
                if key in data:
                    acc[key] = data[key]
            _save_accounts(accounts)
            return jsonify({"message": "更新成功", "account": acc})

    return jsonify({"error": "未找到该公众号"}), 404


@accounts_bp.route("/<fakeid>/rss-subscribe", methods=["POST"])
def rss_subscribe(fakeid):
    """开启 RSS 自动抓取订阅"""
    from backend.rss_scheduler import rss_scheduler

    data = request.get_json() or {}
    interval = data.get("interval_minutes", 60)

    # 从已收藏列表中查找公众号信息
    accounts = _load_accounts()
    account = None
    for acc in accounts:
        if acc.get("fakeid") == fakeid:
            account = acc
            break

    if not account:
        return jsonify({"error": "请先收藏该公众号"}), 404

    nickname = account.get("nickname", fakeid)
    sub = rss_scheduler.subscribe(fakeid, nickname, interval)

    immediate_fetch = rss_scheduler.is_in_fetch_window()
    if immediate_fetch:
        # 提交到线程池立即抓取，让 RSS 马上有内容
        rss_scheduler.submit_fetch(sub)

    message = f"已开启 RSS 订阅: {nickname}"
    if not immediate_fetch:
        message += "，当前不在采集时间段内，将在时间段内自动抓取"
    return jsonify({"message": message, "subscription": sub, "immediate_fetch": immediate_fetch})


@accounts_bp.route("/<fakeid>/rss-subscribe", methods=["DELETE"])
def rss_unsubscribe(fakeid):
    """关闭 RSS 自动抓取订阅"""
    from backend.rss_scheduler import rss_scheduler

    removed = rss_scheduler.unsubscribe(fakeid)
    if not removed:
        return jsonify({"error": "该公众号未订阅 RSS"}), 404

    return jsonify({"message": "已取消 RSS 订阅"})


@accounts_bp.route("/rss-subscriptions", methods=["GET"])
def rss_subscriptions():
    """获取所有 RSS 订阅状态（待上传/已隔离数实时从下载历史派生，保证单一事实来源）"""
    from backend.rss_scheduler import rss_scheduler
    from backend.config import load_json, DOWNLOAD_HISTORY_FILE

    subs = rss_scheduler.get_subscriptions()
    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    for sub in subs:
        nickname = sub.get("nickname", "")
        sub["pending_upload_count"] = rss_scheduler.count_pending(history, nickname)
        sub["quarantined_count"] = rss_scheduler.count_quarantined(history, nickname)
    return jsonify({"subscriptions": subs})


@accounts_bp.route("/rss-upload-log", methods=["GET"])
def rss_upload_log():
    """获取 RSS 上传审计日志（最近若干次上传记录）"""
    from backend.rss_scheduler import rss_scheduler

    limit = request.args.get("limit", default=30, type=int)
    account = request.args.get("account") or None
    log = rss_scheduler.get_upload_log(limit=limit, account=account)
    return jsonify({"log": log})


@accounts_bp.route("/<fakeid>/rss-force-upload", methods=["POST"])
def rss_force_upload(fakeid):
    """强制上传该公众号所有待上传 + 历史未上传文章"""
    from backend.rss_scheduler import rss_scheduler
    from backend.accounts import accounts_bp as _a
    import threading

    sub = rss_scheduler.get_subscription(fakeid)
    if not sub or not sub.get("enabled"):
        return jsonify({"error": "该公众号未开启 RSS 订阅"}), 404

    nickname = sub.get("nickname", "")
    result = rss_scheduler.force_upload_all(nickname)

    # 同步更新订阅的上传状态
    with rss_scheduler._lock:
        subs = rss_scheduler.get_subscriptions()
        for s in subs:
            if s.get("fakeid") == fakeid:
                if result.get("attempted"):
                    s["last_upload_time"] = time.time()
                    s["last_upload_count"] = result.get("count", 0)
                s["last_upload_error"] = result.get("error")
                s["pending_upload_count"] = result.get("pending_count", 0)
                s["quarantined_count"] = result.get("quarantined", 0)
                s["last_upload_attempted"] = result.get("attempted", False)
                s["last_upload_disabled"] = result.get("disabled", False)
                break
        rss_scheduler._save_subscriptions(subs)

    return jsonify(result)


@accounts_bp.route("/sync-pc-wechat", methods=["POST"])
def sync_pc_wechat():
    """按需触发 PC 微信针对特定公众号的自动化同步（如搜索并点击该公众号文章）。"""
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip() or "新京报"
    try:
        from scripts.auto_refresh_pc_wechat import trigger_pc_wechat_refresh
        # force=True：用户手动点击是明确意图，绕过冷却/合并（仍受全局 UI 互斥保护）
        success = trigger_pc_wechat_refresh(keyword=keyword, force=True)
        return jsonify({"success": success, "keyword": keyword})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/sync-batch-pc-wechat", methods=["POST"])
def sync_batch_pc_wechat():
    """全自动触发 UI 自动化一键为全部公众号建立主页授权会话"""
    try:
        from scripts.auto_refresh_pc_wechat import run_batch_portal_flow_macos
        success = run_batch_portal_flow_macos()
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
