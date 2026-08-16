"""
微信读书多账号浏览器自动化保活与会话刷新模块 (Plan B)
为账号池中的每个账号提供独立的 Playwright Persistent Profile 隔离环境，
通过后台无头浏览器访问微信读书换取最新 Cookie (wr_skey 等)。
"""

import os
import shutil
import time
import logging
import threading
from pathlib import Path

from backend.config import app_dir, get_proxies_dict
from backend.runtime import launch_persistent_context

logger = logging.getLogger(__name__)

# 全局浏览器单实例串行锁（确保同一时刻系统只启动 1 个 Headless Chromium 实例，节省内存）
_browser_refresh_lock = threading.Lock()


def get_weread_profiles_root() -> Path:
    """获取微信读书所有账号 Profile 的根目录"""
    root = app_dir() / "data" / "weread_profiles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_weread_profile_dir(account_id: str) -> Path:
    """获取指定账号专有的独立 Profile 目录"""
    clean_id = "".join(c for c in str(account_id) if c.isalnum() or c in ("-", "_")) or "default"
    target = get_weread_profiles_root() / f"weread_{clean_id}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def clean_weread_profile(account_id: str):
    """账号被删除时，清理该账号对应的独立 Profile 物理目录"""
    try:
        clean_id = "".join(c for c in str(account_id) if c.isalnum() or c in ("-", "_")) or "default"
        target = get_weread_profiles_root() / f"weread_{clean_id}"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            logger.info("已清理微信读书账号 [%s] 的浏览器 Profile 目录: %s", account_id, target)
    except Exception as e:
        logger.warning("清理微信读书 Profile 异常: %s", e)


def dedupe_cookie(cookie: str) -> str:
    """对 Cookie 字符串去重键，保留首次出现的值"""
    kept = {}
    for item in (cookie or "").split(";"):
        item = item.strip()
        if "=" in item:
            k, _, v = item.partition("=")
            k = k.strip()
            if k and k not in kept:
                kept[k] = f"{k}={v.strip()}"
    return "; ".join(kept.values())


def extract_vid(cookie: str) -> str:
    """从 Cookie 字符串中提取 wr_vid"""
    for item in (cookie or "").split(";"):
        item = item.strip()
        if item.startswith("wr_vid="):
            return item[len("wr_vid="):].strip()
    return ""


def verify_weread_cookie(cookie: str, timeout: int = 12) -> bool:
    """
    通过真实接口请求验证 Cookie 是否真正有效且未过期。
    请求 https://weread.qq.com/web/mp/articles?bookId=MP_WXS_3528995129&offset=0
    """
    if not cookie or "wr_vid=" not in cookie:
        return False

    try:
        import requests
        proxies = get_proxies_dict()
        resp = requests.get(
            "https://weread.qq.com/web/mp/articles",
            params={"bookId": "MP_WXS_3528995129", "offset": 0},
            headers={
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://weread.qq.com/",
            },
            proxies=proxies,
            timeout=timeout,
        )
        try:
            j = resp.json()
        except Exception:
            return False

        if isinstance(j, dict):
            code = j.get("errCode", j.get("errcode", 0))
            if code:
                # 任何非零错误码（如 -2012 登录超时、-2041 等）均视为失效
                return False
            if "reviews" in j or "articles" in j or "synckey" in j or j.get("bookId"):
                return True
        return False
    except Exception as e:
        logger.debug("验证微信读书 Cookie 请求异常: %s", e)
        return False


def refresh_weread_account_browser(account_id: str, existing_cookie: str = "",
                                   headless: bool = True, timeout_s: int = 40) -> dict:
    """
    使用专有 Profile 启动 Playwright 无头浏览器加载微信读书页面，换取并刷新当天的最新 Cookie。
    严格单实例串行执行，避免并发占用系统资源。
    """
    with _browser_refresh_lock:
        profile_dir = get_weread_profile_dir(account_id)
        logger.info("开始执行微信读书账号 [%s] 的浏览器保活会话刷新 (Profile: %s)...", account_id, profile_dir.name)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {
                "ok": False,
                "needs_scan": False,
                "message": "未安装 Playwright 依赖，无法执行浏览器会话保活",
            }

        captured_cookie = ""
        url = "https://weread.qq.com/web/mp/reader/MP_WXS_3528995129"

        try:
            with sync_playwright() as p:
                launch_args = [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
                context = launch_persistent_context(
                    p.chromium,
                    profile_dir,
                    headless=headless,
                    args=launch_args,
                )

                try:
                    page = context.new_page()

                    # 注入已有 Cookie 种子（如果是首次初始化该 Profile）
                    clean_existing = dedupe_cookie(existing_cookie)
                    if clean_existing:
                        try:
                            cookie_objs = []
                            for item in clean_existing.split(";"):
                                item = item.strip()
                                if "=" in item:
                                    name, _, val = item.partition("=")
                                    name = name.strip()
                                    val = val.strip()
                                    if name and val:
                                        cookie_objs.append({
                                            "name": name,
                                            "value": val,
                                            "url": "https://weread.qq.com",
                                        })
                            if cookie_objs:
                                context.add_cookies(cookie_objs)
                        except Exception as ce:
                            logger.debug("注入种子 Cookie 异常 (可忽略): %s", ce)

                    # 监听请求头提取最新的 Cookie
                    captured = {}
                    def _on_request(request):
                        if "weread.qq.com" in request.url:
                            ck = request.headers.get("cookie", "")
                            if ck and "wr_vid=" in ck:
                                captured["cookie"] = ck

                    page.on("request", _on_request)

                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                        # 额外等待 2 秒让页面内部异步鉴权请求完成
                        page.wait_for_timeout(2000)
                    except Exception as e:
                        logger.debug("打开微信读书页面超时或异常 (尝试读取 Cookie): %s", e)

                    # 优先使用网络请求中携带的最新 Cookie
                    captured_cookie = captured.get("cookie", "").strip()
                    if not captured_cookie:
                        # 回退：从 context 的 cookie jar 拼接
                        try:
                            cookies = context.cookies("https://weread.qq.com")
                            captured_cookie = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                        except Exception:
                            captured_cookie = ""

                    captured_cookie = dedupe_cookie(captured_cookie)

                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

            # 验证提取出的 Cookie
            if captured_cookie and "wr_vid=" in captured_cookie:
                if verify_weread_cookie(captured_cookie):
                    vid = extract_vid(captured_cookie) or str(account_id)
                    logger.info("✅ 微信读书账号 [%s] 浏览器保活刷新成功 (vid=%s)!", account_id, vid)
                    return {
                        "ok": True,
                        "cookie": captured_cookie,
                        "vid": vid,
                        "message": "浏览器保活刷新成功，已换取最新有效会话",
                    }
                else:
                    logger.warning("⚠️ 微信读书账号 [%s] 提取到 Cookie 但验证未通过 (可能微信根授权已失效)", account_id)
                    return {
                        "ok": False,
                        "needs_scan": True,
                        "message": "微信授权已过期 (-2012)，浏览器无法自动续期，需重新扫码",
                    }

            # 未提取到任何带 wr_vid 的 Cookie
            return {
                "ok": False,
                "needs_scan": True,
                "message": "未获取到有效登录 Cookie，请重新扫码登录",
            }

        except Exception as e:
            logger.error("微信读书浏览器保活执行异常: %s", e)
            return {
                "ok": False,
                "needs_scan": False,
                "message": f"浏览器保活执行异常: {str(e)}",
            }
