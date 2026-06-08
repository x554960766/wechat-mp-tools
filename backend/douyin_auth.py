"""
抖音扫码登录模块 - 使用 Playwright 原生窗口
参考 DY_video_downloader 的实现方式
"""
import time
import threading
from flask import Blueprint, jsonify
from playwright.sync_api import sync_playwright

from backend.config import get_settings, save_settings
from backend.runtime import launch_chromium

douyin_auth_bp = Blueprint("douyin_auth", __name__, url_prefix="/api/douyin/auth")

# 登录状态管理
_login_state = {
    "status": "idle",  # idle, scanning, success, expired, error, cancelled
    "message": "",
    "cookie": "",
}
_login_lock = threading.Lock()
_active_browser = None
_cancel_event = threading.Event()

# 登录标记 Cookie 名称
LOGIN_MARKER_KEYS = {'sessionid', 'sessionid_ss', 'sid_guard', 'uid_tt'}


def _set_state(status: str, message: str = "", cookie: str = None):
    """更新登录状态"""
    with _login_lock:
        _login_state["status"] = status
        _login_state["message"] = message
        if cookie is not None:
            _login_state["cookie"] = cookie


def _has_login_cookie(cookies: list) -> bool:
    """检查 Cookie 中是否包含登录标记"""
    cookie_names = {c.get("name", "") for c in cookies}

    # 检查 passport_auth_status
    for c in cookies:
        if c.get("name") == "passport_auth_status" and c.get("value") == "1":
            return True

    # 检查登录标记 Cookie
    return any(name in cookie_names for name in LOGIN_MARKER_KEYS)


def _serialize_cookies(cookies: list) -> str:
    """将 Cookie 列表序列化为字符串"""
    cookie_dict = {}
    for c in cookies:
        name = c.get("name", "").strip()
        value = c.get("value", "").strip()
        if not name or not value:
            continue

        # 检查域名是否适用于 www.douyin.com
        domain = c.get("domain", "").strip().lstrip(".").lower()
        if not domain or domain == "douyin.com" or domain == "www.douyin.com":
            cookie_dict[name] = value

    return "; ".join(f"{name}={value}" for name, value in cookie_dict.items())


@douyin_auth_bp.route("/start", methods=["POST"])
def start_login():
    """启动抖音扫码登录（原生窗口）"""
    global _cancel_event

    if _login_state["status"] == "scanning":
        return jsonify({"error": "正在登录中，请勿重复操作"}), 400

    # 重置取消事件
    _cancel_event.clear()

    thread = threading.Thread(target=_do_login, daemon=True)
    thread.start()

    return jsonify({"message": "已启动登录流程，正在打开登录窗口..."})


@douyin_auth_bp.route("/cancel", methods=["POST"])
def cancel_login():
    """取消正在进行的登录"""
    global _active_browser, _cancel_event

    if _login_state["status"] != "scanning":
        return jsonify({"error": "没有正在进行的登录"}), 400

    # 设置取消标志
    _cancel_event.set()

    # 关闭浏览器
    with _login_lock:
        if _active_browser is not None:
            try:
                _active_browser.close()
            except:
                pass
            _active_browser = None

    _set_state("cancelled", "登录已取消")

    return jsonify({"message": "已取消登录"})


@douyin_auth_bp.route("/status", methods=["GET"])
def check_status():
    """检查抖音登录状态"""
    # 如果正在扫码中，返回扫码的临时状态
    with _login_lock:
        if _login_state["status"] == "scanning":
            return jsonify(_login_state)
            
    # 否则，以配置中的 Cookie 为准进行真实或缓存验证
    settings = get_settings()
    cookie = settings.get("douyin_cookie", "")
    if cookie:
        from backend.douyin_login import validate_douyin_cached
        info = validate_douyin_cached(cookie)
        if info:
            return jsonify({
                "status": "success",
                "message": "登录有效",
                "cookie": cookie,
                "account_info": info
            })
        else:
            return jsonify({
                "status": "expired",
                "message": "凭证已失效，请重新登录",
                "cookie": ""
            })
            
    # 否则返回本地内存中的登录标记状态
    with _login_lock:
        return jsonify(_login_state)


def _do_login():
    """执行后台 Playwright 操作进行登录"""
    global _active_browser, _cancel_event

    with _login_lock:
        if _active_browser is not None:
            try:
                _active_browser.close()
            except:
                pass
            _active_browser = None

    _set_state("scanning", "正在初始化浏览器...")

    try:
        with sync_playwright() as p:
            # 启动浏览器（非 headless 模式，让用户看到登录界面）
            browser = launch_chromium(
                p.chromium,
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )
            _active_browser = browser

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )

            # 反爬虫绕过
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page = context.new_page()

            _set_state("scanning", "正在打开抖音登录页面，请扫码登录...")

            # 直接打开抖音首页，让用户自己点击登录按钮并扫码
            page.goto("https://www.douyin.com/", timeout=60000)

            # 尝试点击登录按钮（可选，用户也可以手动点击）
            try:
                page.wait_for_timeout(2000)  # 等待页面加载
                # 尝试多种选择器
                login_selectors = [
                    'text="登录"',
                    'text="Log in"',
                    'div:has-text("登录")',
                    '[class*="login"]',
                ]
                for selector in login_selectors:
                    try:
                        page.click(selector, timeout=3000)
                        break
                    except:
                        continue
            except:
                pass  # 忽略点击失败，用户可以手动点击

            _set_state("scanning", "请在浏览器窗口中完成扫码登录")

            # 轮询检查是否登录成功（最多等待 5 分钟）
            login_success = False
            timeout_seconds = 300
            start_time = time.time()
            last_cookie_check = ""

            while time.time() - start_time < timeout_seconds:
                # 检查是否被取消
                if _cancel_event.is_set():
                    _set_state("cancelled", "登录已取消")
                    browser.close()
                    _active_browser = None
                    return

                # 检查浏览器是否被关闭
                if page.is_closed():
                    _set_state("cancelled", "登录窗口已关闭")
                    browser.close()
                    _active_browser = None
                    return

                # 获取 Cookie
                try:
                    cookies = context.cookies()

                    # 检查是否包含登录标记
                    if _has_login_cookie(cookies):
                        # 序列化 Cookie
                        cookie_str = _serialize_cookies(cookies)

                        # 如果 Cookie 有变化，进行验证
                        if cookie_str and cookie_str != last_cookie_check:
                            last_cookie_check = cookie_str

                            # 简单验证：检查是否包含必要字段
                            if len(cookie_str) > 100:  # Cookie 应该足够长
                                login_success = True
                                break
                except Exception as e:
                    pass  # 忽略 Cookie 读取错误

                time.sleep(2)  # 每 2 秒检查一次

            if login_success:
                _set_state("scanning", "登录成功，正在保存 Cookie...")

                # 保存 Cookie
                cookies = context.cookies()
                cookie_str = _serialize_cookies(cookies)

                settings = get_settings()
                settings["douyin_cookie"] = cookie_str
                save_settings(settings)

                _set_state("success", "登录完成！Cookie 已保存", cookie=cookie_str)

                # 等待 2 秒后关闭浏览器
                time.sleep(2)
            else:
                if _login_state["status"] not in ["error", "cancelled"]:
                    _set_state("expired", "登录超时，请重试")

            browser.close()
            _active_browser = None

    except Exception as e:
        _set_state("error", f"登录发生异常: {str(e)}")
        if _active_browser:
            try:
                _active_browser.close()
            except:
                pass
            _active_browser = None
