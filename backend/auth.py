"""
登录认证模块
管理微信公众平台扫码登录、凭证验证和状态查询
"""

import json
import time
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from flask import Blueprint, jsonify, request

from backend.config import (
    CONFIG_FILE, DATA_DIR, load_json, save_json,
    get_proxy_config, get_proxy_url
)
from backend.runtime import launch_chromium
from backend.account_pool import account_pool, LOGIN_VALID_SECONDS as POOL_LOGIN_VALID_SECONDS

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

LOGIN_VALID_SECONDS = 4 * 24 * 60 * 60

# 登录状态管理
_login_state = {
    "status": "idle",       # idle / scanning / success / failed
    "message": "",
    "progress": 0,
    "qrcode": "",           # QR code image base64
}
_login_lock = threading.Lock()
_active_browser = None      # 当前活跃的后台 Playwright 浏览器实例


def _set_login_state(status: str, message: str = "", progress: int = 0, qrcode: str = ""):
    with _login_lock:
        _login_state["status"] = status
        _login_state["message"] = message
        _login_state["progress"] = progress
        _login_state["qrcode"] = qrcode


def _credential_expiry(save_time: float) -> dict:
    now = time.time()
    expires_at = save_time + LOGIN_VALID_SECONDS if save_time else 0
    remaining_seconds = max(0, int(expires_at - now)) if expires_at else 0
    expired = not save_time or remaining_seconds <= 0
    return {
        "valid_days": 4,
        "expires_at": expires_at,
        "remaining_seconds": remaining_seconds,
        "expired": expired,
    }


import re

def _fetch_wechat_account_info(token: str, cookie_str: str) -> dict | None:
    """利用 requests 抓取微信公众号首页并解析账号信息"""
    try:
        import requests as req
        from backend.config import DEFAULT_HEADERS, BASE_URL, get_proxies_dict
        headers = {**DEFAULT_HEADERS, "Cookie": cookie_str}
        proxies = get_proxies_dict()
        resp = req.get(
            f"{BASE_URL}/cgi-bin/home",
            params={
                "t": "home/index",
                "lang": "zh_CN",
                "token": token,
            },
            headers=headers,
            proxies=proxies,
            timeout=8
        )
        if resp.status_code == 200:
            html = resp.text
            nickname = ""
            avatar = ""
            
            # 正则匹配昵称
            nick_match = re.search(r'class="weui-desktop-account__nickname"[^>]*>([^<]+)<', html)
            if nick_match:
                nickname = nick_match.group(1).strip()
            else:
                nick_match2 = re.search(r'nickname\s*:\s*"([^"]+)"', html)
                if nick_match2:
                    nickname = nick_match2.group(1)
                    
            # 正则匹配头像
            avatar_match = re.search(r'class="weui-desktop-account__avatar"[^>]*src="([^"]+)"', html)
            if avatar_match:
                avatar = avatar_match.group(1)
            else:
                avatar_match2 = re.search(r'avatar\s*:\s*"([^"]+)"', html)
                if avatar_match2:
                    avatar = avatar_match2.group(1)
                    
            if not nickname:
                nick_match3 = re.search(r'<div class="weui-desktop-account__name">([^<]+)</div>', html)
                if nick_match3:
                    nickname = nick_match3.group(1).strip()
                    
            if nickname:
                return {
                    "nickname": nickname,
                    "avatar": avatar
                }
    except Exception as e:
        print(f"Failed to fetch WeChat account info: {e}")
    return None


@auth_bp.route("/status", methods=["GET"])
def get_status():
    """获取登录状态（聚合账号池中第一个 active 账号）"""
    accounts = account_pool.list_accounts()
    # 找第一个 active 账号
    active_acc = None
    for acc in accounts:
        if acc["status"] == "active" and not acc.get("expired"):
            active_acc = acc
            break

    if not active_acc:
        # 无可用账号，检查是否有任何账号（包括过期/被踢出的）
        if accounts:
            last_acc = accounts[0]
            return jsonify({
                "logged_in": False,
                "login_state": _login_state,
                "token_preview": last_acc.get("token_preview", ""),
                "save_time": last_acc.get("save_time", 0),
                "may_expired": True,
                **_credential_expiry(last_acc.get("save_time", 0)),
                "message": "登录已过期或账号已被踢出，请重新登录",
                "account_info": {
                    "nickname": last_acc.get("nickname", ""),
                    "avatar": last_acc.get("avatar", ""),
                },
                "pool_summary": account_pool.get_summary(),
            })
        return jsonify({
            "logged_in": False,
            "login_state": _login_state,
            "message": "未登录，请先在账号池页面添加账号"
        })

    save_time = active_acc.get("save_time", 0)
    expiry = _credential_expiry(save_time)

    return jsonify({
        "logged_in": True,
        "login_state": _login_state,
        "token_preview": active_acc.get("token_preview", ""),
        "save_time": save_time,
        "may_expired": False,
        **expiry,
        "message": "登录有效",
        "account_info": {
            "nickname": active_acc.get("nickname", ""),
            "avatar": active_acc.get("avatar", ""),
        },
        "pool_summary": account_pool.get_summary(),
    })


@auth_bp.route("/login", methods=["POST"])
def start_login():
    """启动浏览器窗口扫码登录（异步）"""
    if _login_state["status"] == "scanning":
        return jsonify({"error": "正在登录中，请勿重复操作"}), 400

    thread = threading.Thread(target=_do_login, daemon=True)
    thread.start()

    return jsonify({"message": "已启动登录流程，请在弹出的浏览器窗口中扫码"})


@auth_bp.route("/login-browser", methods=["POST"])
def start_browser_login():
    """兼容旧前端按钮，实际复用浏览器窗口扫码登录。"""
    return start_login()


def _do_login():
    """执行扫码登录（在后台线程中运行，使用浏览器窗口登录并保存凭证）"""
    global _active_browser
    
    # 强行中止之前未完成的后台 Playwright 浏览器
    with _login_lock:
        if _active_browser is not None:
            try:
                _active_browser.close()
            except Exception:
                pass
            _active_browser = None

    _set_login_state("scanning", "正在打开微信公众平台...", 10)

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        proxy_config = get_proxy_config()
        proxy_url = get_proxy_url(proxy_config)

        with sync_playwright() as p:
            launch_args = [
                "--window-size=1280,900",
                "--disable-blink-features=AutomationControlled"
            ]
            launch_kwargs = {
                "headless": False,
                "args": launch_args,
            }
            if proxy_url:
                launch_kwargs["proxy"] = {"server": proxy_url}
                if proxy_config.get("username"):
                    launch_kwargs["proxy"]["username"] = proxy_config["username"]
                    launch_kwargs["proxy"]["password"] = proxy_config.get("password", "")

            browser = launch_chromium(p.chromium, **launch_kwargs)
            
            with _login_lock:
                _active_browser = browser

            ctx = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            # 注入脚本，绕过 webdriver 检测
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = ctx.new_page()

            page.goto("https://mp.weixin.qq.com/", timeout=30000)
            # goto 已等到 load 事件，页面可用。不等待 networkidle，因微信页面有长轮询/WebSocket 永不 idle

            _set_login_state("scanning", "请在弹出的浏览器窗口中扫码登录", 50)

            # 等待登录跳转（最多 5 分钟）
            try:
                page.wait_for_url("**/cgi-bin/home**", timeout=300_000)
            except PWTimeout:
                _set_login_state("failed", "扫码超时（5分钟），请重试")
                browser.close()
                with _login_lock:
                    _active_browser = None
                return

            _set_login_state("scanning", "扫码成功，正在保存凭证...", 80)

            # 提取 token
            url_params = parse_qs(urlparse(page.url).query)
            token = url_params.get("token", [""])[0]

            if not token:
                _set_login_state("failed", "未能从 URL 提取 token")
                browser.close()
                with _login_lock:
                    _active_browser = None
                return

            # 保存 cookies
            cookies = ctx.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

            config = {
                "token": token,
                "cookie_str": cookie_str,
                "cookies": cookies,
                "save_time": time.time(),
                "account_info": {
                    "nickname": "公众号未命名",
                    "avatar": ""
                }
            }

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            save_json(CONFIG_FILE, config)

            # 写入账号池
            account_pool.add_or_update({
                "token": token,
                "cookie_str": cookie_str,
                "cookies": cookies,
                "nickname": "公众号未命名",
                "avatar": "",
                "save_time": time.time(),
            })

            # token/cookie 已经可用，先让前端结束等待；昵称头像只是展示信息，后续短超时补充。
            _set_login_state("success", f"登录成功！token = {token[:12]}...", 100)

            try:
                nickname = ""
                avatar = ""
                nickname_locator = page.locator(".weui-desktop-account__nickname")
                if nickname_locator.count() > 0:
                    nickname = nickname_locator.first.inner_text(timeout=1200)
                else:
                    name_locator = page.locator(".weui-desktop-account__name")
                    if name_locator.count() > 0:
                        nickname = name_locator.first.inner_text(timeout=1200)

                avatar_elem = page.locator(".weui-desktop-account__avatar")
                if avatar_elem.count() > 0:
                    avatar = avatar_elem.first.get_attribute("src", timeout=1200) or ""

                if nickname or avatar:
                    config["account_info"] = {
                        "nickname": nickname or "公众号未命名",
                        "avatar": avatar
                    }
                    save_json(CONFIG_FILE, config)
                    # 同步更新账号池中的昵称/头像
                    account_pool.add_or_update({
                        "token": token,
                        "cookie_str": cookie_str,
                        "cookies": cookies,
                        "nickname": nickname or "公众号未命名",
                        "avatar": avatar,
                        "save_time": config["save_time"],
                    })
            except Exception:
                pass

            try:
                browser.close()
            except Exception as e:
                print(f"Error closing browser: {e}")
            with _login_lock:
                _active_browser = None

    except Exception as e:
        with _login_lock:
            current_status = _login_state["status"]
        if current_status != "idle":
            _set_login_state("failed", f"登录失败: {str(e)}")
        with _login_lock:
            _active_browser = None


@auth_bp.route("/cancel", methods=["POST"])
def cancel_login():
    """取消扫码登录"""
    global _active_browser
    with _login_lock:
        if _active_browser is not None:
            try:
                _active_browser.close()
            except Exception:
                pass
            _active_browser = None
    _set_login_state("idle", "登录已取消")
    return jsonify({"message": "登录已取消"})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """清除登录凭证"""
    global _active_browser
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
    with _login_lock:
        if _active_browser is not None:
            try:
                _active_browser.close()
            except Exception:
                pass
            _active_browser = None
    _set_login_state("idle", "已退出登录")
    return jsonify({"message": "已退出登录"})


@auth_bp.route("/check-credentials", methods=["GET"])
def check_credentials():
    """验证凭证是否有效（通过账号池中第一个 active 账号调一次 API 测试）"""
    from backend.account_pool import borrow_session

    try:
        account_id, token, cookie_str = borrow_session()
    except RuntimeError:
        return jsonify({"valid": False, "message": "无可用账号"})

    proxy_url = None
    try:
        import requests as req
        from backend.config import DEFAULT_HEADERS, BASE_URL, get_proxies_dict, report_proxy_status

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
                "query": "微信",
                "begin": "0",
                "count": "1",
            },
            headers=headers,
            proxies=proxies,
            timeout=15,
        )
        
        if resp.status_code != 200:
            report_proxy_status(proxy_url, success=False)
            account_pool.report(account_id, http_ok=False, error=f"HTTP {resp.status_code}")
            return jsonify({"valid": False, "message": f"HTTP {resp.status_code}"})

        report_proxy_status(proxy_url, success=True)
        data = resp.json()
        ret = data.get("base_resp", {}).get("ret", -1)

        account_pool.report(account_id, ret=ret)

        if ret == 0:
            return jsonify({"valid": True, "message": "凭证有效"})
        elif ret == 200003:
            return jsonify({"valid": False, "message": "凭证已过期，请重新登录"})
        else:
            return jsonify({"valid": False, "message": f"API 返回错误 (ret={ret})"})

    except Exception as e:
        report_proxy_status(proxy_url, success=False)
        return jsonify({"valid": False, "message": f"检测失败: {str(e)}"})
