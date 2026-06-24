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
    history = load_json(HISTORY_FILE, [])
    history.insert(0, {
        "title": title,
        "type": item_type,
        "path": str(file_path),
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
                "aid": "1128",
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
                "aid": "1128",
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
        if images and isinstance(images, list) and len(images) > 0:
            # 图文类型
            urls = []
            for img in images:
                url_list = img.get("url_list") or []
                if url_list:
                    # 取最后一个（通常是最高清）
                    urls.append(url_list[-1] if len(url_list) > 1 else url_list[0])
            return {
                "type": "image",
                "title": title,
                "urls": urls,
                "aweme_id": aweme_id,
                "nickname": nickname,
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


def download_media(media_info: dict, target_dir: Path) -> dict:
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

    user_dir = target_dir / nickname
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
        add_history_item(title, "图文", folder_path, total_bytes)

    return {
        "id": aweme_id,
        "title": title,
        "type": item_type,
        "size_bytes": total_bytes,
    }


# ── 后台批处理执行器 ──────────────────────────────────────

def _run_profile_download_task(sec_uid: str, max_pages: int, target_dir: Path):
    """后台线程：使用 API 获取用户所有发布作品并下载"""
    _add_log(f"正在准备抓取主页资源，目标 sec_uid: {sec_uid[:20]}...")

    client = DouyinClient()

    try:
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
                result = download_media(media_info, target_dir)
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
                result = download_media(media_info, target_dir)
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
        _set_task_state(status="completed")

    except Exception as outer_err:
        _add_log(f"💥 批量下载点赞任务发生错误: {str(outer_err)}")
        _set_task_state(status="failed")


def _run_batch_items_download_task(items: list, target_dir: Path):
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
            result = download_media(media_info, target_dir)
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
            "data": result
        })

    except Exception as e:
        return jsonify({"error": f"下载失败: {str(e)}"}), 500


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

    if not items:
        return jsonify({"error": "没有选中任何视频"}), 400

    ensure_douyin_dirs()

    _set_task_state(status="running")
    _task_cancel_event.clear()
    thread = threading.Thread(
        target=_run_batch_items_download_task,
        args=(items, DOUYIN_DIR),
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
    """清除抖音下载历史记录"""
    save_json(HISTORY_FILE, [])
    return jsonify({"message": "历史记录已清空"})


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
        res = api.search_user(keyword, offset)
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
            if "has_more" in res:
                res["has_more"] = res["has_more"] if isinstance(res["has_more"], bool) else int(res["has_more"]) == 1
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
            if "has_more" in res:
                res["has_more"] = res["has_more"] if isinstance(res["has_more"], bool) else int(res["has_more"]) == 1
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
            if "has_more" in res:
                res["has_more"] = res["has_more"] if isinstance(res["has_more"], bool) else int(res["has_more"]) == 1
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

