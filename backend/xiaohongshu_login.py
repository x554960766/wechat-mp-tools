"""
小红书登录管理模块
"""

import time
import threading
from flask import Blueprint, jsonify, request
from playwright.sync_api import sync_playwright

from backend.config import get_settings, save_settings
from backend.runtime import launch_chromium
from backend.xiaohongshu import check_xhs_login, XhsClient

xhs_login_bp = Blueprint("xhs_login", __name__, url_prefix="/api/xhs-auth")

# ── 登录状态管理 ──────────────────────────────────────────
_xhs_login_state = {
    "status": "idle",       # idle / scanning / success / failed
    "message": "",
    "progress": 0,
}
_xhs_login_lock = threading.Lock()
_active_browser = None

def _set_login_state(status: str, message: str = "", progress: int = 0):
    with _xhs_login_lock:
        _xhs_login_state["status"] = status
        _xhs_login_state["message"] = message
        _xhs_login_state["progress"] = progress

def _do_login():
    global _active_browser
    
    with _xhs_login_lock:
        if _active_browser is not None:
            try:
                _active_browser.close()
            except Exception:
                pass
            _active_browser = None
            
    _set_login_state("scanning", "正在启动浏览器...", 10)
    
    try:
        with sync_playwright() as p:
            browser = launch_chromium(p.chromium, headless=False, args=['--disable-blink-features=AutomationControlled'])
            _active_browser = browser
            
            context = browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent=XhsClient.USER_AGENT
            )
            
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); Object.defineProperty(navigator, 'platform', {get: () => 'Win32'})")
            
            page = context.new_page()
            _set_login_state("scanning", "正在打开小红书主页，请扫码登录...", 30)
            
            page.goto("https://www.xiaohongshu.com", timeout=60000)
            
            _set_login_state("scanning", "请在弹出的浏览器中完成扫码或验证码登录", 50)

            # 记录初始（游客）web_session，登录成功后该值会变化；变化后用 /user/me 网络确认是否真登录。
            # 不能只看 web_session 是否存在——小红书对游客也下发 web_session。
            def _ws(cs):
                return next((c['value'] for c in cs if c['name'] == 'web_session'), None)
            initial_ws = _ws(context.cookies())

            login_success = False
            cookie_str = ""
            for _ in range(80):  # 80 * 3 = 240 秒
                if not browser.is_connected():
                    break

                current_url = page.url
                cookies = context.cookies()
                ws = _ws(cookies)
                # Wait for the redirect to settle before extracting cookies
                if ("explore" in current_url or "user/profile" in current_url) or (ws and ws != initial_ws):
                    if "explore" in current_url or "user/profile" in current_url:
                        time.sleep(2)
                        cookies = context.cookies()
                    # Deduplicate cookies by name
                    cookie_dict = {}
                    for c in cookies:
                        cookie_dict[c['name']] = c['value']
                    cookie_str = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])
                    
                    login_res = check_xhs_login(cookie_str)
                    if login_res.get("logged_in") is True:
                        login_success = True
                        break
                    elif login_res.get("logged_in") is None:
                        err_msg = str(login_res.get("error", ""))
                        if "登录已过期" not in err_msg and "异常" not in err_msg:
                            if "login" not in current_url and "captcha" not in current_url:
                                login_success = True
                                break
                time.sleep(3)

            if login_success:
                _set_login_state("scanning", "登录成功，正在提取 Cookie...", 90)

                settings = get_settings()
                settings["xhs_cookie"] = cookie_str
                save_settings(settings)

                _set_login_state("success", "Cookie 已获取并保存！", 100)
            else:
                _set_login_state("failed", "登录超时或浏览器被关闭，未检测到 Cookie", 0)
                
            time.sleep(2)
            try:
                browser.close()
            except Exception:
                pass
            _active_browser = None
            
    except Exception as e:
        _set_login_state("failed", f"登录异常: {str(e)}", 0)
        if _active_browser:
            try:
                _active_browser.close()
            except Exception:
                pass
            _active_browser = None

@xhs_login_bp.route("/status", methods=["GET"])
def get_status():
    settings = get_settings()
    cookie = settings.get("xhs_cookie", "").strip()
    cookie_set = bool(cookie)

    # 扫码进行中时不做网络校验，避免与登录线程重复请求 /user/me（前端此时只读 login_state）。
    if _xhs_login_state["status"] == "scanning":
        return jsonify({"cookie_set": cookie_set, "logged_in": False, "guest": None, "login_state": _xhs_login_state})
    logged_in = False
    guest = True
    if cookie_set:
        result = check_xhs_login(cookie)
        logged_in = result.get("logged_in") is True
        guest = result.get("guest") if result.get("guest") is not None else (not logged_in)

    return jsonify({
        "cookie_set": cookie_set,
        "logged_in": logged_in,
        "login_state": _xhs_login_state,
    })

@xhs_login_bp.route("/login", methods=["POST"])
def start_login():
    if _xhs_login_state["status"] == "scanning":
        return jsonify({"error": "正在登录中，请勿重复操作"}), 400
        
    thread = threading.Thread(target=_do_login, daemon=True)
    thread.start()
    return jsonify({"message": "已启动登录流程，请在弹出的浏览器窗口中扫码"})

@xhs_login_bp.route("/save-cookie", methods=["POST"])
def save_cookie_manually():
    data = request.get_json() or {}
    cookie = data.get("cookie", "").strip()
    if not cookie:
        return jsonify({"error": "Cookie 不能为空"}), 400
        
    # Deduplicate manual cookie
    cookie_dict = {}
    for part in cookie.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            cookie_dict[k.strip()] = v.strip()
    cleaned_cookie = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])

    settings = get_settings()
    settings["xhs_cookie"] = cleaned_cookie
    save_settings(settings)

    warning = None
    result = check_xhs_login(cleaned_cookie)
    if result.get("logged_in") is False:
        warning = "这是游客 Cookie（未登录），无法获取博主笔记列表，视频可能仅低清。请改用扫码登录或粘贴登录后的 Cookie。"

    return jsonify({
        "message": "Cookie 保存成功",
        "warning": warning
    })

@xhs_login_bp.route("/logout", methods=["POST"])
def logout():
    settings = get_settings()
    if "xhs_cookie" in settings:
        settings["xhs_cookie"] = ""
        save_settings(settings)
    return jsonify({"message": "已清除登录 Cookie"})
