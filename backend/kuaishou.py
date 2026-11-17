"""
快手资源下载模块 — 纯 API 调用版本
参考 KS-Downloader 项目思路，直接调用快手 live_api 接口

功能：
- 单条链接解析下载（视频/图集），通过 live_api/profile/feedbyid（需 photoId + principalId）
- 用户主页批量下载（live_api/profile/public，游标分页，需登录 Cookie）

说明：
- 单作品接口无需签名，但 feedbyid 必须同时提供 photoId 与 principalId(作者ID)。
  App 分享链接（fw/photo?userId=&photoId=）天然带作者ID；裸 short-video 链接需登录后解析。
- 用户主页作品列表走 www.kuaishou.com/rest/v/profile/feed（POST JSON），必须登录 Cookie；
  匿名访问一律被风控拦截(result=109)。响应顶层含 feeds(作品数组) 与 pcursor(下一页游标)。
  注意：旧的 live_api/profile/public 返回的是直播间信息，并不含作品列表。
"""

import re
import json
import time
import random
import threading
import urllib.parse
from pathlib import Path
from flask import Blueprint, jsonify, request

import requests as http_requests

from backend.config import (
    DATA_DIR, get_settings, get_proxies_dict, report_proxy_status,
    load_json, save_json,
)
from backend import nas_archive

kuaishou_bp = Blueprint("kuaishou", __name__, url_prefix="/api/kuaishou")

# 快手下载保存的主目录
KUAISHOU_DIR = DATA_DIR / "kuaishou_downloads"
HISTORY_FILE = DATA_DIR / "kuaishou_history.json"
ACCOUNTS_FILE = DATA_DIR / "kuaishou_accounts.json"

# ── 常量 ──────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)

# 快手 API 端点
# 单作品详情（live_api 无需签名，需 photoId + principalId）
API_DETAIL = "https://live.kuaishou.com/live_api/profile/feedbyid"
# 作者主页作品列表（POST JSON，需登录 Cookie）
API_USER_FEED = "https://www.kuaishou.com/rest/v/profile/feed"

# ── 全局任务状态管理 ──────────────────────────────────────

_task_state = {
    "status": "idle",          # idle / running / completed / failed / cancelled
    "total": 0,
    "current_index": 0,
    "current_title": "",
    "logs": [],
    "downloaded_count": 0,
    "failed_count": 0,
}
_task_lock = threading.Lock()
_task_cancel_event = threading.Event()


def _add_log(message: str):
    with _task_lock:
        timestamp = time.strftime("%H:%M:%S")
        _task_state["logs"].append(f"[{timestamp}] {message}")
        if len(_task_state["logs"]) > 300:
            _task_state["logs"] = _task_state["logs"][-300:]


def _set_task_state(status: str = None, total: int = None, current_index: int = None,
                    current_title: str = None, downloaded_count: int = None, failed_count: int = None):
    with _task_lock:
        if status is not None:
            _task_state["status"] = status
        if total is not None:
            _task_state["total"] = total
        if current_index is not None:
            _task_state["current_index"] = current_index
        if current_title is not None:
            _task_state["current_title"] = current_title
        if downloaded_count is not None:
            _task_state["downloaded_count"] = downloaded_count
        if failed_count is not None:
            _task_state["failed_count"] = failed_count


def _reset_task_state(total: int = 0):
    with _task_lock:
        _task_state["status"] = "running"
        _task_state["total"] = total
        _task_state["current_index"] = 0
        _task_state["current_title"] = ""
        _task_state["logs"] = []
        _task_state["downloaded_count"] = 0
        _task_state["failed_count"] = 0
    _add_log(f"任务启动，共计需要处理 {total} 项内容")


def ensure_kuaishou_dirs():
    """确保快手下载目录存在"""
    KUAISHOU_DIR.mkdir(parents=True, exist_ok=True)


# ── 工具函数 ──────────────────────────────────────────────

def clean_filename(filename: str) -> str:
    """清理文件名，移除不支持的字符"""
    filename = re.sub(r'[\\/:*?"<>|\n\r\t]', "", filename or "")
    filename = filename.strip().replace(" ", "_")
    return filename[:80] if filename else "untitled"


def add_history_item(title: str, item_type: str, file_path, size_bytes: int, author: str = ""):
    """保存下载记录到历史记录文件"""
    history = load_json(HISTORY_FILE, [])
    # 尝试从路径推导博主名
    if not author:
        try:
            p = Path(file_path)
            # user_dir 是 target_dir / nickname
            if p.parent.parent == KUAISHOU_DIR:
                author = p.parent.name
            elif p.parent == KUAISHOU_DIR:
                author = "未知博主"
        except Exception:
            author = "快手用户"
    history.insert(0, {
        "title": title,
        "type": item_type,
        "path": str(file_path),
        "author": author or "快手用户",
        "size": f"{size_bytes / (1024 * 1024):.2f} MB" if size_bytes else "未知",
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    save_json(HISTORY_FILE, history[:200])


def _notify_login_expired(expired: bool):
    """通知登录模块 Cookie 是否已失效（延迟导入避免循环依赖）"""
    try:
        from backend import kuaishou_auth
        kuaishou_auth.set_login_expired(expired)
    except Exception:
        pass


# ── 快手 API 客户端 ──────────────────────────────────────

class KuaishouClient:
    """快手 API 客户端 — 直接 HTTP 调用 live_api"""

    def __init__(self):
        self.session = http_requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

        # 加载用户设置中的 Cookie（登录后用于拉取用户主页列表）
        settings = get_settings()
        cookie = settings.get("kuaishou_cookie", "").strip()
        if cookie:
            self._load_cookie(cookie)

        proxies = get_proxies_dict()
        if proxies:
            self.session.proxies.update(proxies)

        self._primed = set()

    def _load_cookie(self, cookie_str: str):
        """将 Cookie 字符串写入 session 的 cookie jar（域 .kuaishou.com）"""
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    self.session.cookies.set(k.strip(), v.strip(), domain=".kuaishou.com")
                except Exception:
                    pass

    def _prime(self, url: str):
        """访问一次目标域名页面以获取 did 等基础 Cookie（每个 URL 仅一次）"""
        if url in self._primed:
            return
        try:
            self.session.get(url, timeout=10)
        except Exception:
            pass
        self._primed.add(url)

    # ── 链接解析 ──────────────────────────────────────────

    def resolve_share_url(self, url: str) -> str:
        """解析快手分享链接（短链跳转到最终 URL）"""
        m = re.search(r"https?://[^\s<>\"'，。；！？、【】《》]+", url.strip())
        if not m:
            return url.strip()
        target = m.group(0).rstrip("，。！？、,.!?;")
        # 仅对短链做重定向跟随 (v.kuaishou.com, kuaishou.com/f/, kuaishou.cn/f/, v.kuaishouapp.com 等)
        if re.search(r"(v\.kuaishou\.|kuaishou\.\w+/f/|v\.kuaishouapp\.)", target):
            try:
                resp = self.session.get(target, allow_redirects=True, timeout=10)
                return resp.url
            except Exception:
                return target
        return target

    @staticmethod
    def extract_ids(url: str):
        """从链接或文案中提取 (photo_id, user_id)。user_id 可能为空。"""
        url = url.strip()
        photo_id = ""
        user_id = ""

        # 提取 URL（若包含文案）
        m_url = re.search(r"https?://[^\s<>\"'，。；！？、【】《》]+", url)
        target_url = m_url.group(0).rstrip("，。！？、,.!?;") if m_url else url

        parsed = urllib.parse.urlparse(target_url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "photoId" in qs:
            photo_id = qs["photoId"][0]
        # 作者ID 在不同链接中字段名不同：userId(App分享) / authorId(网页) / principalId
        for key in ("userId", "authorId", "principalId"):
            if qs.get(key):
                user_id = qs[key][0]
                break

        # 匹配 live.kuaishou.com/u/作者ID/作品ID (电脑网页端常见分享与播放格式)
        m_live = re.search(r"live\.kuaishou\.com/u/([^/?#\s]+)/([^/?#\s]+)", target_url)
        if m_live:
            if not user_id:
                user_id = m_live.group(1)
            if not photo_id:
                photo_id = m_live.group(2)

        # 匹配常见路径：/short-video/<id>, /fw/photo/<id>, /photo/<id>
        if not photo_id:
            m = re.search(r"/(?:short-video|fw/photo|photo)/([A-Za-z0-9_-]+)", target_url)
            if m:
                photo_id = m.group(1)

        # 纯 ID 兜底
        if not photo_id and re.match(r"^[A-Za-z0-9_-]+$", url):
            photo_id = url

        return photo_id, user_id

    @staticmethod
    def extract_user_id(url: str) -> str:
        """从用户主页链接中提取 user_id（作者ID）"""
        url = url.strip()
        m_url = re.search(r"https?://[^\s<>\"'，。；！？、【】《》]+", url)
        target_url = m_url.group(0).rstrip("，。！？、,.!?;") if m_url else url

        # 兼容 live.kuaishou.com/u/<user_id>/<photo_id> 单作品路径，优先提取其 user_id
        m_live_single = re.search(r"live\.kuaishou\.com/u/([^/?#\s]+)/([^/?#\s]+)", target_url)
        if m_live_single:
            return m_live_single.group(1)

        # 兼容多种主页路径：/profile/<id>、/fw/user/<id>(c.kuaishou.com 分享跳转)、/u/<id>
        m = re.search(r"/(?:profile|fw/user|u)/([A-Za-z0-9_-]+)", target_url)
        if m:
            return m.group(1)
        m = re.search(r"[?&](?:userId|authorId|principalId)=([A-Za-z0-9_-]+)", target_url)
        if m:
            return m.group(1)
        if re.match(r"^[A-Za-z0-9_-]+$", url):
            return url
        return ""

    # ── 数据查询接口 ──────────────────────────────────────

    def get_detail_from_html(self, photo_id: str, url: str = "") -> dict:
        """参考 KS-Downloader 思路，从网页 HTML 中提取作品详情。

        支持两种模式：
        1. PC 网页端 (https://www.kuaishou.com/short-video/{photo_id})：
           从 window.__APOLLO_STATE__ 中提取 VisionVideoDetailPhoto 与 VisionVideoDetailAuthor，
           内含最高清晰度 AVC/HEVC 码率视频直链或图集 atlas，且无需提前知晓作者 ID。
        2. 移动端分享页 (v.m.chenzhongtech.com / fw/photo)：
           从 window.INIT_STATE 中通过正则提取 photo 数据对象。
        """
        html = ""
        is_mobile = bool(url and ("chenzhongtech" in url or "fw/photo" in url))

        # 1. 移动端分享链接
        if is_mobile and url:
            try:
                m_headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
                    ),
                    "Referer": "https://v.m.chenzhongtech.com/",
                }
                resp = self.session.get(url, headers=m_headers, timeout=12)
                if resp.status_code == 200:
                    html = resp.text
            except Exception:
                pass

            if html and "window.INIT_STATE" in html:
                m_photo = re.search(r'"photo":(\{[^;]+?"serialInfo"|\{.*?"photoUrls".*?\})', html)
                if not m_photo:
                    m_photo = re.search(r'"photo":(\{.*?\})(?:,"|\})', html)
                if m_photo:
                    try:
                        import json
                        photo_data = json.loads(m_photo.group(1))
                        return {"photo": photo_data, "id": photo_id}
                    except Exception:
                        pass

        # 2. PC 网页端 __APOLLO_STATE__ 解析
        target_url = f"https://www.kuaishou.com/short-video/{photo_id}"
        headers = {
            "Referer": "https://www.kuaishou.com/new-reco",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        try:
            resp = self.session.get(target_url, headers=headers, timeout=12)
            if resp.status_code == 200:
                html = resp.text
        except Exception:
            return {}

        if not html or "window.__APOLLO_STATE__=" not in html:
            return {}

        try:
            import json
            idx = html.find("window.__APOLLO_STATE__=")
            sub = html[idx + len("window.__APOLLO_STATE__="):]
            end = sub.find(";(function()")
            if end != -1:
                sub = sub[:end]
            apollo_data = json.loads(sub)
            client_data = apollo_data.get("defaultClient", {})

            # 寻找作品数据对象
            photo = client_data.get(f"VisionVideoDetailPhoto:{photo_id}")
            if not photo:
                for k, v in client_data.items():
                    if k.startswith("VisionVideoDetailPhoto:") and isinstance(v, dict):
                        photo = v
                        break
            if not photo:
                return {}

            # 寻找作者数据对象
            author = None
            for k, v in client_data.items():
                if k.startswith("VisionVideoDetailAuthor:") and isinstance(v, dict):
                    author = v
                    break

            work = dict(photo)
            work["id"] = photo_id
            if author:
                work["author"] = author
                work["userName"] = author.get("name") or work.get("userName")
                work["userEid"] = author.get("id") or work.get("userEid")

            return work
        except Exception:
            return {}

    def get_detail(self, photo_id: str, user_id: str) -> dict:
        """获取单条作品详情（feedbyid 需 photoId + principalId）"""
        self._prime(f"https://www.kuaishou.com/short-video/{photo_id}")
        headers = {"Referer": f"https://www.kuaishou.com/short-video/{photo_id}"}
        resp = self.session.get(
            API_DETAIL,
            params={"photoId": photo_id, "principalId": user_id},
            headers=headers, timeout=15,
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        work = data.get("currentWork")
        if not work:
            result = data.get("result")
            if result in (21, 11):
                hint = "链接缺少作者ID（裸 short-video 链接无法匿名解析），请改用 App 分享链接或先扫码登录。"
            elif result == 3:
                hint = "作品不存在/私密，或触发了访问频率限制(风控)，请稍后重试或扫码登录以提升稳定性。"
            else:
                hint = "可能需要有效的登录 Cookie，请尝试扫码登录。"
            raise Exception(f"获取作品详情失败 (result={result})。{hint}")
        return work

    def get_user_feed_via_browser(self, user_id: str, pcursor: str = ""):
        """通过后台 Chromium 无头浏览器获取用户作品列表，突破前端 JS 签名校验 (result=50)。"""
        try:
            from playwright.sync_api import sync_playwright
            from backend.runtime import launch_chromium
        except ImportError:
            raise Exception("未安装 Playwright 依赖，无法启用浏览器抓取模式")

        settings = get_settings()
        cookie_str = settings.get("kuaishou_cookie", "").strip()

        cookie_objs = []
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                k = k.strip()
                v = v.strip()
                if k and v:
                    cookie_objs.append({
                        "name": k,
                        "value": v,
                        "domain": ".kuaishou.com",
                        "path": "/",
                    })

        target_url = f"https://www.kuaishou.com/profile/{user_id}"
        captured_data = []

        with sync_playwright() as p:
            browser = launch_chromium(
                p.chromium,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1280, "height": 800}
                )
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                if cookie_objs:
                    context.add_cookies(cookie_objs)
                page = context.new_page()

                def on_res(resp):
                    if "/rest/v/profile/feed" in resp.url and resp.status == 200:
                        try:
                            data = resp.json()
                            if isinstance(data, dict):
                                captured_data.append(data)
                        except Exception:
                            pass

                page.on("response", on_res)

                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass

                # 等待首屏作品响应捕获（最多等待 15 秒）
                # 如果传入了 pcursor 且首屏已捕获，向下滚动以触发下一页请求
                target_page = 1
                if pcursor:
                    try:
                        target_page = int(pcursor)
                    except ValueError:
                        target_page = 2

                for _ in range(30):
                    if captured_data:
                        break
                    page.wait_for_timeout(500)

                if target_page > 1 and captured_data:
                    for _ in range(target_page - 1):
                        curr_count = len(captured_data)
                        for _ in range(8):
                            try:
                                page.evaluate("window.scrollBy(0, 1500)")
                            except Exception:
                                pass
                            page.wait_for_timeout(800)
                            if len(captured_data) > curr_count:
                                break

            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        if not captured_data:
            raise Exception("未能通过浏览器获取到作品列表数据，请确认网络畅通且已在左侧扫码登录快手。")

        # 取最后一个捕获的数据
        latest_data = captured_data[-1]
        result = latest_data.get("result")
        if result == 109 or latest_data.get("loginUrl"):
            _notify_login_expired(True)
            if not pcursor:
                raise Exception("获取快手用户作品列表需要快手登录凭证（快手官方对主页作品列表开启了鉴权与风控限制）。请在左侧点击「扫码登录」登录快手账号后重试。")
            else:
                raise Exception("加载更多作品需要快手登录凭证，请在左侧点击「扫码登录」登录快手后重试。")

        if "feeds" not in latest_data and result not in (1, None):
            raise Exception(f"获取用户作品列表失败 (result={result})。请确认已扫码登录，且该主页作品公开。")

        _notify_login_expired(False)
        items = latest_data.get("feeds") or []
        api_cursor = latest_data.get("pcursor") or ""
        if not items or api_cursor in ("no_more", "NO_MORE"):
            next_pcursor = ""
        else:
            next_pcursor = str(target_page + 1)
        return items, next_pcursor

    def get_user_feed(self, user_id: str, pcursor: str = ""):
        """获取用户公开作品列表，返回 (list, next_pcursor)

        接口为 www.kuaishou.com/rest/v/profile/feed（POST JSON），需登录 Cookie。
        响应顶层含 feeds(作品数组) 与 pcursor(下一页游标)。
        若触发快手 sig4 动态签名风控 (result=50)，自动通过无头浏览器模式安全获取。
        """
        # 前置检查 Cookie 是否存在
        settings = get_settings()
        cookie_str = settings.get("kuaishou_cookie", "").strip()
        if not cookie_str:
            _notify_login_expired(True)
            if not pcursor:
                raise Exception("未登录快手。获取快手用户作品列表需要快手登录凭证（快手官方对主页作品列表开启了鉴权与风控限制），请先在左侧点击「扫码登录」登录快手后重试。")
            else:
                raise Exception("未登录快手。获取更多作品需要快手登录凭证，请先在左侧「扫码登录」登录快手。")

        try:
            self._prime(f"https://www.kuaishou.com/profile/{user_id}")
            headers = {
                "Content-Type": "application/json",
                "Origin": "https://www.kuaishou.com",
                "Referer": f"https://www.kuaishou.com/profile/{user_id}",
            }
            payload = {"user_id": user_id, "pcursor": pcursor, "page": "profile"}
            resp = self.session.post(API_USER_FEED, json=payload, headers=headers, timeout=10)
            data = resp.json() or {}
            result = data.get("result")

            if result == 1 and "feeds" in data:
                _notify_login_expired(False)
                return data.get("feeds") or [], data.get("pcursor") or ""

            if result == 109 or data.get("loginUrl"):
                _notify_login_expired(True)
                raise Exception("快手登录已失效，请先在左侧「扫码登录」重新登录快手后重试。")

            # 遇到 result=50 (签名验证失败) 或风控拦截，切换到无头浏览器抓取
            if result == 50:
                _add_log("API 命中快手前端动态签名风控 (result=50)，正在切换为浏览器内核安全抓取...")
                return self.get_user_feed_via_browser(user_id, pcursor)

            if "feeds" not in data and result not in (1, None):
                raise Exception(
                    f"获取用户作品列表失败 (result={result})。请确认已扫码登录，且该主页作品公开。"
                )
            return data.get("feeds") or [], data.get("pcursor") or ""

        except Exception as e:
            err_msg = str(e)
            if "登录已失效" in err_msg or "未登录" in err_msg or "扫码登录" in err_msg:
                raise
            if "result=50" in err_msg or "未能通过浏览器" not in err_msg:
                try:
                    return self.get_user_feed_via_browser(user_id, pcursor)
                except Exception as b_err:
                    raise b_err
            raise

    def get_user_feed_from_mobile(self, url_or_text: str):
        """从移动端分享页 HTML (window.INIT_STATE) 免登录提取用户主页作品与作者信息。

        快手 App 分享的主页链接 (v.kuaishou.com / c.kuaishou.com/fw/user/...)
        在移动端访问时，服务端直接在 HTML window.INIT_STATE 中下发了作者资料及首屏作品 feeds，
        无需登录 Cookie 且完全不受 PC 端动态签名 (result=50) 或鉴权 (result=109) 限制。
        返回: (feeds, author_dict, next_pcursor)
        """
        m = re.search(r"https?://[^\s<>\"'，。；！？、【】《》]+", (url_or_text or "").strip())
        if not m:
            return [], {}, ""
        url = m.group(0).rstrip("，。！？、,.!?;")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            resp = self.session.get(url, headers=headers, allow_redirects=True, timeout=12)
            html = resp.text
            idx = html.find("window.INIT_STATE")
            if idx == -1:
                return [], {}, ""

            sub = html[idx:]
            sub = sub[sub.find("=") + 1:].strip()
            obj, _ = json.JSONDecoder().raw_decode(sub)
            feeds = []
            author_candidates = []
            next_pcursor = ""

            for v in obj.values():
                if isinstance(v, dict):
                    if "feeds" in v and not feeds:
                        feeds = v["feeds"]
                        next_pcursor = v.get("pcursor") or ""
                    if "userProfile" in v:
                        prof = v["userProfile"].get("profile", {})
                        if prof.get("user_name"):
                            author_candidates.append({
                                "name": prof.get("user_name", ""),
                                "nickname": prof.get("user_name", ""),
                                "avatar": prof.get("headurl") or (prof.get("headurls") and prof["headurls"][0]["url"]) or "",
                                "eid": prof.get("eid") or prof.get("kwaiId") or "",
                                "kwaiId": prof.get("kwaiId") or "",
                                "user_id": str(prof.get("user_id") or prof.get("eid") or prof.get("kwaiId") or ""),
                                "desc": prof.get("user_text") or "",
                                "fans": (v["userProfile"].get("ownerCount") or {}).get("fan", 0),
                            })

            author = {}
            if feeds and author_candidates:
                first_user = feeds[0].get("userName")
                for cand in author_candidates:
                    if cand["name"] == first_user:
                        author = dict(cand)
                        break
            if not author and author_candidates:
                best = max(author_candidates, key=lambda c: c["fans"])
                author = dict(best)

            return feeds, author, next_pcursor
        except Exception as e:
            _add_log(f"移动端首屏作品抓取异常: {e}")
            return [], {}, ""

    # ── 资源解析 ──────────────────────────────────────────

    @staticmethod
    def parse_media_info(work: dict) -> dict:
        """从作品 JSON 中解析下载信息。

        兼容两种结构：
        - feedbyid 的 currentWork（字段多在顶层，视频直链在 playUrl/mp4Url）
        - rest/v/profile/feed 的列表项（核心字段在嵌套 photo 内；视频直链在 photo.photoUrls[{cdn,url}]，
          兜底为 photo.manifest.adaptationSet[].representation[].url；图集在 photo.ext_params.atlas）
        """
        def _pick_url(seq):
            for it in (seq or []):
                if isinstance(it, str) and it:
                    return it
                if isinstance(it, dict) and it.get("url"):
                    return it["url"]
            return ""

        photo = work.get("photo") if isinstance(work.get("photo"), dict) else {}
        author = work.get("author") or {}

        # 作品 ID：顶层 id/photoId → photo.id → photo.share_info 内的 photoId
        photo_id = str(work.get("id") or work.get("photoId") or photo.get("id") or "")
        if not photo_id and photo.get("share_info"):
            qs = urllib.parse.parse_qs(photo["share_info"])
            photo_id = (qs.get("photoId") or [""])[0]

        nickname = (
            photo.get("userName") or author.get("name") or work.get("userName")
            or photo.get("userEid") or work.get("userEid") or "快手用户"
        )
        caption = (work.get("caption") or photo.get("caption") or work.get("title") or "").strip()
        title = clean_filename(caption or nickname or photo_id)

        # 视频直链候选：
        # feedbyid → playUrl/mp4Url/photoUrl；rest feed → photo.photoUrls[{cdn,url}]，兜底 DASH manifest
        video_url = (
            work.get("mp4Url") or work.get("playUrl") or work.get("photoUrl")
            or photo.get("photoUrl")
            or _pick_url(photo.get("photoUrls"))
            or _pick_url(work.get("mainMvUrls") or work.get("main_mv_urls"))
        )
        if not video_url:
            try:
                manifest = work.get("manifest") or photo.get("manifest") or {}
                reps = manifest["adaptationSet"][0]["representation"]
                video_url = _pick_url(reps)
            except (KeyError, IndexError, TypeError):
                pass

        # 图集直链候选
        norm_imgs = []
        for it in (work.get("imgUrls") or photo.get("imgUrls") or []):
            if isinstance(it, str):
                norm_imgs.append(it)
            elif isinstance(it, dict):
                u = it.get("url") or it.get("cdnUrl")
                if u:
                    norm_imgs.append(u)
        # 图集 atlas：feedbyid 在 ext_params.atlas；rest feed 在 photo.ext_params.atlas / photo.atlas
        if not norm_imgs:
            atlas = (
                work.get("atlas") or photo.get("atlas")
                or (work.get("ext_params") or {}).get("atlas")
                or (photo.get("ext_params") or {}).get("atlas") or {}
            )
            cdn = atlas.get("cdn")
            lst = atlas.get("list") or []
            if cdn and lst:
                cdn0 = cdn[0] if isinstance(cdn, list) else cdn
                norm_imgs = [f"https://{cdn0}{p}" for p in lst]

        # 封面（网格缩略图用）
        cover = (
            photo.get("coverUrl") or work.get("coverUrl")
            or _pick_url(photo.get("coverUrls")) or _pick_url(work.get("coverUrls"))
        )

        work_type = str(
            work.get("workType") or work.get("photoType") or photo.get("photoType") or ""
        ).upper()
        is_atlas = "ATLAS" in work_type or (norm_imgs and not video_url)

        if is_atlas and norm_imgs:
            item_type, urls = "image", norm_imgs
        elif video_url:
            item_type, urls = "video", [video_url]
        elif norm_imgs:
            item_type, urls = "image", norm_imgs
        else:
            item_type, urls = "video", []

        avatar = photo.get("headUrl") or author.get("headerUrl") or work.get("headUrl") or ""

        return {
            "type": item_type,
            "title": title,
            "urls": urls,
            "photo_id": photo_id,
            "nickname": clean_filename(nickname),
            "author": clean_filename(nickname),
            "avatar": avatar,
            "cover": cover,
            "count": len(urls),
        }


# ── 下载执行 ──────────────────────────────────────────────

def download_file(url: str, save_path: Path) -> int:
    """下载文件到指定路径，返回文件大小(bytes)，支持流式读取中断自动重试与断点续传"""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.kuaishou.com/",
    }
    proxies = get_proxies_dict()
    proxy_url = proxies.get("http") if proxies else None
    temp_path = save_path.with_suffix(save_path.suffix + ".tmp")
    save_path.parent.mkdir(parents=True, exist_ok=True)

    max_retries = 3
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            req_headers = headers.copy()
            start_byte = 0
            if temp_path.exists() and temp_path.stat().st_size > 0:
                start_byte = temp_path.stat().st_size
                req_headers["Range"] = f"bytes={start_byte}-"

            r = http_requests.get(url, headers=req_headers, proxies=proxies, stream=True, timeout=30)
            if r.status_code == 206:
                open_mode = "ab"
            elif r.status_code == 200:
                open_mode = "wb"
                start_byte = 0
            else:
                if temp_path.exists() and start_byte > 0:
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                raise Exception(f"HTTP {r.status_code}")

            with open(temp_path, open_mode) as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

            if temp_path.exists() and temp_path.stat().st_size > 0:
                if save_path.exists():
                    try:
                        save_path.unlink()
                    except Exception:
                        pass
                temp_path.rename(save_path)
                report_proxy_status(proxy_url, success=True)
                return save_path.stat().st_size

        except Exception as e:
            last_error = e
            err_msg = str(e)
            if "unexpected EOF" in err_msg or "IncompleteRead" in err_msg or "ChunkedEncodingError" in err_msg or "Connection" in err_msg or "timeout" in err_msg:
                time.sleep(1.0)
                continue
            else:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                report_proxy_status(proxy_url, success=False)
                time.sleep(1.0)

    if temp_path.exists():
        try:
            temp_path.unlink()
        except Exception:
            pass
    report_proxy_status(proxy_url, success=False)
    raise last_error or Exception("下载流读取失败: unexpected EOF")


def download_media(media_info: dict, target_dir: Path) -> dict:
    """根据解析的媒体信息执行下载"""
    photo_id = media_info["photo_id"]
    title = media_info["title"]
    item_type = media_info["type"]
    urls = media_info["urls"]
    nickname = media_info.get("nickname", "快手用户")

    if not urls:
        raise Exception("无法获取资源下载链接")

    title_with_id = f"{photo_id}_{title}"
    total_bytes = 0

    user_dir = target_dir / nickname
    user_dir.mkdir(parents=True, exist_ok=True)

    if item_type == "video":
        save_file = user_dir / f"{title_with_id}.mp4"
        _add_log(f"🎬 正在下载无水印视频: {save_file.name}")
        total_bytes = download_file(urls[0], save_file)
        _add_log(f"✅ 视频下载完成! 大小: {total_bytes / (1024 * 1024):.2f} MB")
        add_history_item(title, "视频", save_file, total_bytes, author=nickname)
    else:
        folder_path = user_dir / title_with_id
        folder_path.mkdir(parents=True, exist_ok=True)
        _add_log(f"📸 正在下载图集 (共 {len(urls)} 张) 到目录: {folder_path.name}")
        for idx, img_url in enumerate(urls, 1):
            save_file = folder_path / f"{idx}.jpg"
            total_bytes += download_file(img_url, save_file)
        _add_log(f"✅ 图集全部下载完成! 总大小: {total_bytes / (1024 * 1024):.2f} MB")
        add_history_item(title, "图文", folder_path, total_bytes, author=nickname)

    return {
        "id": photo_id,
        "title": title,
        "type": item_type,
        "size_bytes": total_bytes,
        "path": str(save_file) if item_type == "video" else str(folder_path),
    }


# ── 后台批处理执行器 ──────────────────────────────────────

def _download_media_items(media_infos: list, target_dir: Path):
    """后台下载一批已解析的 media_info；复用全局进度/取消状态。"""
    total = len(media_infos)
    _reset_task_state(total=total)

    downloaded = 0
    failed = 0
    for idx, media_info in enumerate(media_infos, 1):
        if _task_cancel_event.is_set():
            _add_log("⚠️ 用户取消了任务")
            _set_task_state(status="cancelled")
            return

        _set_task_state(current_index=idx)

        try:
            if not media_info.get("urls") and media_info.get("photo_id"):
                _add_log(f"[{idx}/{total}] 正在解析作品下载直链 (ID: {media_info['photo_id']})...")
                client = KuaishouClient()
                detail = client.get_detail_from_html(media_info["photo_id"])
                if detail:
                    parsed = client.parse_media_info(detail)
                    for k in ("urls", "type", "title"):
                        if parsed.get(k):
                            media_info[k] = parsed[k]

            if not media_info.get("urls"):
                _add_log(f"⚠️ 第 {idx} 项无可用资源链接，跳过")
                failed += 1
                _set_task_state(failed_count=failed)
                continue

            _add_log(f"[{idx}/{total}] 正在下载: {media_info['title'][:30]}...")
            result = download_media(media_info, target_dir)
            downloaded += 1
            _set_task_state(downloaded_count=downloaded, current_title=result["title"])
            # 登记 NAS 自动归档（后台队列，失败只记日志）
            if result.get("path"):
                nas_archive.submit_path("kuaishou", str(result["id"]), result["path"])
        except Exception as e:
            failed += 1
            _set_task_state(failed_count=failed)
            _add_log(f"❌ 第 {idx} 项下载失败: {str(e)}")

        if idx < total:
            time.sleep(random.uniform(1.0, 3.0))

    _add_log(f"🎉 下载任务结束! 成功: {downloaded}，失败: {failed}")
    _set_task_state(status="completed")


def _run_selected_download_task(media_infos: list, target_dir: Path):
    """后台线程：下载前端传回的选中作品列表"""
    try:
        _download_media_items(media_infos, target_dir)
    except Exception as outer_err:
        _add_log(f"💥 下载任务发生错误: {str(outer_err)}")
        _set_task_state(status="failed")


def _run_profile_download_task(user_id: str, max_pages: int, target_dir: Path, profile_url: str = ""):
    """后台线程：获取用户所有公开作品并下载"""
    _add_log(f"正在准备抓取主页资源，目标: {user_id}...")

    client = KuaishouClient()

    try:
        _add_log("正在获取用户作品列表...")
        all_items = []
        pcursor = ""
        page = 0
        # max_pages=0 表示不限制
        page_limit = max_pages if max_pages > 0 else 9999

        # 首屏如果提供了主页原始链接，先尝试从移动端免登录极速抓取
        if profile_url:
            m_feeds, m_author, m_pcursor = client.get_user_feed_from_mobile(profile_url)
            if m_feeds:
                page += 1
                all_items.extend(m_feeds)
                _add_log(f"第 1 页通过移动端免登录获取到 {len(m_feeds)} 个作品")
                pcursor = m_pcursor

        while page < page_limit and pcursor not in ("no_more", "NO_MORE"):
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            page += 1
            _add_log(f"正在获取第 {page} 页作品数据 (pcursor={pcursor or '首页'})...")

            try:
                items, next_pcursor = client.get_user_feed(user_id, pcursor)
            except Exception as e:
                _add_log(f"⚠️ 获取第 {page} 页数据失败: {str(e)}")
                break

            if not items:
                _add_log(f"第 {page} 页返回空数据，已到达最后一页")
                break

            all_items.extend(items)
            _add_log(f"第 {page} 页获取到 {len(items)} 个作品，累计 {len(all_items)} 个")

            # pcursor 为 "no_more" 或空表示结束
            if not next_pcursor or next_pcursor in ("no_more", "NO_MORE"):
                _add_log("已获取全部作品数据")
                break

            pcursor = next_pcursor
            delay = random.uniform(1.5, 4.0)
            _add_log(f"休眠 {delay:.1f} 秒规避频率风控...")
            time.sleep(delay)

        if not all_items:
            _set_task_state(status="failed")
            _add_log("❌ 未能获取到任何作品数据，请确认链接正确且主页公开")
            return

        _add_log(f"🚀 作品列表获取完成！共 {len(all_items)} 个作品待处理")
        media_infos = [KuaishouClient.parse_media_info(it) for it in all_items]
        _download_media_items(media_infos, target_dir)

    except Exception as outer_err:
        _add_log(f"💥 批量下载任务发生错误: {str(outer_err)}")
        _set_task_state(status="failed")


# ── Blueprint API 路由 ────────────────────────────────────

@kuaishou_bp.route("/parse-single", methods=["POST"])
def parse_single():
    """解析单条快手作品（视频/图集），只返回元数据，不执行下载"""
    data = request.get_json() or {}
    raw_url = data.get("url", "").strip()

    if not raw_url:
        return jsonify({"error": "请输入有效的链接"}), 400

    try:
        client = KuaishouClient()

        final_url = client.resolve_share_url(raw_url)
        _add_log(f"链接解析完成: {final_url}")

        photo_id, user_id = KuaishouClient.extract_ids(final_url)
        if not photo_id:
            photo_id, user_id = KuaishouClient.extract_ids(raw_url)

        if not photo_id:
            return jsonify({"error": "无法从链接中提取作品 ID"}), 400
        _add_log(f"提取到作品 ID: {photo_id}，作者ID: {user_id or '(未提供)'}")

        work = client.get_detail_from_html(photo_id, final_url)
        if not work:
            work = client.get_detail(photo_id, user_id)

        media_info = KuaishouClient.parse_media_info(work)
        if not media_info["urls"] and user_id:
            try:
                fallback_work = client.get_detail(photo_id, user_id)
                media_info = KuaishouClient.parse_media_info(fallback_work)
            except Exception:
                pass

        if not media_info["urls"]:
            return jsonify({"error": "未能获取作品直链，作品可能不存在或已设为私密"}), 400

        media_info["raw_url"] = final_url
        return jsonify({"status": "success", "item": media_info})

    except Exception as e:
        return jsonify({"error": f"解析失败: {str(e)}"}), 500


@kuaishou_bp.route("/download-single", methods=["POST"])
def download_single():
    """下载单条快手作品（视频/图集），支持传入已解析的 item 或原始 url"""
    data = request.get_json() or {}
    item = data.get("item")
    raw_url = data.get("url", "").strip()

    if not item and not raw_url:
        return jsonify({"error": "请输入有效的作品信息或链接"}), 400

    ensure_kuaishou_dirs()

    try:
        if item and isinstance(item, dict) and item.get("urls"):
            media_info = item
        else:
            client = KuaishouClient()

            final_url = client.resolve_share_url(raw_url)
            _add_log(f"链接解析完成: {final_url}")

            photo_id, user_id = KuaishouClient.extract_ids(final_url)
            if not photo_id:
                photo_id, user_id = KuaishouClient.extract_ids(raw_url)

            if not photo_id:
                return jsonify({"error": "无法从链接中提取作品 ID"}), 400
            _add_log(f"提取到作品 ID: {photo_id}，作者ID: {user_id or '(未提供)'}")

            work = client.get_detail_from_html(photo_id, final_url)
            if not work:
                work = client.get_detail(photo_id, user_id)
            _add_log("成功获取作品详情")

            media_info = KuaishouClient.parse_media_info(work)
            if not media_info["urls"] and user_id:
                try:
                    fallback_work = client.get_detail(photo_id, user_id)
                    media_info = KuaishouClient.parse_media_info(fallback_work)
                except Exception:
                    pass

            if not media_info["urls"]:
                return jsonify({"error": "无法获取资源下载链接"}), 400

        result = download_media(media_info, KUAISHOU_DIR)
        return jsonify({"message": "下载成功", "data": result, "title": result["title"]})

    except Exception as e:
        return jsonify({"error": f"下载失败: {str(e)}"}), 500


@kuaishou_bp.route("/download-profile", methods=["POST"])
def download_profile():
    """解析并批量下载快手用户主页"""
    if _task_state["status"] == "running":
        return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400

    data = request.get_json() or {}
    profile_url = data.get("url", "").strip()
    max_pages = int(data.get("max_pages", 5))

    if not profile_url:
        return jsonify({"error": "请输入有效的主页链接"}), 400

    client = KuaishouClient()
    resolved = client.resolve_share_url(profile_url)
    user_id = KuaishouClient.extract_user_id(resolved)
    if not user_id:
        user_id = KuaishouClient.extract_user_id(profile_url)
    if not user_id:
        user_id = "user"

    ensure_kuaishou_dirs()

    _set_task_state(status="running")
    _task_cancel_event.clear()
    thread = threading.Thread(
        target=_run_profile_download_task,
        args=(user_id, max_pages, KUAISHOU_DIR, profile_url),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": "已在后台启动主页批量解析下载任务", "user_id": user_id})


@kuaishou_bp.route("/user-feed", methods=["POST"])
def user_feed():
    """获取用户主页作品列表（分页，不下载）"""
    data = request.get_json() or {}
    profile_url = data.get("url", "").strip()
    pcursor = data.get("pcursor", "") or ""

    if not profile_url:
        return jsonify({"error": "请输入有效的主页链接"}), 400

    try:
        client = KuaishouClient()
        author = {}
        items = []
        next_pcursor = ""

        # 1. 首页/首屏且传入了链接：优先尝试移动端分享页 HTML (window.INIT_STATE) 免登录极速抓取
        if not pcursor:
            mobile_feeds, mobile_author, mobile_pcursor = client.get_user_feed_from_mobile(profile_url)
            if mobile_feeds:
                raw_items = mobile_feeds
                items = [KuaishouClient.parse_media_info(it) for it in raw_items]
                author = mobile_author or {}
                user_id = KuaishouClient.extract_user_id(profile_url) or (author.get("name") if author else "")
                return jsonify({
                    "user_id": user_id,
                    "author": author,
                    "items": items,
                    "pcursor": "",
                    "has_more": False,
                    "notice": "快手网页端对历史分页有严格的反爬风控限制，当前已为您免登录获取该作者最新公开作品。如需特定历史作品，可直接在「解析链接」中粘贴单个作品链接进行下载。",
                })

        # 2. 如果不是移动端首屏，或移动端未提取到数据，使用 PC Web API / 浏览器内核抓取
        resolved = client.resolve_share_url(profile_url)
        user_id = KuaishouClient.extract_user_id(resolved) or KuaishouClient.extract_user_id(profile_url)
        if not user_id:
            return jsonify({"error": "未能从链接解析出用户ID，请确认是快手用户主页链接"}), 400

        raw_items, next_pcursor = client.get_user_feed(user_id, pcursor)
        items = [KuaishouClient.parse_media_info(it) for it in raw_items]

        for it in raw_items:
            a = it.get("author") or {}
            if a.get("name") or a.get("headerUrl"):
                author = {"name": a.get("name", ""), "avatar": a.get("headerUrl", "")}
                break

        has_more = bool(next_pcursor and next_pcursor not in ("no_more", "NO_MORE", ""))
        return jsonify({
            "user_id": user_id,
            "author": author,
            "items": items,
            "pcursor": next_pcursor,
            "has_more": has_more,
        })
    except Exception as e:
        return jsonify({"error": f"获取作品列表失败: {str(e)}"}), 500


@kuaishou_bp.route("/download-selected", methods=["POST"])
def download_selected():
    """下载前端选中的作品列表（items 为 user-feed 返回的条目，支持自带 urls 或由后台动态解析）"""
    if _task_state["status"] == "running":
        return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400

    data = request.get_json() or {}
    items = [it for it in (data.get("items") or []) if isinstance(it, dict) and (it.get("urls") or it.get("photo_id"))]
    if not items:
        return jsonify({"error": "没有可下载的作品"}), 400

    ensure_kuaishou_dirs()

    _set_task_state(status="running")
    _task_cancel_event.clear()
    thread = threading.Thread(
        target=_run_selected_download_task,
        args=(items, KUAISHOU_DIR),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": f"已在后台启动下载任务，共 {len(items)} 项", "count": len(items)})


@kuaishou_bp.route("/cancel-download", methods=["POST"])
def cancel_download():
    """取消正在进行的批量下载任务"""
    if _task_state["status"] != "running":
        return jsonify({"error": "当前没有正在运行的下载任务"}), 400

    _task_cancel_event.set()
    _add_log("⚠️ 收到取消请求，正在停止任务...")
    return jsonify({"message": "已发送取消信号，任务将在当前项完成后停止"})


@kuaishou_bp.route("/progress", methods=["GET"])
def get_progress():
    """获取当前批量下载的实时进度和日志"""
    with _task_lock:
        return jsonify(_task_state)


@kuaishou_bp.route("/history", methods=["GET"])
def get_history():
    """获取快手下载历史记录"""
    return jsonify(load_json(HISTORY_FILE, []))


@kuaishou_bp.route("/history", methods=["DELETE"])
def clear_history():
    """清除快手下载历史记录"""
    save_json(HISTORY_FILE, [])
    return jsonify({"message": "历史记录已清空"})


@kuaishou_bp.route("/history/item", methods=["DELETE"])
def delete_history_item():
    """删除单条快手下载历史记录"""
    data = request.get_json() or {}
    index = data.get("index")
    path_str = data.get("path")

    history = load_json(HISTORY_FILE, [])
    if index is not None and 0 <= index < len(history):
        history.pop(index)
        save_json(HISTORY_FILE, history)
        return jsonify({"message": "已删除该历史记录"})
    elif path_str:
        history = [h for h in history if h.get("path") != path_str]
        save_json(HISTORY_FILE, history)
        return jsonify({"message": "已删除该历史记录"})
    return jsonify({"error": "未提供有效索引或路径"}), 400


@kuaishou_bp.route("/open-author-folder", methods=["POST"])
def open_author_folder():
    """打开指定博主的下载文件夹"""
    import subprocess
    import sys

    data = request.get_json() or {}
    author_name = clean_filename(data.get("author", ""))
    ensure_kuaishou_dirs()

    if author_name:
        author_dir = KUAISHOU_DIR / author_name
    else:
        author_dir = KUAISHOU_DIR

    if not author_dir.exists():
        author_dir = KUAISHOU_DIR

    path_str = str(author_dir)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path_str])
        elif sys.platform == "win32":
            subprocess.run(["explorer", path_str])
        else:
            subprocess.run(["xdg-open", path_str])
        return jsonify({"message": "已打开博主目录"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@kuaishou_bp.route("/open-folder", methods=["POST"])
def open_folder():
    """在系统文件管理器中打开快手下载目录"""
    import subprocess
    import sys

    ensure_kuaishou_dirs()
    path_str = str(KUAISHOU_DIR)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path_str])
        elif sys.platform == "win32":
            subprocess.run(["explorer", path_str])
        else:
            subprocess.run(["xdg-open", path_str])
        return jsonify({"message": "文件夹已打开"})
    except Exception as e:
        return jsonify({"error": f"打开文件夹失败: {str(e)}"}), 500


@kuaishou_bp.route("/open-file", methods=["POST"])
def open_file():
    """在系统默认程序中打开特定的文件或文件夹"""
    import subprocess
    import sys

    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400

    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件或文件夹不存在"}), 404

        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@kuaishou_bp.route("/open-parent", methods=["POST"])
def open_parent():
    """打开文件或文件夹所在的父目录并选中当前文件"""
    import subprocess
    import sys

    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400

    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件或文件夹不存在"}), 404

        if path.is_file():
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(path)])
            elif sys.platform == "win32":
                subprocess.run(f'explorer /select,"{path.resolve()}"', shell=True)
            else:
                subprocess.run(["xdg-open", str(path.parent)])
        else:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)])
            elif sys.platform == "win32":
                subprocess.run(f'explorer "{path.resolve()}"', shell=True)
            else:
                subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


# ── 快手作者管理（收藏/查询/删除） ─────────────────────────────

@kuaishou_bp.route("/accounts", methods=["GET"])
def list_accounts():
    """获取所有已收藏的快手作者列表"""
    accounts = load_json(ACCOUNTS_FILE, [])
    return jsonify({"accounts": accounts, "total": len(accounts)})


@kuaishou_bp.route("/accounts", methods=["POST"])
def add_account():
    """收藏快手作者"""
    data = request.get_json() or {}
    user_id = str(data.get("user_id") or data.get("kwaiId") or data.get("eid") or data.get("nickname") or "").strip()
    nickname = (data.get("nickname") or data.get("name") or "").strip()

    if not user_id and not nickname:
        return jsonify({"error": "作者 ID 或昵称不能为空"}), 400

    accounts = load_json(ACCOUNTS_FILE, [])
    for acc in accounts:
        if (user_id and acc.get("user_id") == user_id) or (nickname and acc.get("nickname") == nickname):
            return jsonify({"error": "该作者已在收藏列表中", "account": acc}), 400

    account_entry = {
        "user_id": user_id,
        "nickname": nickname,
        "name": nickname,
        "avatar": data.get("avatar", ""),
        "kwaiId": data.get("kwaiId", ""),
        "eid": data.get("eid", ""),
        "desc": data.get("desc", ""),
        "fans": data.get("fans", 0),
        "url": data.get("url", ""),
        "added_time": time.time(),
    }
    accounts.append(account_entry)
    save_json(ACCOUNTS_FILE, accounts)
    return jsonify({"message": "收藏作者成功", "account": account_entry})


@kuaishou_bp.route("/accounts/<path:user_id>", methods=["DELETE"])
def remove_account(user_id):
    """取消收藏快手作者"""
    user_id = str(user_id).strip()
    accounts = load_json(ACCOUNTS_FILE, [])
    new_accounts = [
        a for a in accounts
        if str(a.get("user_id", "")).strip() != user_id
        and str(a.get("kwaiId", "")).strip() != user_id
        and str(a.get("nickname", "")).strip() != user_id
    ]
    if len(new_accounts) == len(accounts):
        return jsonify({"error": "未找到该收藏作者"}), 404

    save_json(ACCOUNTS_FILE, new_accounts)
    return jsonify({"message": "已取消收藏"})


@kuaishou_bp.route("/accounts/parse", methods=["POST"])
def parse_account_url():
    """从主页链接或分享口令解析作者信息（用于添加作者预览）"""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "请输入作者主页链接或 App 分享口令"}), 400

    client = KuaishouClient()
    try:
        # 1. 优先通过移动端通道免登录获取完整作者档案
        _, mobile_author, _ = client.get_user_feed_from_mobile(url)
        if mobile_author and (mobile_author.get("name") or mobile_author.get("nickname")):
            author_info = dict(mobile_author)
            author_info["url"] = url
            return jsonify({"status": "success", "author": author_info})

        # 2. 兜底尝试解析链接 ID
        resolved = client.resolve_share_url(url)
        user_id = KuaishouClient.extract_user_id(resolved) or KuaishouClient.extract_user_id(url)
        if not user_id:
            return jsonify({"error": "无法从链接中解析出作者 ID"}), 400

        author_info = {
            "user_id": user_id,
            "nickname": user_id,
            "name": user_id,
            "avatar": "",
            "kwaiId": user_id,
            "desc": "",
            "fans": 0,
            "url": url,
        }
        return jsonify({"status": "success", "author": author_info})
    except Exception as e:
        return jsonify({"error": f"解析作者主页失败: {str(e)}"}), 500
