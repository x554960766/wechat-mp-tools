"""
抖音资源下载模块 — 纯 API 调用版本
移植自 douyin-downloader-rust 项目，使用直接 HTTP API 调用代替 Playwright 渲染

功能：
- 单条链接解析下载（视频/图文）
- 用户主页批量下载（游标分页，自动翻页）
- 纯 Python a_bogus 签名（无需 JS 引擎）
- 代理池 + 故障转移
"""

import os
import re
import json
import time
import random
import string
import threading
import urllib.parse
from pathlib import Path
from flask import Blueprint, jsonify, request

import requests as http_requests

from backend.config import (
    DATA_DIR, get_settings, get_proxy_config, get_proxy_url,
    get_proxies_dict, report_proxy_status, load_json, save_json
)
from backend.douyin_sign import sign_detail, sign_params

douyin_bp = Blueprint("douyin", __name__, url_prefix="/api/douyin")

# 抖音下载保存的主目录
DOUYIN_DIR = DATA_DIR / "douyin_downloads"
HISTORY_FILE = DATA_DIR / "douyin_history.json"

# ── 常量 ──────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

REFERER = "https://www.douyin.com/"

# 抖音 API 端点
API_VIDEO_DETAIL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
API_MULTI_DETAIL = "https://www.douyin.com/aweme/v1/web/multi/aweme/detail/"
API_USER_POST = "https://www.douyin.com/aweme/v1/web/aweme/post/"
API_MIX_AWEME = "https://www.douyin.com/aweme/v1/web/mix/aweme/"
API_MUSIC_DETAIL = "https://www.douyin.com/aweme/v1/web/music/detail/"

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
_task_cancel_event = threading.Event()  # 取消标志


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


def ensure_douyin_dirs():
    """确保抖音下载目录存在"""
    DOUYIN_DIR.mkdir(parents=True, exist_ok=True)


# ── 工具函数 ──────────────────────────────────────────────

def clean_filename(filename: str) -> str:
    """清理文件名，移除不支持的字符"""
    filename = re.sub(r'[\\/:*?"<>|\n\r\t]', "", filename)
    filename = filename.strip().replace(" ", "_")
    return filename[:80] if filename else "untitled"


def generate_ms_token(size: int = 107) -> str:
    """生成随机 msToken"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(size))


def generate_verify_fp() -> str:
    """生成 verify_fp"""
    chars = string.ascii_lowercase + string.digits
    random_str = ''.join(random.choice(chars) for _ in range(16))
    return f"verify_0{random_str}"


def add_history_item(title: str, item_type: str, file_path: str, size_bytes: int):
    """保存下载记录到历史记录文件"""
    # 提取来源（下载目录下的第一级子目录名）
    source = ""
    path_str = str(file_path)
    marker = "douyin_downloads/"
    idx = path_str.find(marker)
    if idx >= 0:
        rest = path_str[idx + len(marker):]
        parts = rest.split("/")
        if parts:
            source = parts[0]

    history = load_json(HISTORY_FILE, [])
    history.insert(0, {
        "title": title,
        "type": item_type,
        "path": str(file_path),
        "source": source,
        "size": f"{size_bytes / (1024 * 1024):.2f} MB" if size_bytes else "未知",
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    save_json(HISTORY_FILE, history[:150])


# ── 抖音 API 客户端 ──────────────────────────────────────

class DouyinClient:
    """抖音 API 客户端 — 直接 HTTP 调用 + a_bogus 签名"""

    def __init__(self):
        self.session = http_requests.Session()
        
        # 加载用户设置中的 Cookie
        settings = get_settings()
        cookie = settings.get("douyin_cookie", "").strip()
        
        headers = self._get_common_headers()
        if cookie:
            headers["Cookie"] = cookie
            
        self.session.headers.update(headers)
        
        proxies = get_proxies_dict()
        if proxies:
            self.session.proxies.update(proxies)

    def _get_common_headers(self) -> dict:
        """通用请求头"""
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": REFERER,
            "User-Agent": USER_AGENT,
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "sec-ch-ua-platform": '"macOS"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "priority": "u=1, i",
        }

    def _get_common_params(self) -> dict:
        """通用请求参数"""
        return {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "update_version_code": "0",
            "pc_client_type": "1",
            "version_code": "190600",
            "version_name": "19.6.0",
            "cookie_enabled": "true",
            "screen_width": "1680",
            "screen_height": "1050",
            "browser_language": "zh-CN",
            "browser_platform": "MacIntel",
            "browser_name": "Edge",
            "browser_version": "145.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "engine_version": "145.0.0.0",
            "os_name": "Mac OS",
            "os_version": "10.15.7",
            "cpu_core_num": "8",
            "device_memory": "8",
            "platform": "PC",
            "downlink": "10",
            "effective_type": "4g",
            "round_trip_time": "50",
            "pc_libra_divert": "Mac",
            "support_h265": "1",
            "support_dash": "1",
            "disable_rs": "0",
            "need_filter_settings": "1",
            "list_type": "single",
            "msToken": generate_ms_token(),
            "verifyFp": generate_verify_fp(),
        }

    def _enrich_and_sign(self, params: dict, skip_sign: bool = False) -> dict:
        """补充通用参数并添加签名，返回字典"""
        all_params = self._get_common_params()
        all_params.update(params)

        if not skip_sign:
            try:
                # 构造用于签名的 query string
                query_parts = []
                for k, v in all_params.items():
                    query_parts.append(f"{k}={urllib.parse.quote(str(v))}")
                query = "&".join(query_parts)

                from backend.sign import sign_detail
                a_bogus = sign_detail(query, USER_AGENT)
                all_params["a_bogus"] = a_bogus
            except Exception as e:
                _add_log(f"⚠️ 签名生成失败: {e}")
                
        return all_params

    def api_get(self, url: str, params: dict, skip_sign: bool = False, timeout: int = 15) -> dict:
        """发起签名后的 GET 请求，返回 JSON"""
        all_params = self._enrich_and_sign(params, skip_sign)
        try:
            resp = self.session.get(url, params=all_params, timeout=timeout)
            resp.raise_for_status()
            
            # 抖音在未登录或Cookie失效且无有效签名时，可能会直接返回 0 字节的响应
            if len(resp.content) == 0:
                raise ValueError("未获取到有效数据。请检查是否已在左侧菜单「扫码登录」并获取有效 Cookie。")
                
            return resp.json()
        except ValueError as ve:
            raise Exception(str(ve))
        except Exception as e:
            raise Exception(f"API 请求失败: {url} — {str(e)}")

    def api_post(self, url: str, params: dict, data: dict = None, skip_sign: bool = False, timeout: int = 15) -> dict:
        """发起签名后的 POST 请求，返回 JSON"""
        all_params = self._enrich_and_sign(params, skip_sign)
        try:
            if data is not None:
                resp = self.session.post(url, params=all_params, data=data, timeout=timeout)
            else:
                resp = self.session.post(url, data=all_params, timeout=timeout)
            resp.raise_for_status()
            
            if len(resp.content) == 0:
                raise ValueError("未获取到有效数据。请检查是否已在左侧菜单「扫码登录」并获取有效 Cookie。")
                
            return resp.json()
        except ValueError as ve:
            raise Exception(str(ve))
        except Exception as e:
            raise Exception(f"API 请求失败: {url} — {str(e)}")

    # ── 数据查询接口 ──────────────────────────────────────────

    def search_user(self, keyword: str, offset: int = 0) -> dict:
        """搜索用户"""
        import urllib.parse
        encoded_keyword = urllib.parse.quote(keyword)
        self.session.headers["Referer"] = f"https://www.douyin.com/jingxuan/search/{encoded_keyword}?type=user"
        params = {
            "keyword": keyword,
            "search_channel": "aweme_user_web",
            "search_source": "normal_search",
            "query_correct_type": "1",
            "is_filter_search": "0",
            "from_group_id": "",
            "offset": str(offset),
            "count": "10",
            "pc_search_top_1_params": '{"enable_ai_search_top_1":1}'
        }
        return self.api_get("https://www.douyin.com/aweme/v1/web/discover/search/", params, skip_sign=True)

    def get_user_detail(self, sec_uid: str) -> dict:
        """获取用户详情"""
        self.session.headers["Referer"] = "https://www.douyin.com/"
        params = {
            "sec_user_id": sec_uid,
            "personal_center_strategy": "1",
            "source": "channel_pc_web"
        }
        return self.api_get("https://www.douyin.com/aweme/v1/web/user/profile/other/", params, skip_sign=True)

    def get_recommended_feed(self, count: int = 10, cursor: int = 0) -> dict:
        """获取推荐视频流"""
        self.session.headers["Referer"] = "https://www.douyin.com/?recommend=1"
        params = {
            "module_id": "3003101",
            "count": str(count),
            "pull_type": "0",
            "refresh_index": "1",
            "refer_type": "10",
            "use_lite_type": "2",
            "awemePcRecRawData": '{"is_xigua_user":0,"danmaku_switch_status":0,"is_client":false}'
        }
        if cursor > 0:
            params["cursor"] = str(cursor)
        return self.api_post("https://www.douyin.com/aweme/v2/web/module/feed/", params)

    def get_self_sec_uid(self) -> str:
        """获取登录用户自己的 sec_uid"""
        try:
            res = self.api_get("https://www.douyin.com/aweme/v1/web/user/profile/self/", {}, skip_sign=True)
            if res.get("status_code") == 0 and "user" in res:
                return res["user"].get("sec_uid", "")
        except Exception as e:
            _add_log(f"⚠️ 获取登录用户个人信息失败: {e}")
        return ""

    def get_liked_videos(self, sec_uid: str = "", max_cursor: int = 0, count: int = 18) -> dict:
        """获取点赞视频列表"""
        if not sec_uid:
            sec_uid = self.get_self_sec_uid()
            if not sec_uid:
                raise Exception("未登录或获取个人信息失败，请先扫码登录获取 Cookie")

        params = {
            "max_cursor": str(max_cursor),
            "count": str(count),
            "locate_query": "false",
            "publish_video_strategy_type": "2"
        }
        params["sec_user_id"] = sec_uid
        return self.api_get("https://www.douyin.com/aweme/v1/web/aweme/favorite/", params, skip_sign=True)

    def get_collected_videos(self, cursor: int = 0, count: int = 18) -> dict:
        """获取收藏视频列表 (需要登录)"""
        params = {
            "cursor": str(cursor),
            "count": str(count)
        }

        # 添加特殊的 headers
        self.session.headers.update({
            "Referer": "https://www.douyin.com/user/self?from_tab_name=main&showTab=favorite_collection",
            "Origin": "https://www.douyin.com",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        })

        body_data = {
            "cursor": str(cursor),
            "count": str(count)
        }

        # 使用 POST 请求，query params 和 body params 分离
        return self.api_post("https://www.douyin.com/aweme/v1/web/aweme/listcollection/", params, data=body_data)


    def get_collect_folders(self, cursor: int = 0, count: int = 10) -> dict:
        """获取收藏夹列表 (需要登录)"""
        params = {
            "cursor": str(cursor),
            "count": str(count)
        }
        self.session.headers.update({
            "Referer": "https://www.douyin.com/user/self?showTab=favorite_collection",
            "Origin": "https://www.douyin.com"
        })
        return self.api_get("https://www.douyin.com/aweme/v1/web/collects/list/", params)


    def get_collect_folder_videos(self, collect_id: str, cursor: int = 0, count: int = 18) -> dict:
        """获取特定收藏夹下的视频列表 (需要登录)"""
        params = {
            "collect_id": str(collect_id),
            "collects_id": str(collect_id),
            "cursor": str(cursor),
            "count": str(count)
        }
        self.session.headers.update({
            "Referer": f"https://www.douyin.com/collection/{collect_id}",
            "Origin": "https://www.douyin.com"
        })
        return self.api_get("https://www.douyin.com/aweme/v1/web/collects/video/list/", params)


    # ── 链接解析 ──────────────────────────────────────────

    def resolve_share_url(self, url: str) -> str:
        """解析抖音分享链接（短链重定向到最终 URL）"""
        match = re.search(r"https?://[^\s<>\"']+", url.strip())
        if not match:
            return url
        target = match.group(0).rstrip("，。！？；、,.!;")

        try:
            resp = self.session.get(target, allow_redirects=True, timeout=10)
            return resp.url
        except Exception:
            return target

    @staticmethod
    def extract_aweme_id(url: str) -> str:
        """从 URL 中提取 aweme_id"""
        url = url.strip()
        # 纯数字
        if re.match(r"^\d+$", url):
            return url
        # 各种 URL 格式
        patterns = [
            r"video/(\d+)",
            r"note/(\d+)",
            r"aweme_id=(\d+)",
            r"modal_id=(\d+)",
            r"/(\d{18,21})",
        ]
        for pattern in patterns:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return ""

    # ── 单条作品详情 API ──────────────────────────────────

    def get_video_detail(self, aweme_id: str) -> dict:
        """获取单条作品详情，自动双接口兜底"""
        # 1. 先用 detail 接口（skip_sign=True, 对应参考项目的 unsigned first）
        try:
            data = self.api_get(API_VIDEO_DETAIL, {
                "aweme_id": aweme_id,
                "aid": "6383",
                "version_name": "23.5.0",
                "device_platform": "webapp",
                "os": "windows",
            }, skip_sign=True)

            if data.get("status_code") == 0 and data.get("aweme_detail"):
                return data["aweme_detail"]
        except Exception:
            pass

        # 2. detail 接口带签名重试
        try:
            data = self.api_get(API_VIDEO_DETAIL, {
                "aweme_id": aweme_id,
                "aid": "6383",
                "version_name": "23.5.0",
                "device_platform": "webapp",
                "os": "windows",
            }, skip_sign=False)

            if data.get("status_code") == 0 and data.get("aweme_detail"):
                return data["aweme_detail"]
        except Exception:
            pass

        # 3. multi detail 兜底
        try:
            data = self.api_get(API_MULTI_DETAIL, {
                "aweme_ids": f"[{aweme_id}]",
                "request_source": "200",
            }, skip_sign=True)

            if data.get("status_code") == 0:
                details = data.get("aweme_details", [])
                if details:
                    for d in details:
                        if d.get("aweme_id") == aweme_id:
                            return d
                    return details[0]
        except Exception:
            pass

        raise Exception(f"无法获取作品 {aweme_id} 的详情，可能需要有效的 Cookie 或作品已删除")

    def get_mix_videos(self, mix_id: str, cursor: int = 0, count: int = 20) -> tuple:
        """获取合集作品列表，返回 (aweme_list, next_cursor, has_more)"""
        data = self.api_get(API_MIX_AWEME, {
            "mix_id": mix_id,
            "cursor": str(cursor),
            "count": str(count),
        }, skip_sign=False)

        if data.get("status_code") != 0:
            msg = data.get("status_msg", "未知错误")
            raise Exception(f"获取合集作品列表失败: {msg}")

        aweme_list = data.get("aweme_list") or []
        has_more = data.get("has_more", 0)
        if isinstance(has_more, bool):
            has_more = has_more
        else:
            has_more = int(has_more) == 1
        next_cursor = data.get("cursor", 0)

        return aweme_list, next_cursor, has_more

    def get_user_mixes(self, sec_uid: str, cursor: int = 0, count: int = 20) -> tuple:
        """获取用户的合集列表，返回 (mix_infos, next_cursor, has_more)"""
        data = self.api_get("https://www.douyin.com/aweme/v1/web/mix/list/", {
            "sec_user_id": sec_uid,
            "cursor": str(cursor),
            "count": str(count),
        }, skip_sign=False)

        if data.get("status_code") != 0:
            msg = data.get("status_msg", "未知错误")
            raise Exception(f"获取合集列表失败: {msg}")

        mix_infos = data.get("mix_infos") or []
        has_more = data.get("has_more", 0)
        if isinstance(has_more, bool):
            has_more = has_more
        else:
            has_more = int(has_more) == 1
        next_cursor = data.get("cursor", 0)

        return mix_infos, next_cursor, has_more


    def get_music_detail(self, music_id: str) -> dict:
        """获取音乐详情，带兜底"""
        try:
            data = self.api_get(API_MUSIC_DETAIL, {
                "music_id": music_id,
            }, skip_sign=True)
            if data.get("status_code") == 0 and data.get("music_info"):
                return data["music_info"]
        except Exception:
            pass

        try:
            data = self.api_get(API_MUSIC_DETAIL, {
                "music_id": music_id,
            }, skip_sign=False)
            if data.get("status_code") == 0 and data.get("music_info"):
                return data["music_info"]
        except Exception:
            pass

        raise Exception(f"无法获取音乐 {music_id} 的详情，可能需要有效的 Cookie 或作品已删除")

    # ── 用户作品列表 API ──────────────────────────────────

    def get_user_videos(self, sec_uid: str, max_cursor: int = 0, count: int = 18) -> tuple:
        """获取用户发布的作品列表，返回 (aweme_list, next_cursor, has_more)"""
        data = self.api_get(API_USER_POST, {
            "publish_video_strategy_type": "2",
            "sec_user_id": sec_uid,
            "max_cursor": str(max_cursor),
            "locate_query": "false",
            "show_live_replay_strategy": "1",
            "need_time_list": "0",
            "time_list_query": "0",
            "whale_cut_token": "",
            "count": str(count),
        }, skip_sign=True)

        if data.get("status_code") != 0:
            msg = data.get("status_msg", "未知错误")
            raise Exception(f"获取用户作品列表失败: {msg}")

        aweme_list = data.get("aweme_list") or []
        has_more = data.get("has_more", 0)
        if isinstance(has_more, bool):
            has_more = has_more
        else:
            has_more = int(has_more) == 1
        next_cursor = data.get("max_cursor", 0)

        return aweme_list, next_cursor, has_more

    def get_user_stories(self, sec_uid: str, max_cursor: int = 0, count: int = 18) -> tuple:
        """获取用户日常列表，返回 (aweme_list, next_cursor, has_more)"""
        data = self.api_get(API_USER_POST, {
            "publish_video_strategy_type": "1",
            "sec_user_id": sec_uid,
            "max_cursor": str(max_cursor),
            "locate_query": "false",
            "show_live_replay_strategy": "1",
            "need_time_list": "0",
            "time_list_query": "0",
            "whale_cut_token": "",
            "count": str(count),
        }, skip_sign=True)

        if data.get("status_code") != 0:
            msg = data.get("status_msg", "未知错误")
            raise Exception(f"获取用户日常列表失败: {msg}")

        aweme_list = data.get("aweme_list") or []
        has_more = data.get("has_more", 0)
        if isinstance(has_more, bool):
            has_more = has_more
        else:
            has_more = int(has_more) == 1
        next_cursor = data.get("max_cursor", 0)

        return aweme_list, next_cursor, has_more


    # ── 解析资源信息 ──────────────────────────────────────

    @staticmethod
    def parse_media_info(detail: dict) -> dict:
        """
        从 aweme_detail 中提取下载所需的资源信息
        返回: {type, title, urls, aweme_id, nickname}
        """
        aweme_id = detail.get("aweme_id", "unknown")
        desc = detail.get("desc", "")
        title = clean_filename(desc) if desc else f"抖音作品_{aweme_id}"

        author_nickname = ""
        if "author" in detail and isinstance(detail["author"], dict):
            author_nickname = detail["author"].get("nickname", "")
        nickname = clean_filename(author_nickname.strip()) if author_nickname else ""
        if not nickname or nickname == "untitled":
            nickname = "未知用户"

        # 判断类型：images 存在且非空 → 图文；否则 → 视频
        images = detail.get("images")
        if not images:
            image_post_info = detail.get("image_post_info")
            if isinstance(image_post_info, dict):
                images = image_post_info.get("images")

        if images and isinstance(images, list) and len(images) > 0:
            # 图文类型
            urls = []
            for img in images:
                url_list = img.get("url_list") or []
                if not url_list and "display_image" in img:
                    display_image = img.get("display_image")
                    if isinstance(display_image, dict):
                        url_list = display_image.get("url_list") or []
                if not url_list and "owner_watermark_image" in img:
                    watermark_image = img.get("owner_watermark_image")
                    if isinstance(watermark_image, dict):
                        url_list = watermark_image.get("url_list") or []

                if url_list:
                    # 取最后一个（通常是最高清）
                    urls.append(url_list[-1] if len(url_list) > 1 else url_list[0])

            # 提取背景音乐 URL
            music_url = ""
            music = detail.get("music")
            if music and isinstance(music, dict):
                play_url = music.get("play_url")
                if play_url and isinstance(play_url, dict):
                    music_urls = play_url.get("url_list") or []
                    if music_urls:
                        music_url = music_urls[0]

            return {
                "type": "image",
                "title": title,
                "urls": urls,
                "aweme_id": aweme_id,
                "nickname": nickname,
                "music_url": music_url,
            }
        else:
            # 视频类型 — 从多个地址源中选择最佳无水印版本
            video = detail.get("video", {})
            url_candidates = []

            # 1. bit_rate 中的最佳质量
            bit_rates = video.get("bit_rate", [])
            if bit_rates:
                # 按 data_size 降序选择最大质量
                sorted_rates = sorted(bit_rates, key=lambda x: x.get("data_size", 0), reverse=True)
                for br in sorted_rates:
                    play_addr = br.get("play_addr", {})
                    url_list = play_addr.get("url_list", [])
                    for u in url_list:
                        if u and "watermark=1" not in u.lower() and "playwm" not in u.lower():
                            url_candidates.append(u)
                            break

            # 2. play_addr_h264 (H264 版本, 通常无水印)
            h264 = video.get("play_addr_h264", {})
            if isinstance(h264, dict):
                for u in (h264.get("url_list") or []):
                    if u and "watermark=1" not in u.lower() and "playwm" not in u.lower():
                        url_candidates.append(u)
                        break

            # 3. play_addr (主播放地址)
            play_addr = video.get("play_addr", {})
            if isinstance(play_addr, dict):
                for u in (play_addr.get("url_list") or []):
                    if u:
                        clean = u.replace("watermark=1", "watermark=0").replace("playwm", "play")
                        url_candidates.append(clean)
                        break

            # 4. download_addr (下载地址，可能有水印)
            download_addr = video.get("download_addr", {})
            if isinstance(download_addr, dict):
                for u in (download_addr.get("url_list") or []):
                    if u:
                        url_candidates.append(u)
                        break

            # 5. play_addr_lowbr (低码率备用)
            lowbr = video.get("play_addr_lowbr", {})
            if isinstance(lowbr, dict):
                for u in (lowbr.get("url_list") or []):
                    if u:
                        url_candidates.append(u)
                        break

            # 选择第一个无水印的链接
            chosen_url = ""
            for u in url_candidates:
                lower = u.lower()
                if "watermark=1" not in lower and "playwm" not in lower:
                    chosen_url = u
                    break
            if not chosen_url and url_candidates:
                chosen_url = url_candidates[0]

            return {
                "type": "video",
                "title": title,
                "urls": [chosen_url] if chosen_url else [],
                "aweme_id": aweme_id,
                "nickname": nickname,
            }


# ── 文件下载 ──────────────────────────────────────────────

def download_file(url: str, save_path: Path) -> int:
    """下载文件到指定路径，返回文件大小(bytes)"""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
    }

    proxies = get_proxies_dict()
    proxy_url = proxies.get("http") if proxies else None

    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        r = http_requests.get(url, headers=headers, proxies=proxies, stream=True, timeout=30)
        if r.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            report_proxy_status(proxy_url, success=True)
            return save_path.stat().st_size
        else:
            raise Exception(f"HTTP {r.status_code}")
    except Exception as e:
        report_proxy_status(proxy_url, success=False)
        raise e


def download_media(media_info: dict, target_dir: Path, custom_dir: Path = None) -> dict:
    """根据解析的媒体信息执行下载"""
    aweme_id = media_info["aweme_id"]
    title = media_info["title"]
    item_type = media_info["type"]
    urls = media_info["urls"]
    nickname = media_info.get("nickname", "未知用户")

    if not urls:
        raise Exception("无法获取资源下载链接")

    title_with_id = f"{aweme_id}_{title}"
    total_bytes = 0

    user_dir = custom_dir if custom_dir else (target_dir / nickname)
    user_dir.mkdir(parents=True, exist_ok=True)

    if item_type == "video":
        save_file = user_dir / f"{title_with_id}.mp4"
        _add_log(f"🎬 正在下载无水印视频: {save_file.name}")
        total_bytes = download_file(urls[0], save_file)
        _add_log(f"✅ 视频下载完成! 大小: {total_bytes / (1024 * 1024):.2f} MB")
        add_history_item(title, "视频", save_file, total_bytes)
    else:
        folder_path = user_dir / title_with_id
        folder_path.mkdir(parents=True, exist_ok=True)
        _add_log(f"📸 正在下载图集 (共 {len(urls)} 张) 到目录: {folder_path.name}")
        for idx, img_url in enumerate(urls, 1):
            save_file = folder_path / f"{idx}.jpeg"
            img_bytes = download_file(img_url, save_file)
            total_bytes += img_bytes
        _add_log(f"✅ 图集全部下载完成! 总大小: {total_bytes / (1024 * 1024):.2f} MB")

        # 下载图文背景音乐 (BGM)
        music_url = media_info.get("music_url")
        if music_url:
            _add_log("🎵 发现图文背景音乐，正在下载...")
            try:
                save_music = folder_path / "bgm.mp3"
                music_bytes = download_file(music_url, save_music)
                total_bytes += music_bytes
                _add_log(f"✅ 背景音乐下载成功! 大小: {music_bytes / (1024 * 1024):.2f} MB")
            except Exception as me:
                _add_log(f"⚠️ 背景音乐下载失败: {me}")

        add_history_item(title, "图文", folder_path, total_bytes)

    return {
        "id": aweme_id,
        "title": title,
        "type": item_type,
        "size_bytes": total_bytes,
    }


def download_music_file(music_info: dict, target_dir: Path) -> dict:
    """下载音乐原声 mp3 文件"""
    music_id = str(music_info["id"])
    title = clean_filename(music_info["title"])
    author = clean_filename(music_info.get("author", "未知歌手"))

    play_url = music_info.get("play_url", {})
    urls = play_url.get("url_list", [])
    if not urls:
        raise Exception("该音乐没有可用的播放/下载链接")

    user_dir = target_dir / author
    user_dir.mkdir(parents=True, exist_ok=True)

    save_file = user_dir / f"原声_{music_id}_{title}.mp3"
    _add_log(f"🎵 正在下载音乐原声: {save_file.name}")

    total_bytes = download_file(urls[0], save_file)
    _add_log(f"✅ 音乐下载完成! 大小: {total_bytes / (1024 * 1024):.2f} MB")

    add_history_item(title, "音乐", save_file, total_bytes)

    return {
        "id": music_id,
        "title": f"原声 - {title}",
        "type": "music",
        "size_bytes": total_bytes,
    }


# ── 后台批处理执行器 ──────────────────────────────────────

def _run_profile_download_task(sec_uid: str, max_pages: int, target_dir: Path):
    """后台线程：使用 API 获取用户所有发布作品并下载"""
    _add_log(f"正在准备抓取主页资源，目标 sec_uid: {sec_uid[:20]}...")

    client = DouyinClient()

    try:
        nickname = "未知用户"
        try:
            user_res = client.get_user_detail(sec_uid)
            if user_res and user_res.get("user"):
                nickname = user_res["user"].get("nickname", "未知用户")
        except Exception:
            pass
        nickname = clean_filename(nickname)
        profile_dir = target_dir / f"{nickname}的作品"
        profile_dir.mkdir(parents=True, exist_ok=True)

        # 第一步：收集所有作品列表
        _add_log("正在通过 API 获取用户作品列表...")
        all_items = []
        cursor = 0
        page = 0

        while page < max_pages:
            # 检查是否取消
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            page += 1
            _add_log(f"正在获取第 {page} 页作品数据 (cursor={cursor})...")

            try:
                aweme_list, next_cursor, has_more = client.get_user_videos(sec_uid, cursor)
            except Exception as e:
                _add_log(f"⚠️ 获取第 {page} 页数据失败: {str(e)}，尝试继续...")
                break

            if not aweme_list:
                _add_log(f"第 {page} 页返回空数据，已到达最后一页")
                break

            all_items.extend(aweme_list)
            _add_log(f"第 {page} 页获取到 {len(aweme_list)} 个作品，累计 {len(all_items)} 个")

            if not has_more:
                _add_log("已获取全部作品数据")
                break

            cursor = next_cursor
            # 随机延迟避免风控
            delay = random.uniform(1.5, 4.0)
            _add_log(f"休眠 {delay:.1f} 秒规避频率风控...")
            time.sleep(delay)

        if not all_items:
            _set_task_state(status="failed")
            _add_log("❌ 未能获取到任何作品数据，请确认用户主页链接正确")
            return

        _add_log(f"🚀 作品列表获取完成！共 {len(all_items)} 个作品待处理")
        _reset_task_state(total=len(all_items))

        # 第二步：逐个下载
        downloaded = 0
        failed = 0

        for idx, item in enumerate(all_items, 1):
            # 检查是否取消
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            _set_task_state(current_index=idx)

            try:
                media_info = DouyinClient.parse_media_info(item)
                if not media_info["urls"]:
                    _add_log(f"⚠️ 第 {idx} 项无可用资源链接，跳过")
                    failed += 1
                    _set_task_state(failed_count=failed)
                    continue

                _add_log(f"[{idx}/{len(all_items)}] 正在下载: {media_info['title'][:30]}...")
                result = download_media(media_info, target_dir, custom_dir=profile_dir)
                downloaded += 1
                _set_task_state(downloaded_count=downloaded, current_title=result["title"])
            except Exception as e:
                failed += 1
                _set_task_state(failed_count=failed)
                _add_log(f"❌ 第 {idx} 项下载失败: {str(e)}")

            # 每个作品之间的间隔
            if idx < len(all_items):
                delay = random.uniform(1.0, 3.0)
                time.sleep(delay)

        _add_log(f"🎉 批量下载任务结束! 成功: {downloaded}，失败: {failed}")
        add_history_item(f"{nickname}的作品", "批量", profile_dir, downloaded)
        _set_task_state(status="completed")

    except Exception as outer_err:
        _add_log(f"💥 批量下载任务发生错误: {str(outer_err)}")
        _set_task_state(status="failed")


def _run_mix_download_task(mix_id: str, target_dir: Path):
    """后台线程：使用 API 获取合集所有视频并下载"""
    _add_log(f"正在准备抓取合集资源，目标 mix_id: {mix_id}...")

    client = DouyinClient()

    try:
        # 第一步：收集所有作品列表
        _add_log("正在通过 API 获取合集作品列表...")
        all_items = []
        cursor = 0
        page = 0
        mix_name = ""

        # Capture cookies first
        try:
            client.session.get("https://www.douyin.com/", timeout=10)
        except Exception:
            pass

        while True:
            # 检查是否取消
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            page += 1
            _add_log(f"正在获取第 {page} 页合集数据 (cursor={cursor})...")

            try:
                aweme_list, next_cursor, has_more = client.get_mix_videos(mix_id, cursor)
            except Exception as e:
                _add_log(f"⚠️ 获取第 {page} 页数据失败: {str(e)}，尝试继续...")
                break

            if not aweme_list:
                _add_log(f"第 {page} 页返回空数据，已到达最后一页")
                break

            # Extract mix name if not already done
            if not mix_name and aweme_list[0].get("mix_info"):
                mix_name = aweme_list[0]["mix_info"].get("mix_name", "")

            all_items.extend(aweme_list)
            _add_log(f"第 {page} 页获取到 {len(aweme_list)} 个作品，累计 {len(all_items)} 个")

            if not has_more:
                _add_log("已获取全部合集作品数据")
                break

            cursor = next_cursor
            # 随机延迟避免风控
            delay = random.uniform(1.5, 4.0)
            _add_log(f"休眠 {delay:.1f} 秒规避频率风控...")
            time.sleep(delay)

        if not all_items:
            _set_task_state(status="failed")
            _add_log("❌ 未能获取到任何合集作品数据，请确认合集 ID 正确")
            return

        mix_name = clean_filename(mix_name or f"合集_{mix_id}")
        _add_log(f"🚀 合集列表获取完成！合集名称: {mix_name}，共 {len(all_items)} 个作品待处理")
        _reset_task_state(total=len(all_items))

        # 第二步：逐个下载
        downloaded = 0
        failed = 0

        # 获取作者昵称
        first_author = all_items[0].get("author", {})
        nickname = "未知用户"
        if isinstance(first_author, dict):
            nickname = first_author.get("nickname", "未知用户")
        nickname = clean_filename(nickname)

        mix_dir = target_dir / nickname / f"合集_{mix_name}"
        mix_dir.mkdir(parents=True, exist_ok=True)

        for idx, item in enumerate(all_items, 1):
            # 检查是否取消
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            _set_task_state(current_index=idx)

            try:
                media_info = DouyinClient.parse_media_info(item)
                if not media_info["urls"]:
                    _add_log(f"⚠️ 第 {idx} 项无可用资源链接，跳过")
                    failed += 1
                    _set_task_state(failed_count=failed)
                    continue

                _add_log(f"[{idx}/{len(all_items)}] 正在下载: {media_info['title'][:30]}...")
                result = download_media(media_info, target_dir, custom_dir=mix_dir)
                downloaded += 1
                _set_task_state(downloaded_count=downloaded, current_title=result["title"])
            except Exception as e:
                failed += 1
                _set_task_state(failed_count=failed)
                _add_log(f"❌ 第 {idx} 项下载失败: {str(e)}")

            # 每个作品之间的间隔
            if idx < len(all_items):
                delay = random.uniform(1.0, 3.0)
                time.sleep(delay)

        _add_log(f"🎉 批量下载合集任务结束! 成功: {downloaded}，失败: {failed}")
        add_history_item(mix_name, "合集", mix_dir, 0)
        _set_task_state(status="completed")

    except Exception as outer_err:
        _add_log(f"💥 批量下载合集任务发生错误: {str(outer_err)}")
        _set_task_state(status="failed")


def _run_user_download_task(sec_uid: str, types: list, max_pages: int, target_dir: Path):
    """后台线程：下载博主的作品、喜欢或合集（支持组合）"""
    _add_log(f"正在准备抓取博主资源，目标 sec_uid: {sec_uid[:20]}...")
    client = DouyinClient()

    try:
        # Capture cookies first
        try:
            client.session.get("https://www.douyin.com/", timeout=10)
        except Exception:
            pass

        # 解析昵称
        _add_log("正在获取博主资料...")
        nickname = "未知用户"
        try:
            user_res = client.get_user_detail(sec_uid)
            if user_res and user_res.get("user"):
                nickname = user_res["user"].get("nickname", "未知用户")
        except Exception:
            pass
        nickname = clean_filename(nickname)
        _add_log(f"当前博主: {nickname}")

        # 1. 下载作品 (Post)
        if "post" in types:
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            _add_log(f"🚀 [1/4] 开始抓取博主作品列表...")
            all_items = []
            cursor = 0
            page = 0
            while page < max_pages if max_pages > 0 else True:
                if _task_cancel_event.is_set():
                    _add_log("⚠️ 用户取消了任务")
                    _set_task_state(status="cancelled")
                    return
                page += 1
                _add_log(f"正在获取作品第 {page} 页 (cursor={cursor})...")
                try:
                    aweme_list, next_cursor, has_more = client.get_user_videos(sec_uid, cursor)
                except Exception as e:
                    _add_log(f"⚠️ 获取作品第 {page} 页失败: {e}")
                    break
                if not aweme_list:
                    break
                all_items.extend(aweme_list)
                if not has_more:
                    break
                cursor = next_cursor
                time.sleep(random.uniform(1.0, 2.5))

            if all_items:
                _add_log(f"作品列表获取完成！共 {len(all_items)} 个作品待下载。")
                _reset_task_state(total=len(all_items))
                downloaded = 0
                failed = 0
                for idx, item in enumerate(all_items, 1):
                    if _task_cancel_event.is_set():
                        _add_log("⚠️ 用户取消了任务")
                        _set_task_state(status="cancelled")
                        return
                    _set_task_state(current_index=idx)
                    try:
                        media_info = DouyinClient.parse_media_info(item)
                        _add_log(f"[{idx}/{len(all_items)}] 正在下载作品: {media_info['title'][:30]}")
                        result = download_media(media_info, target_dir, custom_dir=target_dir / nickname)
                        downloaded += 1
                        _set_task_state(downloaded_count=downloaded, current_title=result["title"])
                    except Exception as e:
                        failed += 1
                        _set_task_state(failed_count=failed)
                        _add_log(f"❌ 第 {idx} 项作品下载失败: {e}")
                    time.sleep(random.uniform(0.5, 1.5))
                _add_log(f"🎉 博主作品下载结束! 成功: {downloaded}，失败: {failed}")
                add_history_item(f"{nickname}的作品", "批量", target_dir / nickname, downloaded)
            else:
                _add_log("该博主作品列表为空或获取失败。")

        # 2. 下载喜欢 (Like)
        if "like" in types:
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            _add_log(f"🚀 [2/4] 开始抓取博主喜欢列表...")
            all_items = []
            cursor = 0
            page = 0
            while page < max_pages if max_pages > 0 else True:
                if _task_cancel_event.is_set():
                    _add_log("⚠️ 用户取消了任务")
                    _set_task_state(status="cancelled")
                    return
                page += 1
                _add_log(f"正在获取喜欢第 {page} 页 (cursor={cursor})...")
                try:
                    res = client.get_liked_videos(sec_uid, cursor)
                    if res.get("status_code") != 0:
                        _add_log(f"⚠️ 获取喜欢失败: {res.get('status_msg')}")
                        break
                    aweme_list = res.get("aweme_list") or []
                    next_cursor = res.get("max_cursor", 0)
                    has_more = res.get("has_more", 0)
                    has_more_bool = has_more if isinstance(has_more, bool) else (int(has_more) == 1)
                except Exception as e:
                    _add_log(f"⚠️ 获取喜欢第 {page} 页失败: {e}")
                    break
                if not aweme_list:
                    break
                all_items.extend(aweme_list)
                if not has_more_bool:
                    break
                cursor = next_cursor
                time.sleep(random.uniform(1.0, 2.5))

            if all_items:
                _add_log(f"喜欢列表获取完成！共 {len(all_items)} 个作品待下载。")
                _reset_task_state(total=len(all_items))
                downloaded = 0
                failed = 0
                for idx, item in enumerate(all_items, 1):
                    if _task_cancel_event.is_set():
                        _add_log("⚠️ 用户取消了任务")
                        _set_task_state(status="cancelled")
                        return
                    _set_task_state(current_index=idx)
                    try:
                        media_info = DouyinClient.parse_media_info(item)
                        _add_log(f"[{idx}/{len(all_items)}] 正在下载喜欢: {media_info['title'][:30]}")
                        result = download_media(media_info, target_dir, custom_dir=target_dir / f"{nickname}的喜欢")
                        downloaded += 1
                        _set_task_state(downloaded_count=downloaded, current_title=result["title"])
                    except Exception as e:
                        failed += 1
                        _set_task_state(failed_count=failed)
                        _add_log(f"❌ 第 {idx} 项喜欢下载失败: {e}")
                    time.sleep(random.uniform(0.5, 1.5))
                _add_log(f"🎉 博主喜欢下载结束! 成功: {downloaded}，失败: {failed}")
                add_history_item(f"{nickname}的喜欢", "批量", target_dir / f"{nickname}的喜欢", downloaded)
            else:
                _add_log("该博主未公开喜欢列表，或喜欢列表为空。")

        # 3. 下载合集 (Mix)
        if "mix" in types:
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            _add_log(f"🚀 [3/4] 开始抓取博主创建的合集列表...")
            mix_infos = []
            try:
                mix_infos, _, _ = client.get_user_mixes(sec_uid)
            except Exception as e:
                _add_log(f"⚠️ 获取合集列表失败: {e}")

            if mix_infos:
                _add_log(f"已获取到 {len(mix_infos)} 个合集，开始逐个下载...")
                for m_idx, m_info in enumerate(mix_infos, 1):
                    if _task_cancel_event.is_set():
                        _add_log("⚠️ 用户取消了任务")
                        _set_task_state(status="cancelled")
                        return
                    mix_id = m_info.get("mix_id")
                    mix_name = m_info.get("mix_name", f"合集_{mix_id}")
                    _add_log(f"📦 正在处理第 {m_idx}/{len(mix_infos)} 个合集: 「{mix_name}」")

                    mix_items = []
                    cursor = 0
                    while True:
                        if _task_cancel_event.is_set():
                            _add_log("⚠️ 用户取消了任务")
                            _set_task_state(status="cancelled")
                            return
                        try:
                            aweme_list, next_cursor, has_more = client.get_mix_videos(mix_id, cursor)
                            if not aweme_list:
                                break
                            mix_items.extend(aweme_list)
                            if not has_more:
                                break
                            cursor = next_cursor
                            time.sleep(random.uniform(1.0, 2.5))
                        except Exception as e:
                            _add_log(f"⚠️ 获取合集 {mix_name} 列表失败: {e}")
                            break

                    if mix_items:
                        _reset_task_state(total=len(mix_items))
                        downloaded = 0
                        failed = 0
                        mix_dir = target_dir / nickname / f"合集_{clean_filename(mix_name)}"
                        mix_dir.mkdir(parents=True, exist_ok=True)
                        for idx, item in enumerate(mix_items, 1):
                            if _task_cancel_event.is_set():
                                _add_log("⚠️ 用户取消了任务")
                                _set_task_state(status="cancelled")
                                return
                            _set_task_state(current_index=idx)
                            try:
                                media_info = DouyinClient.parse_media_info(item)
                                _add_log(f"[{idx}/{len(mix_items)}] 正在下载合集视频: {media_info['title'][:30]}")
                                result = download_media(media_info, target_dir, custom_dir=mix_dir)
                                downloaded += 1
                                _set_task_state(downloaded_count=downloaded, current_title=result["title"])
                            except Exception as e:
                                failed += 1
                                _set_task_state(failed_count=failed)
                                _add_log(f"❌ 下载失败: {e}")
                            time.sleep(random.uniform(0.5, 1.5))
                        _add_log(f"🎉 合集 「{mix_name}」 下载结束! 成功: {downloaded}，失败: {failed}")
                        add_history_item(mix_name, "合集", mix_dir, downloaded)
            else:
                _add_log("该博主未创建任何合集。")

        # 4. 下载日常 (Story)
        if "story" in types:
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            _add_log(f"🚀 [4/4] 开始抓取博主日常列表...")
            all_items = []
            cursor = 0
            page = 0
            while page < max_pages if max_pages > 0 else True:
                if _task_cancel_event.is_set():
                    _add_log("⚠️ 用户取消了任务")
                    _set_task_state(status="cancelled")
                    return
                page += 1
                _add_log(f"正在获取日常第 {page} 页 (cursor={cursor})...")
                try:
                    aweme_list, next_cursor, has_more = client.get_user_stories(sec_uid, cursor)
                except Exception as e:
                    _add_log(f"⚠️ 获取日常第 {page} 页失败: {e}")
                    break
                if not aweme_list:
                    break
                all_items.extend(aweme_list)
                if not has_more:
                    break
                cursor = next_cursor
                time.sleep(random.uniform(1.0, 2.5))

            if all_items:
                _add_log(f"日常列表获取完成！共 {len(all_items)} 个日常待下载。")
                _reset_task_state(total=len(all_items))
                downloaded = 0
                failed = 0
                for idx, item in enumerate(all_items, 1):
                    if _task_cancel_event.is_set():
                        _add_log("⚠️ 用户取消了任务")
                        _set_task_state(status="cancelled")
                        return
                    _set_task_state(current_index=idx)
                    try:
                        media_info = DouyinClient.parse_media_info(item)
                        _add_log(f"[{idx}/{len(all_items)}] 正在下载日常: {media_info['title'][:30]}")
                        result = download_media(media_info, target_dir, custom_dir=target_dir / f"{nickname}的日常")
                        downloaded += 1
                        _set_task_state(downloaded_count=downloaded, current_title=result["title"])
                    except Exception as e:
                        failed += 1
                        _set_task_state(failed_count=failed)
                        _add_log(f"❌ 第 {idx} 项日常下载失败: {e}")
                    time.sleep(random.uniform(0.5, 1.5))
                _add_log(f"🎉 博主日常下载结束! 成功: {downloaded}，失败: {failed}")
                add_history_item(f"{nickname}的日常", "批量", target_dir / f"{nickname}的日常", downloaded)
            else:
                _add_log("该博主日常列表为空或获取失败。")

        # 5. 下载收藏 (Collect)
        if "collect" in types:
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            _add_log(f"🚀 开始抓取您的个人收藏列表...")
            all_items = []
            cursor = 0
            page = 0
            while page < max_pages if max_pages > 0 else True:
                if _task_cancel_event.is_set():
                    _add_log("⚠️ 用户取消了任务")
                    _set_task_state(status="cancelled")
                    return
                page += 1
                _add_log(f"正在获取收藏第 {page} 页 (cursor={cursor})...")
                try:
                    res = client.get_collected_videos(cursor)
                    aweme_list = res.get("aweme_list") or []
                    next_cursor = res.get("cursor") if res.get("cursor") is not None else res.get("max_cursor", 0)
                    has_more = res.get("has_more", False)
                except Exception as e:
                    _add_log(f"⚠️ 获取收藏第 {page} 页失败: {e}")
                    break
                if not aweme_list:
                    break
                all_items.extend(aweme_list)
                if not has_more:
                    break
                cursor = next_cursor
                time.sleep(random.uniform(1.0, 2.5))

            if all_items:
                _add_log(f"收藏列表获取完成！共 {len(all_items)} 个作品待下载。")
                _reset_task_state(total=len(all_items))
                downloaded = 0
                failed = 0
                collect_dir = target_dir / f"{nickname}的收藏"
                collect_dir.mkdir(parents=True, exist_ok=True)
                for idx, item in enumerate(all_items, 1):
                    if _task_cancel_event.is_set():
                        _add_log("⚠️ 用户取消了任务")
                        _set_task_state(status="cancelled")
                        return
                    _set_task_state(current_index=idx)
                    try:
                        media_info = DouyinClient.parse_media_info(item)
                        _add_log(f"[{idx}/{len(all_items)}] 正在下载收藏: {media_info['title'][:30]}")
                        result = download_media(media_info, target_dir, custom_dir=collect_dir)
                        downloaded += 1
                        _set_task_state(downloaded_count=downloaded, current_title=result["title"])
                    except Exception as e:
                        failed += 1
                        _set_task_state(failed_count=failed)
                        _add_log(f"❌ 第 {idx} 项收藏下载失败: {e}")
                    time.sleep(random.uniform(0.5, 1.5))
                _add_log(f"🎉 个人收藏下载结束! 成功: {downloaded}，失败: {failed}")
                add_history_item(f"{nickname}的收藏", "批量", collect_dir, downloaded)
            else:
                _add_log("您的收藏列表为空。")

        _add_log(f"🎉 博主 「{nickname}」 的所有抓取下载任务已全部完成！")
        _set_task_state(status="completed")

    except Exception as outer_err:
        _add_log(f"💥 批量下载任务发生错误: {str(outer_err)}")
        _set_task_state(status="failed")


def _run_liked_download_task(sec_uid: str, max_pages: int, target_dir: Path):
    """后台线程：使用 API 获取用户所有点赞作品并下载"""
    _add_log(f"正在准备抓取点赞资源，目标 sec_uid: {sec_uid[:20] if sec_uid else '当前登录用户'}...")

    client = DouyinClient()

    try:
        # 优化：如果是空，先获取一次自己的 sec_uid
        if not sec_uid:
            _add_log("未提供 sec_uid，正在获取登录用户自己的 sec_uid...")
            sec_uid = client.get_self_sec_uid()
            if not sec_uid:
                raise Exception("未登录或获取个人信息失败，请先扫码登录获取 Cookie")
            _add_log(f"成功获取当前登录用户 sec_uid: {sec_uid[:20]}...")

        # 解析昵称
        _add_log("正在获取点赞用户资料...")
        nickname = "未知用户"
        try:
            user_res = client.get_user_detail(sec_uid)
            if user_res and user_res.get("user"):
                nickname = user_res["user"].get("nickname", "未知用户")
        except Exception:
            pass
        nickname = clean_filename(nickname)
        liked_dir = target_dir / f"{nickname}的喜欢"
        liked_dir.mkdir(parents=True, exist_ok=True)

        # 第一步：收集所有作品列表
        _add_log("正在通过 API 获取用户点赞作品列表...")
        all_items = []
        cursor = 0
        page = 0

        while page < max_pages:
            # 检查是否取消
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            page += 1
            _add_log(f"正在获取第 {page} 页点赞数据 (cursor={cursor})...")

            try:
                res = client.get_liked_videos(sec_uid, cursor)
                if res.get("status_code") != 0:
                    msg = res.get("status_msg", "未知错误")
                    raise Exception(f"获取失败: {msg}")
                aweme_list = res.get("aweme_list") or []
                next_cursor = res.get("max_cursor", 0)
                has_more = res.get("has_more", 0)
                if isinstance(has_more, bool):
                    has_more_bool = has_more
                else:
                    has_more_bool = int(has_more) == 1
            except Exception as e:
                _add_log(f"⚠️ 获取第 {page} 页点赞数据失败: {str(e)}，尝试继续...")
                break

            if not aweme_list:
                _add_log(f"第 {page} 页返回空数据，已到达最后一页")
                break

            all_items.extend(aweme_list)
            _add_log(f"第 {page} 页获取到 {len(aweme_list)} 个作品，累计 {len(all_items)} 个")

            if not has_more_bool:
                _add_log("已获取全部点赞作品数据")
                break

            cursor = next_cursor
            # 随机延迟避免风控
            delay = random.uniform(1.5, 4.0)
            _add_log(f"休眠 {delay:.1f} 秒规避频率风控...")
            time.sleep(delay)

        if not all_items:
            _set_task_state(status="failed")
            _add_log("❌ 未能获取到任何点赞作品数据，请确认登录状态或链接")
            return

        _add_log(f"🚀 点赞作品列表获取完成！共 {len(all_items)} 个作品待处理")
        _reset_task_state(total=len(all_items))

        # 第二步：逐个下载
        downloaded = 0
        failed = 0

        for idx, item in enumerate(all_items, 1):
            # 检查是否取消
            if _task_cancel_event.is_set():
                _add_log("⚠️ 用户取消了任务")
                _set_task_state(status="cancelled")
                return

            _set_task_state(current_index=idx)

            try:
                media_info = DouyinClient.parse_media_info(item)
                if not media_info["urls"]:
                    _add_log(f"⚠️ 第 {idx} 项无可用资源链接，跳过")
                    failed += 1
                    _set_task_state(failed_count=failed)
                    continue

                _add_log(f"[{idx}/{len(all_items)}] 正在下载: {media_info['title'][:30]}...")
                result = download_media(media_info, target_dir, custom_dir=liked_dir)
                downloaded += 1
                _set_task_state(downloaded_count=downloaded, current_title=result["title"])
            except Exception as e:
                failed += 1
                _set_task_state(failed_count=failed)
                _add_log(f"❌ 第 {idx} 项下载失败: {str(e)}")

            # 每个作品之间的间隔
            if idx < len(all_items):
                delay = random.uniform(1.0, 3.0)
                time.sleep(delay)

        _add_log(f"🎉 批量下载点赞任务结束! 成功: {downloaded}，失败: {failed}")
        add_history_item(f"{nickname}的喜欢", "批量", liked_dir, downloaded)
        _set_task_state(status="completed")

    except Exception as outer_err:
        _add_log(f"💥 批量下载点赞任务发生错误: {str(outer_err)}")
        _set_task_state(status="failed")


def _run_batch_items_download_task(items: list, target_dir: Path, source_name: str = ""):
    """后台线程：下载指定的作品列表"""
    _add_log(f"正在准备批量下载选中的 {len(items)} 个作品...")
    _reset_task_state(total=len(items))

    downloaded = 0
    failed = 0

    for idx, item in enumerate(items, 1):
        # 检查是否取消
        if _task_cancel_event.is_set():
            _add_log("⚠️ 用户取消了任务")
            _set_task_state(status="cancelled")
            return

        _set_task_state(current_index=idx)

        try:
            media_info = DouyinClient.parse_media_info(item)
            if not media_info["urls"]:
                _add_log(f"⚠️ 第 {idx} 项无可用资源链接，跳过")
                failed += 1
                _set_task_state(failed_count=failed)
                continue

            _add_log(f"[{idx}/{len(items)}] 正在下载: {media_info['title'][:30]}...")
            custom_dir = target_dir / source_name if source_name else None
            result = download_media(media_info, target_dir, custom_dir=custom_dir)
            downloaded += 1
            _set_task_state(downloaded_count=downloaded, current_title=result["title"])
        except Exception as e:
            failed += 1
            _set_task_state(failed_count=failed)
            _add_log(f"❌ 第 {idx} 项下载失败: {str(e)}")

        # 每个作品之间的间隔
        if idx < len(items):
            delay = random.uniform(1.0, 3.0)
            time.sleep(delay)

    _add_log(f"🎉 批量下载任务结束! 成功: {downloaded}，失败: {failed}")
    if source_name:
        add_history_item(source_name, "批量", target_dir / source_name, downloaded)
    _set_task_state(status="completed")



# ── Blueprint API 路由 ────────────────────────────────────

@douyin_bp.route("/download-single", methods=["POST"])
def download_single():
    """解析并下载单条抖音视频/图文"""
    data = request.get_json() or {}
    raw_url = data.get("url", "").strip()

    if not raw_url:
        return jsonify({"error": "请输入有效的链接"}), 400

    ensure_douyin_dirs()

    try:
        client = DouyinClient()

        # 1. 链接预解析
        final_url = client.resolve_share_url(raw_url)
        _add_log(f"链接解析完成: {final_url}")

        # Check if it is a mix/collection
        mix_match = re.search(r"(?:collection|mix)/(\d+)", final_url)
        if not mix_match:
            mix_match = re.search(r"mix_id=(\d+)", final_url)

        # Check if it is a music track
        music_match = re.search(r"music/(\d+)", final_url)
        if not music_match:
            music_match = re.search(r"music_id=(\d+)", final_url)

        if mix_match:
            mix_id = mix_match.group(1)
            # Check if there is already a running task
            if _task_state["status"] == "running":
                return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400

            global _task_cancel_event
            _task_cancel_event.clear()
            _set_task_state(status="running")

            # Asynchronously start background task
            thread = threading.Thread(
                target=_run_mix_download_task,
                args=(mix_id, DOUYIN_DIR)
            )
            thread.daemon = True
            thread.start()

            return jsonify({
                "message": "已启动合集批量下载",
                "task_started": True
            })

        elif music_match:
            music_id = music_match.group(1)
            # Fetch music details
            music_info = client.get_music_detail(music_id)
            # Download music
            result = download_music_file(music_info, DOUYIN_DIR)
            return jsonify({
                "message": "下载成功",
                "data": result,
                "title": result["title"]
            })

        # 2. 提取 aweme_id
        aweme_id = DouyinClient.extract_aweme_id(final_url)
        if not aweme_id:
            return jsonify({"error": "无法从链接中提取作品 ID"}), 400
        _add_log(f"提取到作品 ID: {aweme_id}")

        # 3. 调用 API 获取详情
        detail = client.get_video_detail(aweme_id)
        _add_log("成功获取作品详情")

        # 4. 解析资源信息
        media_info = DouyinClient.parse_media_info(detail)
        if not media_info["urls"]:
            return jsonify({"error": "无法获取资源下载链接，可能需要有效的 Cookie"}), 400

        # 5. 下载
        result = download_media(media_info, DOUYIN_DIR)

        return jsonify({
            "message": "下载成功",
            "data": result,
            "title": media_info["title"]
        })

    except Exception as e:
        return jsonify({"error": f"下载失败: {str(e)}"}), 500


@douyin_bp.route("/detect-url", methods=["POST"])
def detect_url():
    """检测并解析抖音链接类型"""
    data = request.get_json() or {}
    raw_url = data.get("url", "").strip()

    if not raw_url:
        return jsonify({"error": "请输入有效的链接"}), 400

    ensure_douyin_dirs()

    try:
        client = DouyinClient()

        # 1. 链接预解析
        final_url = client.resolve_share_url(raw_url)

        # 2. 类型识别
        # Check User Profile
        user_match = re.search(r"user/([A-Za-z0-9_\-]+)", final_url)
        if not user_match:
            user_match = re.search(r"sec_user_id=([A-Za-z0-9_\-]+)", final_url)

        if user_match:
            sec_uid = user_match.group(1)
            nickname = "未知用户"
            try:
                # Try to fetch user profile details
                user_res = client.get_user_detail(sec_uid)
                if user_res and user_res.get("user"):
                    nickname = user_res["user"].get("nickname", "未知用户")
            except Exception:
                pass
            return jsonify({
                "type": "user",
                "id": sec_uid,
                "nickname": nickname,
                "resolved_url": final_url,
                "message": f"博主 「{nickname}」 的主页"
            })

        # Check Mix/Collection
        mix_match = re.search(r"(?:collection|mix)/(\d+)", final_url)
        if not mix_match:
            mix_match = re.search(r"mix_id=(\d+)", final_url)

        if mix_match:
            mix_id = mix_match.group(1)
            mix_name = ""
            try:
                aweme_list, _, _ = client.get_mix_videos(mix_id, cursor=0, count=1)
                if aweme_list and aweme_list[0].get("mix_info"):
                    mix_name = aweme_list[0]["mix_info"].get("mix_name", "")
            except Exception:
                pass
            name_str = f"「{mix_name}」" if mix_name else ""
            return jsonify({
                "type": "mix",
                "id": mix_id,
                "title": mix_name,
                "resolved_url": final_url,
                "message": f"视频合集 {name_str}"
            })

        # Check Music
        music_match = re.search(r"music/(\d+)", final_url)
        if not music_match:
            music_match = re.search(r"music_id=(\d+)", final_url)

        if music_match:
            music_id = music_match.group(1)
            music_title = ""
            try:
                music_info = client.get_music_detail(music_id)
                music_title = music_info.get("title", "")
            except Exception:
                pass
            name_str = f"「{music_title}」" if music_title else ""
            return jsonify({
                "type": "music",
                "id": music_id,
                "title": music_title,
                "resolved_url": final_url,
                "message": f"音乐原声 {name_str}"
            })

        # Check Single Video/Note
        aweme_id = DouyinClient.extract_aweme_id(final_url)
        if aweme_id:
            title = ""
            item_type = "video"
            try:
                detail = client.get_video_detail(aweme_id)
                media_info = DouyinClient.parse_media_info(detail)
                title = media_info.get("title", "")
                item_type = media_info.get("type", "video")
            except Exception:
                pass
            type_name = "图文" if item_type == "image" else "视频"
            name_str = f"「{title}」" if title else ""
            return jsonify({
                "type": "single",
                "id": aweme_id,
                "title": title,
                "item_type": item_type,
                "resolved_url": final_url,
                "message": f"单个{type_name} {name_str}"
            })

        return jsonify({"error": "无法识别此链接类型，请检查输入"}), 400

    except Exception as e:
        return jsonify({"error": f"链接检测失败: {str(e)}"}), 500


@douyin_bp.route("/download-user", methods=["POST"])
def download_user():
    """解析并批量下载博主的主页内容 (作品/喜欢/合集)"""
    if _task_state["status"] == "running":
        return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400

    data = request.get_json() or {}
    sec_uid = data.get("sec_uid", "").strip()
    types = data.get("types", []) # ["post", "like", "mix"]
    max_pages = int(data.get("max_pages", 10))

    if not sec_uid:
        return jsonify({"error": "参数错误，缺失博主 ID"}), 400
    if not types:
        return jsonify({"error": "请至少选择一项要下载的内容"}), 400

    ensure_douyin_dirs()

    global _task_cancel_event
    _task_cancel_event.clear()
    _set_task_state(status="running")

    # Asynchronously start background task
    thread = threading.Thread(
        target=_run_user_download_task,
        args=(sec_uid, types, max_pages, DOUYIN_DIR)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        "message": "已启动博主主页批量下载任务",
        "task_started": True
    })


@douyin_bp.route("/download-profile", methods=["POST"])
def download_profile():
    """解析并批量下载抖音用户主页"""
    if _task_state["status"] == "running":
        return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400

    data = request.get_json() or {}
    profile_url = data.get("url", "").strip()
    max_pages = int(data.get("scroll_depth", 10))

    if not profile_url:
        return jsonify({"error": "请输入有效的主页链接"}), 400

    # 解析真实主页 URL
    client = DouyinClient()
    resolved = client.resolve_share_url(profile_url)

    # 提取 sec_uid
    match = re.search(r"/user/([A-Za-z0-9_-]+)", resolved)
    if not match:
        return jsonify({"error": "未能从链接解析出用户的 sec_uid，请确认主页格式是否正确"}), 400

    sec_uid = match.group(1)
    ensure_douyin_dirs()

    # 启动异步后台批量下载任务
    _set_task_state(status="running")
    _task_cancel_event.clear()  # 清除取消标志
    thread = threading.Thread(
        target=_run_profile_download_task,
        args=(sec_uid, max_pages, DOUYIN_DIR),
        daemon=True
    )
    thread.start()

    return jsonify({
        "message": "已在后台启动主页批量解析下载任务",
        "sec_uid": sec_uid
    })


@douyin_bp.route("/download-liked", methods=["POST"])
def download_liked():
    """批量下载抖音点赞视频"""
    if _task_state["status"] == "running":
        return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400

    data = request.get_json() or {}
    sec_uid = data.get("sec_uid", "").strip()
    max_pages = int(data.get("max_pages", 10))

    ensure_douyin_dirs()

    # 启动异步后台批量下载任务
    _set_task_state(status="running")
    _task_cancel_event.clear()  # 清除取消标志
    thread = threading.Thread(
        target=_run_liked_download_task,
        args=(sec_uid, max_pages, DOUYIN_DIR),
        daemon=True
    )
    thread.start()

    return jsonify({
        "message": "已在后台启动点赞视频批量解析下载任务",
        "sec_uid": sec_uid
    })


@douyin_bp.route("/download-batch", methods=["POST"])
def download_batch():
    """批量下载选中的抖音视频"""
    if _task_state["status"] == "running":
        return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400

    data = request.get_json() or {}
    items = data.get("items", [])
    source_name = data.get("source_name", "")

    if not items:
        return jsonify({"error": "没有选中任何视频"}), 400

    ensure_douyin_dirs()

    _set_task_state(status="running")
    _task_cancel_event.clear()
    thread = threading.Thread(
        target=_run_batch_items_download_task,
        args=(items, DOUYIN_DIR, source_name),
        daemon=True
    )
    thread.start()

    return jsonify({
        "message": "已在后台启动批量下载任务",
        "count": len(items)
    })


@douyin_bp.route("/cancel-download", methods=["POST"])
def cancel_download():
    """取消正在进行的批量下载任务"""
    if _task_state["status"] != "running":
        return jsonify({"error": "当前没有正在运行的下载任务"}), 400

    _task_cancel_event.set()  # 设置取消标志
    _add_log("⚠️ 收到取消请求，正在停止任务...")

    return jsonify({"message": "已发送取消信号，任务将在当前项完成后停止"})


@douyin_bp.route("/progress", methods=["GET"])
def get_progress():
    """获取当前批量下载的实时进度和日志"""
    with _task_lock:
        return jsonify(_task_state)


@douyin_bp.route("/history", methods=["GET"])
def get_history():
    """获取抖音下载历史记录"""
    history = load_json(HISTORY_FILE, [])
    return jsonify(history)


@douyin_bp.route("/history", methods=["DELETE"])
def clear_history():
    """清除抖音下载历史记录及下载的文件"""
    import shutil
    save_json(HISTORY_FILE, [])
    # 清空下载文件夹中的所有文件和文件夹
    if DOUYIN_DIR.exists():
        for item in DOUYIN_DIR.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                _add_log(f"⚠️ 清理文件 {item.name} 失败: {e}")
    return jsonify({"message": "历史记录和已下载的文件已清空"})


@douyin_bp.route("/open-folder", methods=["POST"])
def open_folder():
    """在系统文件管理器中打开抖音下载目录"""
    import subprocess
    import sys

    path_str = str(DOUYIN_DIR)
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


@douyin_bp.route("/open-file", methods=["POST"])
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


@douyin_bp.route("/search", methods=["GET"])
def api_search():
    keyword = request.args.get("keyword", "")
    offset = int(request.args.get("offset", 0))
    if not keyword:
        return jsonify({"error": "关键字不能为空"}), 400
    try:
        api = DouyinClient()
        
        # 1. 自动检测是否为 raw sec_uid 或分享/主页链接
        sec_uid = None
        is_direct = False
        kw_strip = keyword.strip()
        
        if kw_strip.startswith("MS4wLjAB") and len(kw_strip) > 40:
            sec_uid = kw_strip
            is_direct = True
        elif kw_strip.startswith("http://") or kw_strip.startswith("https://") or "douyin.com" in kw_strip:
            try:
                final_url = api.resolve_share_url(kw_strip)
                user_match = re.search(r"user/([A-Za-z0-9_\-]+)", final_url)
                if not user_match:
                    user_match = re.search(r"sec_user_id=([A-Za-z0-9_\-]+)", final_url)
                if user_match:
                    sec_uid = user_match.group(1)
                    is_direct = True
            except Exception as e:
                _add_log(f"⚠️ 解析搜索链接失败: {e}")

        if is_direct and sec_uid:
            try:
                detail = api.get_user_detail(sec_uid)
                if detail and detail.get("user"):
                    return jsonify({
                        "status_code": 0,
                        "user_list": [{
                            "user_info": detail["user"]
                        }],
                        "has_more": 0
                    })
            except Exception as e:
                return jsonify({"error": f"直接获取博主详情失败: {str(e)}"}), 500

        # 2. 模糊搜索并处理验证拦截 fallback
        res = api.search_user(keyword, offset)
        if isinstance(res, dict) and res.get("search_nil_info", {}).get("search_nil_type") == "verify_check":
            return jsonify({
                "error": "由于抖音安全验证拦截，无法进行模糊搜索。请直接复制该博主的「主页链接」或「sec_uid」在此搜索，我们将自动解析并跳转！"
            }), 400
            
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/user-detail", methods=["GET"])
def api_user_detail():
    sec_uid = request.args.get("sec_uid", "")
    if not sec_uid:
        return jsonify({"error": "sec_uid不能为空"}), 400
    try:
        api = DouyinClient()
        res = api.get_user_detail(sec_uid)
        if isinstance(res, dict) and "user" in res:
            self_sec_uid = api.get_self_sec_uid()
            res["user"]["is_self"] = (sec_uid == self_sec_uid)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/feed", methods=["GET"])
def api_feed():
    count = int(request.args.get("count", 10))
    cursor = int(request.args.get("cursor", 0))
    try:
        api = DouyinClient()
        res = api.get_recommended_feed(count, cursor)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/liked", methods=["GET"])
def api_liked():
    sec_uid = request.args.get("sec_uid", "")
    max_cursor = int(request.args.get("max_cursor", 0))
    count = int(request.args.get("count", 18))
    try:
        api = DouyinClient()
        res = api.get_liked_videos(sec_uid, max_cursor, count)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/user-videos", methods=["GET"])
def api_user_videos():
    sec_uid = request.args.get("sec_uid", "")
    max_cursor = int(request.args.get("max_cursor", 0))
    count = int(request.args.get("count", 18))
    if not sec_uid:
        return jsonify({"error": "sec_uid不能为空"}), 400
    try:
        api = DouyinClient()
        aweme_list, next_cursor, has_more = api.get_user_videos(sec_uid, max_cursor, count)
        return jsonify({
            "aweme_list": aweme_list,
            "max_cursor": next_cursor,
            "has_more": has_more
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/user-stories", methods=["GET"])
def api_user_stories():
    sec_uid = request.args.get("sec_uid", "")
    max_cursor = int(request.args.get("max_cursor", 0))
    count = int(request.args.get("count", 18))
    if not sec_uid:
        return jsonify({"error": "sec_uid不能为空"}), 400
    try:
        api = DouyinClient()
        aweme_list, next_cursor, has_more = api.get_user_stories(sec_uid, max_cursor, count)
        return jsonify({
            "aweme_list": aweme_list,
            "max_cursor": next_cursor,
            "has_more": has_more
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@douyin_bp.route("/user-mixes", methods=["GET"])
def api_user_mixes():
    sec_uid = request.args.get("sec_uid", "")
    cursor = int(request.args.get("cursor", 0))
    count = int(request.args.get("count", 20))
    if not sec_uid:
        return jsonify({"error": "sec_uid不能为空"}), 400
    try:
        api = DouyinClient()
        mix_infos, next_cursor, has_more = api.get_user_mixes(sec_uid, cursor, count)
        return jsonify({
            "mix_infos": mix_infos,
            "max_cursor": next_cursor,
            "has_more": has_more
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/collected", methods=["GET"])
def api_collected():
    cursor = int(request.args.get("cursor", 0))
    count = int(request.args.get("count", 18))
    try:
        api = DouyinClient()
        res = api.get_collected_videos(cursor, count)
        if isinstance(res, dict):
            # 兼容前端的 max_cursor 字段
            next_cursor = res.get("cursor") if res.get("cursor") is not None else res.get("max_cursor", 0)
            res["max_cursor"] = next_cursor
            if res.get("has_more") is not None:
                res["has_more"] = res["has_more"] if isinstance(res["has_more"], bool) else int(res["has_more"]) == 1
            else:
                res["has_more"] = False
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/collects/list", methods=["GET"])
def api_collects_list():
    cursor = int(request.args.get("cursor", 0))
    count = int(request.args.get("count", 20))
    try:
        api = DouyinClient()
        res = api.get_collect_folders(cursor, count)
        if isinstance(res, dict):
            next_cursor = res.get("cursor") if res.get("cursor") is not None else res.get("max_cursor", 0)
            res["max_cursor"] = next_cursor
            if res.get("has_more") is not None:
                res["has_more"] = res["has_more"] if isinstance(res["has_more"], bool) else int(res["has_more"]) == 1
            else:
                res["has_more"] = False
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/collects/video/list", methods=["GET"])
def api_collects_video_list():
    collect_id = request.args.get("collect_id", "")
    cursor = int(request.args.get("cursor", 0))
    count = int(request.args.get("count", 18))
    if not collect_id:
        return jsonify({"error": "collect_id 不能为空"}), 400
    try:
        api = DouyinClient()
        res = api.get_collect_folder_videos(collect_id, cursor, count)
        if isinstance(res, dict):
            next_cursor = res.get("cursor") if res.get("cursor") is not None else res.get("max_cursor", 0)
            res["max_cursor"] = next_cursor
            if res.get("has_more") is not None:
                res["has_more"] = res["has_more"] if isinstance(res["has_more"], bool) else int(res["has_more"]) == 1
            else:
                res["has_more"] = False
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@douyin_bp.route("/open-parent", methods=["POST"])
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
                subprocess.run(["explorer", f"/select,{path}"])
            else:
                subprocess.run(["xdg-open", str(path.parent)])
        else:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)])
            elif sys.platform == "win32":
                subprocess.run(["explorer", str(path)])
            else:
                subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500

