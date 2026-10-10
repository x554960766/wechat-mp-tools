"""
公众号管理模块
搜索、收藏、管理公众号列表
完全使用微信读书官方检索与元数据接口实现
"""

import re
import html
import time
import base64
import urllib.parse
import requests as req
from flask import Blueprint, jsonify, request

from backend.config import (
    ACCOUNTS_FILE, CONFIG_FILE, load_json, save_json,
    get_proxies_dict, get_default_wechat_ua
)
from backend.account_pool import borrow_weread_session, account_pool, get_weread_session

accounts_bp = Blueprint("accounts", __name__, url_prefix="/api/accounts")


def biz_to_book_id(fakeid_or_biz: str) -> str:
    """将微信 Base64 __biz / fakeid 转换为微信读书官方 bookId (MP_WXS_{uin})"""
    if not fakeid_or_biz:
        return ""
    fid = str(fakeid_or_biz).strip()
    if fid.startswith("MP_"):
        return fid
    raw = urllib.parse.unquote(fid).strip()
    missing_padding = len(raw) % 4
    if missing_padding:
        raw += '=' * (4 - missing_padding)
    try:
        decoded = base64.b64decode(raw).decode('utf-8', errors='ignore').strip()
        if decoded.isdigit():
            return f"MP_WXS_{decoded}"
    except Exception:
        pass
    if fid.isdigit():
        return f"MP_WXS_{fid}"
    return fid


def book_id_to_biz(book_id: str) -> str:
    """将微信读书 MP_WXS_{uin} 转换回 Base64 __biz"""
    if not book_id:
        return ""
    bid = str(book_id).strip()
    if bid.startswith("MP_WXS_"):
        uin = bid[len("MP_WXS_"):]
        if uin.isdigit():
            return base64.b64encode(uin.encode("utf-8")).decode("ascii")
    return bid


def _load_accounts() -> list:
    """加载已收藏的公众号列表"""
    raw_accounts = load_json(ACCOUNTS_FILE, [])
    if not isinstance(raw_accounts, list):
        return []
    # 保证每条账号都有标准化 bookId
    updated = False
    for a in raw_accounts:
        if isinstance(a, dict):
            fid = a.get("fakeid", "")
            if not a.get("bookId"):
                a["bookId"] = biz_to_book_id(fid)
                updated = True
    if updated:
        save_json(ACCOUNTS_FILE, raw_accounts)
    return raw_accounts


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
    """
    搜索公众号：
    1. 若输入为文章 URL 或 biz，通过解析/WeRead接口直达提取；
    2. 若输入为公众号名称/关键字，通过微信读书官方公众号专属检索 (scope=2) 提取。
    """
    data = request.get_json() or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "请输入搜索关键字或公众号文章链接"}), 400

    try:
        acc_id, vid, token, headers = borrow_weread_session()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    results = []
    seen_ids = set()

    s = get_weread_session()

    # ── 模式 1：链接 / biz 解析 ──────────────────────────
    if keyword.startswith("http://") or keyword.startswith("https://") or keyword.startswith("biz:"):
        clean_url = keyword.replace("biz:", "").strip()
        book_id = None
        fakeid = None

        # 尝试从 URL 中直接提取 __biz
        parsed = urllib.parse.urlparse(clean_url)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("__biz") and qs.get("__biz")[0]:
            fakeid = qs.get("__biz")[0].strip()
            book_id = biz_to_book_id(fakeid)

        # 若未从 URL 提取到，调用微信读书 /mp/getreviewid
        if not book_id:
            try:
                res = s.post(
                    "https://i.weread.qq.com/mp/getreviewid",
                    json={"urls": [clean_url]},
                    headers=headers,
                    timeout=10,
                ).json()
                book_id = res.get("bookId")
                if not book_id and res.get("reviewIds"):
                    book_id = res["reviewIds"][0].get("bookId")
            except Exception:
                pass

        # 若仍未得到，尝试请求文章网页源码提取 __biz
        if not book_id:
            try:
                page_resp = req.get(clean_url, headers={"User-Agent": get_default_wechat_ua()}, timeout=10)
                m = (re.search(r'__biz=([A-Za-z0-9+/=]{10,})', page_resp.text) or
                     re.search(r'var\s+biz\s*=\s*"([A-Za-z0-9+/=]{10,})"', page_resp.text))
                if m:
                    fakeid = m.group(1).strip()
                    book_id = biz_to_book_id(fakeid)
            except Exception:
                pass

        if not book_id:
            return jsonify({"error": "未能从该链接解析出微信公众号信息，请确认链接是否有效"}), 400

        # 获取该公众号详情元数据
        try:
            info_resp = s.get(f"https://i.weread.qq.com/book/info?bookId={book_id}", headers=headers, timeout=10).json()
            title = info_resp.get("title") or "微信公众号"
            cover = info_resp.get("cover") or ""
            intro = info_resp.get("intro") or ""
            author = info_resp.get("author") or "公众号"
            fid = fakeid or book_id_to_biz(book_id) or book_id
            return jsonify({
                "results": [{
                    "fakeid": fid,
                    "bookId": book_id,
                    "nickname": title,
                    "alias": fid,
                    "round_head_img": cover,
                    "signature": intro,
                    "service_type": 1,
                }]
            })
        except Exception as e:
            return jsonify({"error": f"获取公众号元数据失败: {e}"}), 500

    # ── 模式 2：名称关键字搜索 (微信读书官方 scope=2 检索) ──
    try:
        encoded_kw = urllib.parse.quote(keyword)
        # 优先使用 scope=2 官方公众号专属库检索
        url_mp = f"https://i.weread.qq.com/store/search?keyword={encoded_kw}&scope=2&v=2&count=20"
        resp = s.get(url_mp, headers=headers, timeout=10)

        # 401 自动上报与静默刷新重试
        if resp.status_code == 401:
            account_pool.report(acc_id, status_code=401)
            acc_id, vid, token, headers = borrow_weread_session()
            resp = s.get(url_mp, headers=headers, timeout=10)

        if resp.status_code == 200:
            books = resp.json().get("books", [])
            for b in books:
                b_info = b.get("bookInfo", {})
                b_id = b_info.get("bookId", "")
                if not b_id or b_id in seen_ids:
                    continue
                seen_ids.add(b_id)
                fid = book_id_to_biz(b_id) or b_id
                results.append({
                    "fakeid": fid,
                    "bookId": b_id,
                    "nickname": b_info.get("title", ""),
                    "alias": fid,
                    "round_head_img": b_info.get("cover", ""),
                    "signature": b_info.get("intro", ""),
                    "service_type": 1,
                })

        # 若 scope=2 无结果，拉取通用搜索作为补充
        if not results:
            url_gen = f"https://i.weread.qq.com/store/search?keyword={encoded_kw}&v=2&count=20"
            resp_gen = s.get(url_gen, headers=headers, timeout=10)
            if resp_gen.status_code == 200:
                for b in resp_gen.json().get("books", []):
                    b_info = b.get("bookInfo", {})
                    b_id = b_info.get("bookId", "")
                    if b_id.startswith("MP_") and b_id not in seen_ids:
                        seen_ids.add(b_id)
                        fid = book_id_to_biz(b_id) or b_id
                        results.append({
                            "fakeid": fid,
                            "bookId": b_id,
                            "nickname": b_info.get("title", ""),
                            "alias": fid,
                            "round_head_img": b_info.get("cover", ""),
                            "signature": b_info.get("intro", ""),
                            "service_type": 1,
                        })

        return jsonify({"results": results, "total": len(results)})

    except Exception as e:
        return jsonify({"error": f"搜索公众号失败: {str(e)}"}), 500


@accounts_bp.route("", methods=["POST"])
def add_account():
    """收藏/添加公众号"""
    data = request.get_json() or {}
    fakeid = data.get("fakeid", "").strip()
    book_id = data.get("bookId", "").strip() or biz_to_book_id(fakeid)
    nickname = data.get("nickname", "").strip()

    if not fakeid and not book_id:
        return jsonify({"error": "缺少公众号标识 (fakeid 或 bookId)"}), 400
    if not nickname:
        return jsonify({"error": "缺少公众号名称"}), 400

    if not fakeid:
        fakeid = book_id_to_biz(book_id) or book_id

    accounts = _load_accounts()

    # 查重：避免重复添加
    for a in accounts:
        if a.get("fakeid") == fakeid or (book_id and a.get("bookId") == book_id):
            return jsonify({"message": "该公众号已在收藏列表中", "account": a})

    new_account = {
        "fakeid": fakeid,
        "bookId": book_id,
        "nickname": nickname,
        "alias": data.get("alias", fakeid),
        "round_head_img": data.get("round_head_img", ""),
        "signature": data.get("signature", ""),
        "service_type": data.get("service_type", 1),
        "added_time": time.time(),
    }

    accounts.insert(0, new_account)
    _save_accounts(accounts)

    return jsonify({"message": "添加成功", "account": new_account})


@accounts_bp.route("/<fakeid>", methods=["DELETE"])
def remove_account(fakeid):
    """取消收藏公众号"""
    accounts = _load_accounts()
    target_book_id = biz_to_book_id(fakeid)

    before_count = len(accounts)
    accounts = [a for a in accounts if a.get("fakeid") != fakeid and a.get("bookId") != target_book_id]

    if len(accounts) == before_count:
        return jsonify({"error": "未找到该公众号"}), 404

    _save_accounts(accounts)
    return jsonify({"message": "已取消收藏"})


@accounts_bp.route("/<fakeid>", methods=["PUT"])
def update_account(fakeid):
    """更新公众号备注信息"""
    data = request.get_json() or {}
    accounts = _load_accounts()
    target_book_id = biz_to_book_id(fakeid)

    target = None
    for a in accounts:
        if a.get("fakeid") == fakeid or a.get("bookId") == target_book_id:
            target = a
            break

    if not target:
        return jsonify({"error": "未找到该公众号"}), 404

    if "nickname" in data:
        target["nickname"] = data["nickname"]
    if "signature" in data:
        target["signature"] = data["signature"]

    _save_accounts(accounts)
    return jsonify({"message": "更新成功", "account": target})


@accounts_bp.route("/user-mps", methods=["GET"])
def get_user_subscribed_mps():
    """获取当前登录微信读书用户已关注的公众号列表"""
    try:
        acc_id, vid, token, headers = borrow_weread_session()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    try:
        s = get_weread_session()
        url = "https://i.weread.qq.com/mp/list?listType=0&count=50"
        resp = s.get(url, headers=headers, timeout=10)
        if resp.status_code == 401:
            account_pool.report(acc_id, status_code=401)
            acc_id, vid, token, headers = borrow_weread_session()
            resp = s.get(url, headers=headers, timeout=10)

        mps = []
        if resp.status_code == 200:
            for it in resp.json().get("lists", []):
                # mp item
                pass
        return jsonify({"mps": mps})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── RSS 订阅路由保持向后兼容 ──────────────────────────

@accounts_bp.route("/<fakeid>/rss-subscribe", methods=["POST"])
def rss_subscribe(fakeid):
    from backend.rss_scheduler import rss_scheduler
    data = request.get_json(silent=True) or {}
    interval = data.get("interval_minutes", 60)
    sub = rss_scheduler.add_subscription(fakeid, interval_minutes=interval)
    if not sub:
        return jsonify({"error": "添加订阅失败，请确认公众号已收藏"}), 400
    return jsonify({"message": "已启用 RSS 自动抓取", "subscription": sub})


@accounts_bp.route("/<fakeid>/rss-subscribe", methods=["DELETE"])
def rss_unsubscribe(fakeid):
    from backend.rss_scheduler import rss_scheduler
    ok = rss_scheduler.remove_subscription(fakeid)
    if not ok:
        return jsonify({"error": "未找到该订阅"}), 404
    return jsonify({"message": "已停用 RSS 自动抓取"})


@accounts_bp.route("/rss-subscriptions", methods=["GET"])
def rss_subscriptions():
    from backend.rss_scheduler import rss_scheduler
    return jsonify(rss_scheduler.list_subscriptions())


@accounts_bp.route("/<fakeid>/rss-force-upload", methods=["POST"])
def rss_force_upload(fakeid):
    from backend.rss_scheduler import rss_scheduler
    res = rss_scheduler.force_upload_account(fakeid)
    return jsonify(res)


@accounts_bp.route("/rss-upload-log", methods=["GET"])
def rss_upload_log():
    from backend.rss_scheduler import rss_scheduler
    account = request.args.get("account")
    limit = request.args.get("limit", 30, type=int)
    return jsonify({"logs": rss_scheduler.get_upload_logs(account=account, limit=limit)})
