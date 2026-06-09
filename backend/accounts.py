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

accounts_bp = Blueprint("accounts", __name__, url_prefix="/api/accounts")


def _get_session():
    """创建带凭证的 requests 会话"""
    config = load_json(CONFIG_FILE)
    if not config or not config.get("token"):
        raise RuntimeError("未登录")
    token = config["token"]
    cookie_str = config["cookie_str"]
    return token, cookie_str


def _load_accounts() -> list:
    """加载已收藏的公众号列表"""
    return load_json(ACCOUNTS_FILE, [])


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
    """搜索公众号"""
    data = request.get_json() or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "请输入搜索关键字"}), 400

    try:
        token, cookie_str = _get_session()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    proxy_url = None
    try:
        headers = {**DEFAULT_HEADERS, "Cookie": cookie_str}
        proxies = get_proxies_dict()
        if proxies:
            proxy_url = proxies.get("http")

        resp = req.get(
            f"{BASE_URL}/cgi-bin/searchbiz",
            params={
                "action": "search_biz",
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": "1",
                "query": keyword,
                "begin": "0",
                "count": "10",
            },
            headers=headers,
            proxies=proxies,
            timeout=25,
        )

        if resp.status_code != 200:
            report_proxy_status(proxy_url, success=False)
            return jsonify({"error": f"HTTP {resp.status_code}"}), 500

        report_proxy_status(proxy_url, success=True)
        resp_data = resp.json()
        ret = resp_data.get("base_resp", {}).get("ret", -1)

        if ret == 200003:
            return jsonify({"error": "登录已过期，请重新扫码登录"}), 401
        if ret != 0:
            err_msg = resp_data.get("base_resp", {}).get("err_msg", "未知错误")
            return jsonify({"error": f"搜索失败 (ret={ret}): {err_msg}"}), 500

        results = []
        for item in resp_data.get("list", []):
            results.append({
                "fakeid": item.get("fakeid", ""),
                "nickname": item.get("nickname", ""),
                "alias": item.get("alias", ""),
                "round_head_img": item.get("round_head_img", ""),
                "service_type": item.get("service_type", 0),
                "signature": item.get("signature", ""),
            })

        return jsonify({"results": results, "total": len(results)})

    except req.RequestException as e:
        report_proxy_status(proxy_url, success=False)
        return jsonify({"error": f"网络请求失败: {str(e)}"}), 500


@accounts_bp.route("", methods=["POST"])
def add_account():
    """添加公众号到收藏"""
    data = request.get_json() or {}
    fakeid = data.get("fakeid", "").strip()
    nickname = data.get("nickname", "").strip()

    if not fakeid or not nickname:
        return jsonify({"error": "fakeid 和 nickname 不能为空"}), 400

    accounts = _load_accounts()

    # 检查是否已存在
    for acc in accounts:
        if acc.get("fakeid") == fakeid:
            return jsonify({"error": "该公众号已在收藏中"}), 400

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
        # 立即执行一次抓取，让 RSS 马上有内容
        import threading
        threading.Thread(
            target=rss_scheduler._fetch_for_account, args=(sub,), daemon=True
        ).start()

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
    """获取所有 RSS 订阅状态"""
    from backend.rss_scheduler import rss_scheduler

    subs = rss_scheduler.get_subscriptions()
    return jsonify({"subscriptions": subs})


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
                s["last_upload_time"] = time.time()
                s["last_upload_count"] = result.get("count", 0)
                s["last_upload_error"] = result.get("error")
                s["pending_upload_count"] = result.get("pending_count", 0)
                s["last_upload_attempted"] = True
                s["last_upload_disabled"] = False
                break
        rss_scheduler._save_subscriptions(subs)

    return jsonify(result)
