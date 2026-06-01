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
