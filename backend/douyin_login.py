"""
抖音登录模块
使用 Playwright 提供抖音的独立登录功能，提取 Cookie。
"""

import json
import time
import threading
from urllib.parse import urlparse
from flask import Blueprint, jsonify, request
from playwright.sync_api import sync_playwright

from backend.config import get_settings, save_settings

douyin_login_bp = Blueprint("douyin_login", __name__, url_prefix="/api/douyin-auth")

# 登录状态管理
_dy_login_state = {
    "status": "idle",       # idle / scanning / success / failed
    "message": "",
    "progress": 0,
    "qrcode": "",
}
_dy_login_lock = threading.Lock()
_active_browser = None


def _set_login_state(status: str, message: str = "", progress: int = 0, qrcode: str = ""):
    with _dy_login_lock:
        _dy_login_state["status"] = status
        _dy_login_state["message"] = message
        _dy_login_state["progress"] = progress
        _dy_login_state["qrcode"] = qrcode


@douyin_login_bp.route("/status", methods=["GET"])
def get_status():
    """获取抖音登录状态"""
    settings = get_settings()
    cookie = settings.get("douyin_cookie", "")
    
    if not cookie:
        return jsonify({
            "logged_in": False,
            "login_state": _dy_login_state,
            "message": "未登录，请先登录抖音"
        })

    # 判断 Cookie 有效性（此处简单判断有无 sessionid）
    if "sessionid" in cookie:
        return jsonify({
            "logged_in": True,
            "login_state": _dy_login_state,
            "message": "登录有效"
        })
    else:
        return jsonify({
            "logged_in": False,
            "login_state": _dy_login_state,
            "message": "凭证失效或不完整，请重新登录"
        })


@douyin_login_bp.route("/login", methods=["POST"])
def start_login():
    """启动扫码登录（异步）"""
    if _dy_login_state["status"] == "scanning":
        return jsonify({"error": "正在登录中，请勿重复操作"}), 400

    thread = threading.Thread(target=_do_login, daemon=True)
    thread.start()

    return jsonify({"message": "已启动登录流程，请在弹出的浏览器窗口中扫码"})


def _do_login():
    global _active_browser
    
    with _dy_login_lock:
        if _active_browser is not None:
            try:
                _active_browser.close()
            except Exception:
                pass
            _active_browser = None

    _set_login_state("scanning", "正在启动浏览器...", 10)

    try:
        with sync_playwright() as p:
            # 抖音有很强的反爬，建议非无头模式，让用户自己扫码
            browser = p.chromium.launch(headless=False, args=['--disable-blink-features=AutomationControlled'])
            _active_browser = browser
            
            context = browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            # 反爬绕过
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            page = context.new_page()
            _set_login_state("scanning", "正在打开抖音网页版，请扫码登录...", 30)
            
            page.goto("https://www.douyin.com/", timeout=60000)
            
            # 点击登录按钮
            try:
                page.click('xpath=//div[contains(text(), "登录") or contains(text(), "Log in")]', timeout=10000)
            except Exception:
                pass # 忽略找不到按钮的错误，可能页面样式变了

            _set_login_state("scanning", "请在弹出的浏览器中完成扫码或验证码登录", 50)
            
            # 轮询检查是否登录成功 (寻找头像或特定的 cookie)
            login_success = False
            for _ in range(120): # 120 * 2 = 240 秒超时
                cookies = context.cookies()
                has_session = any(c['name'] == 'sessionid' for c in cookies)
                if has_session:
                    login_success = True
                    break
                
                time.sleep(2)
                
            if login_success:
                _set_login_state("scanning", "登录成功，正在提取 Cookie...", 90)
                
                # 提取 Cookie
                cookies = context.cookies()
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                
                # 保存到 settings
                current_settings = get_settings()
                current_settings["douyin_cookie"] = cookie_str
                save_settings(current_settings)
                
                _set_login_state("success", "登录成功！", 100)
            else:
                _set_login_state("failed", "登录超时，未检测到有效登录状态", 0)

            time.sleep(2) # 缓冲
            browser.close()
            _active_browser = None

    except Exception as e:
        _set_login_state("failed", f"登录异常: {str(e)}", 0)
        if _active_browser:
            try:
                _active_browser.close()
            except Exception:
                pass
            _active_browser = None
