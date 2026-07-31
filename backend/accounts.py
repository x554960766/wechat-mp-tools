"""
公众号管理模块
搜索、收藏、管理公众号列表
"""

import time
import requests as req
from flask import Blueprint, jsonify, request

from backend.config import (
    ACCOUNTS_FILE, CONFIG_FILE, BASE_URL, DEFAULT_HEADERS,
    load_json, save_json, get_proxies_dict, report_proxy_status
)
from backend.account_pool import borrow_session, account_pool

accounts_bp = Blueprint("accounts", __name__, url_prefix="/api/accounts")


def _get_session():
    """获取凭证（通过账号池）"""
    account_id, token, cookie_str = borrow_session()
    return token, cookie_str


def is_legacy_fakeid(fakeid: str) -> bool:
    """判断是否为旧版微信创作者后台不兼容的 fakeid 标识"""
    if not fakeid:
        return True
    fid = str(fakeid).strip()
    if fid.endswith("="):
        return True
    if not fid.startswith("MP_WXS_") and len(fid) >= 16 and not fid.isdigit():
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
            "error": "微信读书模式下，请粘贴该公众号的任意文章链接（例如 https://mp.weixin.qq.com/s/...）以解析添加"
        }), 400

    try:
        acc_id, token, cookie_str = borrow_session()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    proxy_url = None
    try:
        from backend.config import get_settings, WEREAD_PLATFORM_URL
        platform_url = get_settings().get("weread_platform_url") or WEREAD_PLATFORM_URL
        headers = {
            "xid": str(acc_id),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        proxies = get_proxies_dict()
        if proxies:
            proxy_url = proxies.get("http")

        # 使用 curl_cffi 发送请求，解决 TLS 握手/SSLError 问题
        try:
            from curl_cffi import requests as c_req
            resp = c_req.post(
                f"{platform_url}/api/v2/platform/wxs2mp",
                json={"url": keyword},
                headers=headers,
                proxies=proxies,
                timeout=25,
                impersonate="chrome",
            )
        except Exception:
            resp = req.post(
                f"{platform_url}/api/v2/platform/wxs2mp",
                json={"url": keyword},
                headers=headers,
                proxies=proxies,
                timeout=25,
            )

        if resp.status_code != 200:
            err_text = resp.text
            report_proxy_status(proxy_url, success=False)
            account_pool.report(acc_id, http_ok=False, error=err_text)
            if "No book found" in err_text:
                return jsonify({"error": "解析失败：该文章链接未能匹配到对应的公众号，请确认链接是否有效"}), 400
            return jsonify({"error": f"解析失败 (HTTP {resp.status_code}): {err_text}"}), 500

        report_proxy_status(proxy_url, success=True)
        items = resp.json()
        account_pool.report(acc_id, ret=0)

        results = []
        if isinstance(items, list):
            for item in items:
                results.append({
                    "fakeid": item.get("id", ""),
                    "nickname": item.get("name", ""),
                    "alias": item.get("id", ""),
                    "round_head_img": item.get("cover", ""),
                    "service_type": 1,
                    "signature": item.get("intro", ""),
                    "update_time": item.get("updateTime", 0),
                })
        elif isinstance(items, dict) and items.get("id"):
            results.append({
                "fakeid": items.get("id", ""),
                "nickname": items.get("name", ""),
                "alias": items.get("id", ""),
                "round_head_img": items.get("cover", ""),
                "service_type": 1,
                "signature": items.get("intro", ""),
                "update_time": items.get("updateTime", 0),
            })

        return jsonify({"results": results, "total": len(results)})

    except Exception as e:
        report_proxy_status(proxy_url, success=False)
        account_pool.report(acc_id, http_ok=False, error=str(e))
        return jsonify({"error": f"网络请求失败: {str(e)}"}), 500


@accounts_bp.route("/weread/login-url", methods=["POST"])
def get_weread_login_url():
    """获取微信读书扫码登录 URL 及 UUID"""
    from backend.config import get_settings
    platform_url = get_settings().get("weread_platform_url") or WEREAD_PLATFORM_URL
    try:
        resp = req.get(f"{platform_url}/api/v2/login/platform", timeout=15)
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({"error": f"获取登录二维码失败 (HTTP {resp.status_code})"}), 500
    except Exception as e:
        return jsonify({"error": f"请求中转服务异常: {str(e)}"}), 500


@accounts_bp.route("/weread/login-status/<uuid_str>", methods=["GET"])
def check_weread_login_status(uuid_str):
    """轮询微信读书扫码登录结果"""
    from backend.config import get_settings
    platform_url = get_settings().get("weread_platform_url") or WEREAD_PLATFORM_URL
    try:
        resp = req.get(f"{platform_url}/api/v2/login/platform/{uuid_str}", timeout=30)
        data = resp.json()
        vid = data.get("vid")
        token = data.get("token")
        if vid and token:
            username = data.get("username") or f"WeRead_{vid}"
            acc = account_pool.add_or_update({
                "token": token,
                "vid": str(vid),
                "nickname": username,
                "save_time": time.time(),
            })
            return jsonify({
                "status": "success",
                "message": "登录成功",
                "account": acc,
                "vid": vid,
                "username": username,
            })
        return jsonify({"status": "waiting", "message": data.get("message", "等待扫码")})
    except Exception as e:
        return jsonify({"error": f"查询登录状态异常: {str(e)}"}), 500


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
