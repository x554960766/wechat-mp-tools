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


# ── 抖音凭证验证缓存 ──────────────────────────────────────────
_douyin_cache = {
    "cookie": "",
    "valid": False,
    "account_info": None,
    "last_check": 0.0
}
_douyin_cache_lock = threading.Lock()

def validate_douyin_cached(cookie_str: str) -> dict | None:
    """带 5 分钟内存缓存的抖音 Cookie 验证器"""
    global _douyin_cache
    
    if not cookie_str or "sessionid" not in cookie_str:
        return None
        
    now = time.time()
    with _douyin_cache_lock:
        # 如果 Cookie 相同且距离上次检查小于 300 秒，直接返回缓存结果
        if _douyin_cache["cookie"] == cookie_str and (now - _douyin_cache["last_check"]) < 300.0:
            return _douyin_cache["account_info"] if _douyin_cache["valid"] else None

    # 执行真实网络请求校验
    try:
        import requests as req
        from backend.config import get_proxies_dict
        
        session = req.Session()
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.douyin.com/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            "Cookie": cookie_str
        })
        proxies = get_proxies_dict()
        if proxies:
            session.proxies.update(proxies)
            
        # 加上必备的客户端参数，防止被抖音判定为异常请求返回 403 或重定向
        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "pc_client_type": "1",
            "version_code": "190600",
            "version_name": "19.6.0",
            "cookie_enabled": "true",
            "platform": "PC"
        }
            
        resp = session.get("https://www.douyin.com/aweme/v1/web/user/profile/self/", params=params, timeout=8)
        
        # 如果是明显的未登录状态码（401/403）或被重定向到登录页面，代表凭证失效
        if resp.status_code in (401, 403) or "login" in resp.url:
            raise ValueError("Session expired")
            
        if resp.status_code == 200 and len(resp.content) > 0:
            data = resp.json()
            if data.get("status_code") == 0 and "user" in data:
                user = data["user"]
                
                # 提取最高清的头像
                avatar = ""
                for size_key in ["avatar_thumb", "avatar_larger", "avatar_medium"]:
                    size_dict = user.get(size_key, {})
                    if size_dict and size_dict.get("url_list"):
                        avatar = size_dict["url_list"][0]
                        break
                        
                account_info = {
                    "nickname": user.get("nickname", "未知用户"),
                    "avatar": avatar,
                    "unique_id": user.get("unique_id") or user.get("short_id") or "",
                    "signature": user.get("signature", ""),
                    "sec_uid": user.get("sec_uid", "")
                }
                
                # 写入缓存
                with _douyin_cache_lock:
                    _douyin_cache["cookie"] = cookie_str
                    _douyin_cache["valid"] = True
                    _douyin_cache["account_info"] = account_info
                    _douyin_cache["last_check"] = now
                return account_info
            elif data.get("status_code") in (2093, 20000) or "error" in data:
                # 抖音明确返回未登录等报错状态码
                raise ValueError("Session unauthorized by API response")
                
    except ValueError as ve:
        # 确实失效了，清除缓存并标记失效
        print(f"Douyin Cookie verified as invalid: {ve}")
        with _douyin_cache_lock:
            _douyin_cache["cookie"] = cookie_str
            _douyin_cache["valid"] = False
            _douyin_cache["account_info"] = None
            _douyin_cache["last_check"] = now
        return None
        
    except Exception as e:
        # 网络抖动、DNS 异常或代理超时：不主动判定失效，保留之前的缓存以防止误登出
        print(f"Network error verifying Douyin Cookie (ignored to avoid false logout): {e}")
        with _douyin_cache_lock:
            if _douyin_cache["cookie"] == cookie_str and _douyin_cache["valid"]:
                _douyin_cache["last_check"] = now  # 延长校验时间，避免频繁重试堵塞
                return _douyin_cache["account_info"]
                
        # 冷启动时如果断网/超时，返回默认的在线状态数据结构，保留 Cookie
        return {
            "nickname": "已登录 (网络检测超时)",
            "avatar": "",
            "unique_id": "network_checking"
        }


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

    # 进行真实的网络校验
    account_info = validate_douyin_cached(cookie)
    if account_info:
        return jsonify({
            "logged_in": True,
            "login_state": _dy_login_state,
            "message": "登录有效",
            "account_info": account_info
        })
    else:
        # Cookie 已失效，自动清除
        settings["douyin_cookie"] = ""
        save_settings(settings)
        return jsonify({
            "logged_in": False,
            "login_state": _dy_login_state,
            "message": "凭证已失效或过期，请重新登录"
        })


@douyin_login_bp.route("/logout", methods=["POST"])
def logout():
    """清除抖音登录凭证"""
    settings = get_settings()
    if "douyin_cookie" in settings:
        settings["douyin_cookie"] = ""
        save_settings(settings)
    
    # 清空缓存
    global _douyin_cache
    with _douyin_cache_lock:
        _douyin_cache["cookie"] = ""
        _douyin_cache["valid"] = False
        _douyin_cache["account_info"] = None
        _douyin_cache["last_check"] = 0.0
        
    return jsonify({"message": "已清除登录 Cookie"})


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
