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

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

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


@auth_bp.route("/status", methods=["GET"])
def get_status():
    """获取登录状态"""
    config = load_json(CONFIG_FILE)
    if not config:
        return jsonify({
            "logged_in": False,
            "login_state": _login_state,
            "message": "未登录，请先扫码登录"
        })

    token = config.get("token", "")
    cookie_str = config.get("cookie_str", "")
    save_time = config.get("save_time", 0)

    if not token or not cookie_str:
        return jsonify({
            "logged_in": False,
            "login_state": _login_state,
            "message": "凭证不完整，请重新登录"
        })

    # 检查凭证是否过期（粗略判断：超过 3 天提示可能过期）
    elapsed = time.time() - save_time if save_time else float('inf')
    may_expired = elapsed > 3 * 86400

    return jsonify({
        "logged_in": True,
        "login_state": _login_state,
        "token_preview": token[:8] + "...",
        "save_time": save_time,
        "may_expired": may_expired,
        "message": "凭证可能已过期，建议重新登录" if may_expired else "登录有效"
    })


@auth_bp.route("/login", methods=["POST"])
def start_login():
    """启动扫码登录（异步）"""
    if _login_state["status"] == "scanning":
        return jsonify({"error": "正在登录中，请勿重复操作"}), 400

    thread = threading.Thread(target=_do_login, daemon=True)
    thread.start()

    return jsonify({"message": "已启动登录流程，请在弹出的浏览器窗口中扫码"})


def _do_login():
    """执行扫码登录（在后台线程中运行，支持端内直显二维码）"""
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
            launch_args = ["--window-size=1280,900"]
            launch_kwargs = {
                "headless": True, # Headless 运行，避免弹窗
                "args": launch_args,
            }
            if proxy_url:
                launch_kwargs["proxy"] = {"server": proxy_url}
                if proxy_config.get("username"):
                    launch_kwargs["proxy"]["username"] = proxy_config["username"]
                    launch_kwargs["proxy"]["password"] = proxy_config.get("password", "")

            browser = p.chromium.launch(**launch_kwargs)
            
            with _login_lock:
                _active_browser = browser

            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            page.goto("https://mp.weixin.qq.com/", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

            _set_login_state("scanning", "正在获取登录二维码...", 30)

            # 获取并截图二维码元素
            try:
                qr_elem = page.wait_for_selector(".login__qrcode", timeout=20000)
                if not qr_elem:
                    raise Exception("未能定位到登录二维码元素 (.login__qrcode)")
                
                import base64
                qr_bytes = qr_elem.screenshot(timeout=10000)
                qr_base64 = base64.b64encode(qr_bytes).decode("utf-8")
                
                _set_login_state("scanning", "📱 请使用微信扫描网页内二维码", 50, qrcode=qr_base64)
            except Exception as qr_err:
                _set_login_state("failed", f"获取二维码失败: {str(qr_err)}")
                browser.close()
                with _login_lock:
                    _active_browser = None
                return

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
            }

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            save_json(CONFIG_FILE, config)

            browser.close()
            with _login_lock:
                _active_browser = None
            _set_login_state("success", f"登录成功！token = {token[:12]}...", 100)

    except Exception as e:
        _set_login_state("failed", f"登录失败: {str(e)}")
        with _login_lock:
            _active_browser = None


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """清除登录凭证"""
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
    _set_login_state("idle", "已退出登录")
    return jsonify({"message": "已退出登录"})


@auth_bp.route("/check-credentials", methods=["GET"])
def check_credentials():
    """验证凭证是否有效（通过调一次 API 测试）"""
    config = load_json(CONFIG_FILE)
    if not config or not config.get("token"):
        return jsonify({"valid": False, "message": "无凭证"})

    proxy_url = None
    try:
        import requests as req
        from backend.config import DEFAULT_HEADERS, BASE_URL, get_proxies_dict, report_proxy_status

        headers = {**DEFAULT_HEADERS, "Cookie": config["cookie_str"]}
        proxies = get_proxies_dict()
        if proxies:
            proxy_url = proxies.get("http")

        resp = req.get(
            f"{BASE_URL}/cgi-bin/searchbiz",
            params={
                "action": "search_biz",
                "token": config["token"],
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
            return jsonify({"valid": False, "message": f"HTTP {resp.status_code}"})

        report_proxy_status(proxy_url, success=True)
        data = resp.json()
        ret = data.get("base_resp", {}).get("ret", -1)

        if ret == 0:
            return jsonify({"valid": True, "message": "凭证有效"})
        elif ret == 200003:
            return jsonify({"valid": False, "message": "凭证已过期，请重新登录"})
        else:
            return jsonify({"valid": False, "message": f"API 返回错误 (ret={ret})"})

    except Exception as e:
        report_proxy_status(proxy_url, success=False)
        return jsonify({"valid": False, "message": f"检测失败: {str(e)}"})
