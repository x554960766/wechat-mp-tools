import os
import re
import json
import time
import random
import string
import threading
import subprocess
import urllib.parse
from pathlib import Path
from flask import Blueprint, jsonify, request
import requests
import xml.etree.ElementTree as ET

from backend.config import (
    DATA_DIR, get_settings, get_proxy_config, get_proxy_url,
    get_proxies_dict, report_proxy_status, load_json, save_json
)
from backend.bilibili_sign import sign_wbi

bilibili_bp = Blueprint("bilibili", __name__, url_prefix="/api/bilibili")

# Bilibili download directories
BILI_DIR = DATA_DIR / "bilibili_downloads"
HISTORY_FILE = DATA_DIR / "bilibili_history.json"
ACCOUNTS_FILE = DATA_DIR / "bilibili_accounts.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

REFERER = "https://www.bilibili.com"

# Global task state management (Thread safe)
_task_state = {
    "status": "idle",          # idle / running / completed / failed / cancelled
    "total": 0,
    "current_index": 0,
    "current_title": "",
    "logs": [],
    "downloaded_count": 0,
    "failed_count": 0,
    "current_percent": 0,
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
                     current_title: str = None, downloaded_count: int = None, failed_count: int = None,
                     current_percent: int = None):
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
        if current_percent is not None:
            _task_state["current_percent"] = current_percent

def _reset_task_state(total: int = 0):
    with _task_lock:
        _task_state["status"] = "running"
        _task_state["total"] = total
        _task_state["current_index"] = 0
        _task_state["current_title"] = ""
        _task_state["logs"] = []
        _task_state["downloaded_count"] = 0
        _task_state["failed_count"] = 0
        _task_state["current_percent"] = 0
    _add_log(f"任务启动，共计需要下载 {total} 项内容")

def ensure_bili_dirs():
    BILI_DIR.mkdir(parents=True, exist_ok=True)

def clean_filename(filename: str) -> str:
    filename = re.sub(r'[\\/:*?"<>|\n\r\t]', "", filename)
    filename = filename.strip().replace(" ", "_")
    return filename[:80] if filename else "untitled"

def add_history_item(title: str, item_type: str, file_path: str, size_bytes: int, bvid: str = ""):
    source = ""
    path_str = str(file_path)
    marker = "bilibili_downloads/"
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
        "bvid": bvid,
        "size": f"{size_bytes / (1024 * 1024):.2f} MB" if size_bytes else "未知",
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    save_json(HISTORY_FILE, history[:150])


# ── B站 Danmaku to ASS Converter ──────────────────────────────────────

def xml_to_ass(xml_content: str, output_path: str):
    """Converts Bilibili XML danmaku to ASS subtitle file"""
    try:
        root = ET.fromstring(xml_content)
    except Exception as e:
        _add_log(f"解析弹幕 XML 失败: {e}")
        return
        
    ass_header = (
        "[Script Info]\n"
        "Title: Bilibili Danmaku\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "Timer: 100.0000\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,28,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,8,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    
    events = []
    # 20 tracks to distribute scrolls
    tracks = [0.0] * 20
    
    for d in root.findall('d'):
        p_str = d.get('p', '')
        text = d.text or ''
        parts = p_str.split(',')
        if len(parts) < 4 or not text:
            continue
            
        try:
            start_time = float(parts[0])
            mode = int(parts[1])
            size = int(parts[2])
            color_dec = int(parts[3])
        except ValueError:
            continue
            
        # Convert decimal RGB to ASS hex (&H00BBGGRR)
        r = (color_dec >> 16) & 0xFF
        g = (color_dec >> 8) & 0xFF
        b = color_dec & 0xFF
        color_hex = f"&H00{b:02X}{g:02X}{r:02X}"
        
        duration = 7.0
        end_time = start_time + duration
        
        def format_time(t):
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = int(t % 60)
            cs = int(round((t - int(t)) * 100))
            if cs >= 100:
                cs = 99
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
            
        start_str = format_time(start_time)
        end_str = format_time(end_time)
        
        # Escape brackets
        text = text.replace('{', '｛').replace('}', '｝')
        
        if mode == 1:  # Scroll left
            selected_track = 0
            for i, last_t in enumerate(tracks):
                if start_time >= last_t:
                    selected_track = i
                    break
            else:
                selected_track = int(start_time) % len(tracks)
                
            tracks[selected_track] = start_time + 4.5  # buffer time
            y_pos = 50 + selected_track * 45
            effect_str = f"\\move(1920,{y_pos},-600,{y_pos})"
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{{effect_str}\\c{color_hex}}}{text}")
        elif mode == 4:  # bottom
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{\\an2\\c{color_hex}}}{text}")
        elif mode == 5:  # top
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{\\an8\\c{color_hex}}}{text}")
        else:
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{\\c{color_hex}}}{text}")
            
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write("\n".join(events))
    except Exception as e:
        _add_log(f"写入 ASS 弹幕文件失败: {e}")


# ── B站 Subtitles JSON to SRT Converter ──────────────────────────────────

def json_to_srt(json_content: str, output_path: str):
    """Converts B站 JSON subtitles format to SRT"""
    try:
        data = json.loads(json_content)
    except Exception as e:
        _add_log(f"解析字幕 JSON 失败: {e}")
        return
        
    body = data.get("body", [])
    lines = []
    
    def format_time_srt(t):
        s = int(t)
        ms = int(round((t - s) * 1000))
        if ms >= 1000:
            ms = 999
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
        
    for idx, item in enumerate(body):
        start = item.get("from", 0.0)
        end = item.get("to", 0.0)
        content = item.get("content", "")
        
        start_str = format_time_srt(start)
        end_str = format_time_srt(end)
        
        lines.append(f"{idx + 1}")
        lines.append(f"{start_str} --> {end_str}")
        lines.append(content)
        lines.append("")
        
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        _add_log(f"写入 SRT 字幕文件失败: {e}")


# ── Bilibili API 请求客户端 ──────────────────────────────────

class BilibiliClient:
    def __init__(self):
        self.session = requests.Session()
        settings = get_settings()
        cookie = settings.get("bilibili_cookie", "").strip()
        
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": REFERER,
            "Origin": REFERER
        }
        if cookie:
            headers["Cookie"] = cookie
            
        self.session.headers.update(headers)
        
        proxies = get_proxies_dict()
        if proxies:
            self.session.proxies.update(proxies)

    def extract_bvid_or_avid(self, text: str) -> tuple[str, str]:
        """Extract bvid or avid from url/string. Returns ('bvid', id) or ('avid', id) or (None, None)"""
        # Match BV
        bv_match = re.search(r"(BV[A-Za-z0-9]{10})", text, re.IGNORECASE)
        if bv_match:
            return "bvid", bv_match.group(1)
            
        # Match av
        av_match = re.search(r"av(\d+)", text, re.IGNORECASE)
        if av_match:
            return "aid", av_match.group(1)
            
        # Match bare aid
        if text.isdigit():
            return "aid", text
            
        return None, None

    def get_video_detail(self, identifier_type: str, val: str) -> dict:
        """Call view API to get video details"""
        url = "https://api.bilibili.com/x/web-interface/view"
        params = {}
        if identifier_type == "bvid":
            params["bvid"] = val
        else:
            params["aid"] = val
            
        resp = self.session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        res_json = resp.json()
        if res_json.get("code") != 0:
            raise Exception(f"B站API接口返回异常: {res_json.get('message')} (code: {res_json.get('code')})")
        return res_json.get("data", {})

    def get_playurl(self, bvid: str, cid: str, qn: int = 80, is_bangumi: bool = False) -> tuple[list[str], list[str]]:
        """Call playurl API to retrieve DASH video and audio mirrors (supports UGC and PGC)"""
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": REFERER
        }
        
        if is_bangumi:
            url = "https://api.bilibili.com/pgc/player/web/v2/playurl"
            params = {
                "bvid": bvid,
                "cid": cid,
                "qn": str(qn),
                "fnver": "0",
                "fnval": "4048",
                "fourk": "1",
                "support_multi_audio": "true",
                "from_client": "BROWSER"
            }
            resp = self.session.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            res_json = resp.json()
            
            if res_json.get("code") != 0:
                raise Exception(f"获取番剧播放地址失败: {res_json.get('message')}")
                
            dash_data = res_json.get("result", {}).get("video_info", {}).get("dash")
            if not dash_data:
                raise Exception("该番剧视频不支持 DASH 格式。")
        else:
            url = "https://api.bilibili.com/x/player/playurl"
            params = {
                "bvid": bvid,
                "cid": cid,
                "qn": str(qn),
                "fnver": "0",
                "fnval": "4048",
                "fourk": "1",
                "otype": "json"
            }
            resp = self.session.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            res_json = resp.json()
            
            if res_json.get("code") != 0:
                raise Exception(f"获取播放地址失败: {res_json.get('message')}")
                
            dash_data = res_json.get("data", {}).get("dash")
            if not dash_data:
                # Fallback to durl if DASH is not supported
                durl = res_json.get("data", {}).get("durl", [])
                if durl:
                    urls = [item.get("url") for item in durl if item.get("url")]
                    return urls, []
                raise Exception("该视频不支持 DASH 格式，且未找到 FLV 播放地址。")
            
        # Extract Video & Audio base URLs
        video_list = dash_data.get("video", [])
        audio_list = dash_data.get("audio", [])
        
        # Sort video list by qn descending and choose best
        video_urls = []
        if video_list:
            # Filter by matching quality or closest
            best_video = video_list[0] # defaults to first
            video_urls = [best_video.get("base_url")] + (best_video.get("backup_url") or [])
            
        audio_urls = []
        if audio_list:
            best_audio = audio_list[0]
            audio_urls = [best_audio.get("base_url")] + (best_audio.get("backup_url") or [])
            
        return video_urls, audio_urls

    def get_bangumi_season_by_ep_id(self, ep_id: str) -> str:
        """Get season_id by episode_id"""
        url = f"https://api.bilibili.com/pgc/view/web/season?ep_id={ep_id}"
        resp = self.session.get(url, timeout=10)
        resp.raise_for_status()
        res_json = resp.json()
        if res_json.get("code") != 0:
            raise Exception(f"获取番剧信息失败: {res_json.get('message')}")
        return str(res_json.get("result", {}).get("season_id", ""))

    def get_bangumi_season_by_media_id(self, media_id: str) -> str:
        """Get season_id by media_id"""
        url = f"https://api.bilibili.com/pgc/review/user?media_id={media_id}"
        resp = self.session.get(url, timeout=10)
        resp.raise_for_status()
        res_json = resp.json()
        if res_json.get("code") != 0:
            raise Exception(f"获取番剧媒体信息失败: {res_json.get('message')}")
        return str(res_json.get("result", {}).get("media", {}).get("season_id", ""))

    def get_bangumi_list(self, season_id: str) -> dict:
        """Get list of episodes in a season"""
        url = f"https://api.bilibili.com/pgc/view/web/season?season_id={season_id}"
        resp = self.session.get(url, timeout=10)
        resp.raise_for_status()
        res_json = resp.json()
        if res_json.get("code") != 0:
            raise Exception(f"获取番剧列表失败: {res_json.get('message')}")
        result = res_json.get("result", {})
        title = result.get("title", "")
        
        section_episodes = []
        for section in result.get("section", []):
            if section.get("type") != 5:
                section_episodes.extend(section.get("episodes", []))
                
        episodes = []
        for idx, item in enumerate(result.get("episodes", []) + section_episodes):
            episodes.append({
                "id": str(item.get("id", "")),
                "title": f"{item.get('title', '')} {item.get('long_title', '')}".strip() or f"第{idx+1}话",
                "bvid": item.get("bvid", ""),
                "cid": str(item.get("cid", ""))
            })
            
        return {
            "title": title,
            "episodes": episodes
        }

    def get_collection_details(self, series_id: str, mid: str) -> dict:
        """Get series/collection video details (paginated space seasons list)"""
        import math
        # Get collection title
        title_url = f"https://api.bilibili.com/x/v1/medialist/info?type=8&biz_id={series_id}"
        resp = self.session.get(title_url, timeout=10)
        resp.raise_for_status()
        title_json = resp.json()
        title = title_json.get("data", {}).get("title", "未命名合集")
        
        # Paginate videos
        api = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
        ps = 30
        pn = 1
        total_pages = 1
        videos = []
        
        while pn <= total_pages:
            params = {
                "mid": mid,
                "season_id": series_id,
                "sort_reverse": "false",
                "page_num": str(pn),
                "page_size": str(ps)
            }
            resp = self.session.get(api, params=params, timeout=10)
            resp.raise_for_status()
            res_json = resp.json()
            if res_json.get("code") != 0:
                raise Exception(f"获取合集视频失败: {res_json.get('message')}")
            
            data = res_json.get("data", {})
            archives = data.get("archives", [])
            for item in archives:
                videos.append({
                    "bvid": item.get("bvid", ""),
                    "title": item.get("title", "")
                })
            
            total_count = data.get("page", {}).get("total", 0)
            total_pages = math.ceil(total_count / ps)
            pn += 1
            if not archives or pn > 10:
                break
                
        return {
            "title": title,
            "videos": videos
        }

    def get_favorite_details(self, fid: str) -> dict:
        """Get favorite folder video details (paginated list all normal items)"""
        # Get folder title
        info_url = f"https://api.bilibili.com/x/v3/fav/folder/info?media_id={fid}"
        resp = self.session.get(info_url, timeout=10)
        resp.raise_for_status()
        info_json = resp.json()
        title = info_json.get("data", {}).get("title", "未命名收藏夹")
        
        # Paginate items
        api = "https://api.bilibili.com/x/v3/fav/resource/list"
        ps = 20
        pn = 1
        videos = []
        
        while True:
            params = {
                "media_id": fid,
                "pn": str(pn),
                "ps": str(ps),
                "platform": "web"
            }
            resp = self.session.get(api, params=params, timeout=10)
            resp.raise_for_status()
            res_json = resp.json()
            if res_json.get("code") != 0:
                raise Exception(f"获取收藏夹视频失败: {res_json.get('message')}")
                
            data = res_json.get("data", {})
            medias = data.get("medias") or []
            for media in medias:
                if media.get("type") == 2 and media.get("bvid"):
                    videos.append({
                        "bvid": media.get("bvid"),
                        "title": media.get("title", ""),
                        "page": int(media.get("page", 1))
                    })
            
            has_more = data.get("has_more")
            if has_more is not None:
                if not has_more:
                    break
            elif len(medias) < ps:
                break
                
            pn += 1
            if pn > 20:
                break
                
        return {
            "title": title,
            "videos": videos
        }

    def get_user_detail(self, mid: str) -> dict:
        """Fetch host profile (WBI signed)"""
        params = {"mid": mid}
        signed = sign_wbi(params, self.session.headers.get("Cookie", ""), self.session.proxies)
        url = "https://api.bilibili.com/x/space/wbi/acc/info"
        resp = self.session.get(url, params=signed, timeout=10)
        resp.raise_for_status()
        res_json = resp.json()
        if res_json.get("code") != 0:
            raise Exception(f"获取UP主详情失败: {res_json.get('message')}")
        return res_json.get("data", {})

    def get_user_videos(self, mid: str, page: int = 1, page_size: int = 30) -> tuple[list[dict], int]:
        """Fetch space upload videos list (WBI signed)"""
        params = {
            "mid": mid,
            "ps": str(page_size),
            "pn": str(page),
            "order": "pubdate",
            "tid": "0"
        }
        signed = sign_wbi(params, self.session.headers.get("Cookie", ""), self.session.proxies)
        url = "https://api.bilibili.com/x/space/wbi/arc/search"
        resp = self.session.get(url, params=signed, timeout=10)
        resp.raise_for_status()
        res_json = resp.json()
        if res_json.get("code") != 0:
            raise Exception(f"获取投稿视频失败: {res_json.get('message')}")
            
        data = res_json.get("data", {})
        vlist = data.get("list", {}).get("vlist", [])
        total = data.get("page", {}).get("count", 0)
        return vlist, total

    def download_file(self, urls: list[str], dest_path: Path, progress_callback=None) -> int:
        """Download file from a list of mirror URLs (with recovery on error and Range resumption)"""
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": REFERER
        }
        
        last_err = None
        for url in urls:
            if _task_cancel_event.is_set():
                raise Exception("Task Cancelled")
            try:
                temp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
                start_byte = 0
                if temp_path.exists():
                    start_byte = temp_path.stat().st_size
                
                req_headers = headers.copy()
                if start_byte > 0:
                    req_headers["Range"] = f"bytes={start_byte}-"
                
                with self.session.get(url, headers=req_headers, stream=True, timeout=15) as r:
                    if r.status_code == 416:
                        start_byte = 0
                        req_headers.pop("Range", None)
                        r = self.session.get(url, headers=req_headers, stream=True, timeout=15)
                    
                    r.raise_for_status()
                    
                    is_range = r.status_code == 206
                    content_len = r.headers.get('content-length')
                    content_len = int(content_len) if content_len else 0
                    
                    if is_range:
                        total_length = start_byte + content_len
                        open_mode = "ab"
                        dl = start_byte
                        _add_log(f"支持断点续传，从字节 {start_byte} 恢复下载...")
                    else:
                        total_length = content_len
                        open_mode = "wb"
                        dl = 0
                        start_byte = 0
                    
                    with open(temp_path, open_mode) as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if _task_cancel_event.is_set():
                                raise Exception("Task Cancelled")
                            if chunk:
                                f.write(chunk)
                                dl += len(chunk)
                                if progress_callback and total_length:
                                    progress_callback(dl, total_length)
                temp_path.rename(dest_path)
                return dest_path.stat().st_size
            except Exception as e:
                last_err = e
                _add_log(f"下载源失败，尝试下一个镜像... {e}")
                # Only clean up if we did NOT do range/resume, or if the error is terminal
                # To be safe, if Range request fails, next retry will clean it if we reset start_byte
                if dest_path.with_suffix(dest_path.suffix + ".tmp").exists() and start_byte == 0:
                    dest_path.with_suffix(dest_path.suffix + ".tmp").unlink()
                time.sleep(1)
                
        raise Exception(f"所有下载镜像均失败。最后错误: {last_err}")


# ── Downloader Engine Core ──────────────────────────────────

def download_video_item(client: BilibiliClient, bvid: str, page_num: int = 1, target_dir: Path = BILI_DIR,
                        is_bangumi: bool = False, cid: str = None, p_title: str = None, bangumi_title: str = None,
                        quality: str = None) -> dict:
    """Download details, streams, danmakus, subtitles and merges them with FFmpeg"""
    # 1. Fetch details
    if is_bangumi:
        title = bangumi_title or "番剧视频"
        up_name = "番剧"
        cid = cid
        p_title = p_title or f"第{page_num}话"
        pages = []
        clean_title = clean_filename(title)
        video_folder = target_dir / up_name / f"{clean_title}_{bvid}"
        video_folder.mkdir(parents=True, exist_ok=True)
        full_title = f"{clean_title}_{clean_filename(p_title)}"
    else:
        detail = client.get_video_detail("bvid", bvid)
        title = detail.get("title", "未命名视频")
        up_name = clean_filename(detail.get("owner", {}).get("name", "未知UP主"))
        
        pages = detail.get("pages", [])
        if not pages:
            raise Exception("未找到可用的分P视频信息")
            
        page_info = pages[0]
        if page_num <= len(pages):
            page_info = pages[page_num - 1]
            
        cid = str(page_info.get("cid"))
        p_title = page_info.get("part", "")
        
        # Format folder structure: bilibili_downloads/<UP主>/<视频标题>_BVxxx/
        clean_title = clean_filename(title)
        video_folder = target_dir / up_name / f"{clean_title}_{bvid}"
        video_folder.mkdir(parents=True, exist_ok=True)
        
        # Save final file filename
        if len(pages) > 1:
            # Multiple parts filename
            full_title = f"{clean_title}_P{page_num:02d}_{clean_filename(p_title)}"
        else:
            full_title = clean_title
        
    final_output_path = video_folder / f"{full_title}.mp4"
    
    # Skip if already exists
    if final_output_path.exists():
        _add_log(f"视频已存在，跳过下载: {full_title}")
        return {
            "title": full_title,
            "path": str(final_output_path),
            "size": final_output_path.stat().st_size,
            "skipped": True
        }
        
    # Get settings configurations (override with parameter if provided)
    settings = get_settings()
    selected_quality = quality or settings.get("bili_video_quality") or "1080p"
    
    qn = 80  # Default 1080P
    if selected_quality == "vip":
        qn = 120
    elif selected_quality == "720p":
        qn = 64
    elif selected_quality == "360p":
        qn = 16
        
    download_danmaku = settings.get("bili_download_danmaku", True)
    download_subtitle = settings.get("bili_download_subtitle", True)
    
    # 2. Get stream URLs
    _add_log(f"获取 {full_title} DASH 媒体流 URL中...")
    video_urls, audio_urls = client.get_playurl(bvid, cid, qn, is_bangumi=is_bangumi)
    
    # Paths for temporary video and audio chunks
    temp_video_m4s = video_folder / f"{full_title}_video.m4s"
    temp_audio_m4s = video_folder / f"{full_title}_audio.m4s"
    
    video_size = 0
    audio_size = 0
    
    # 3. Download streams
    def video_progress(dl, total):
        pct = int((dl / total) * 75)
        _set_task_state(current_percent=pct)

    def audio_progress(dl, total):
        pct = 75 + int((dl / total) * 20)
        _set_task_state(current_percent=pct)

    _set_task_state(current_percent=0)

    if video_urls:
        _add_log(f"下载视频轨 {full_title} ...")
        video_size = client.download_file(video_urls, temp_video_m4s, progress_callback=video_progress)
        
    _set_task_state(current_percent=75)

    if audio_urls:
        _add_log(f"下载音频轨 {full_title} ...")
        audio_size = client.download_file(audio_urls, temp_audio_m4s, progress_callback=audio_progress)
        
    # 4. Merge via FFmpeg
    merged = False
    if temp_video_m4s.exists() and temp_audio_m4s.exists():
        _add_log(f"开始使用 FFmpeg 合并 {full_title} ...")
        _set_task_state(current_percent=95)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(temp_video_m4s),
            "-i", str(temp_audio_m4s),
            "-c:v", "copy",
            "-c:a", "copy",
            "-strict", "unofficial",
            str(final_output_path)
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if res.returncode == 0 and final_output_path.exists():
                merged = True
                _set_task_state(current_percent=100)
                _add_log(f"音视频合并成功!")
                # Cleanup m4s
                temp_video_m4s.unlink()
                temp_audio_m4s.unlink()
            else:
                _add_log(f"FFmpeg 合并失败: {res.stderr.decode('utf-8', errors='ignore')}")
        except subprocess.TimeoutExpired:
            _add_log("FFmpeg 合并超时!")
            
    # Fallback to rename video chunk to mp4 if audio didn't exist or merging failed
    if not merged:
        if temp_video_m4s.exists():
            temp_video_m4s.rename(final_output_path)
            if temp_audio_m4s.exists():
                temp_audio_m4s.unlink()
            _add_log("未成功合并音频，保留纯视频轨。")
        elif temp_audio_m4s.exists():
            temp_audio_m4s.rename(final_output_path.with_suffix(".m4a"))
            _add_log("未成功下载视频，保留纯音频轨。")
            final_output_path = final_output_path.with_suffix(".m4a")

    # 5. Fetch and Save Danmaku (XML -> ASS)
    if download_danmaku:
        try:
            _add_log("获取并生成 ASS 弹幕...")
            # B站 list.so returns normal protobuf/xml danmaku
            dm_url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
            dm_resp = client.session.get(dm_url, timeout=10)
            if dm_resp.status_code == 200:
                ass_path = final_output_path.with_suffix(".ass")
                xml_to_ass(dm_resp.content, str(ass_path))
        except Exception as e:
            _add_log(f"获取弹幕异常: {e}")
            
    # 6. Fetch and Save Subtitles (JSON -> SRT)
    if download_subtitle:
        try:
            if is_bangumi:
                try:
                    sub_url = f"https://api.bilibili.com/x/player/wbi/v2?bvid={bvid}&cid={cid}"
                    sub_info_resp = client.session.get(sub_url, timeout=10)
                    if sub_info_resp.status_code == 200:
                        sub_data = sub_info_resp.json().get("data", {})
                        subtitles_list = sub_data.get("subtitle", {}).get("subtitles", [])
                    else:
                        subtitles_list = []
                except Exception:
                    subtitles_list = []
            else:
                subtitles_list = detail.get("subtitle", {}).get("subtitles", [])
                
            if subtitles_list:
                _add_log("获取并生成 SRT 字幕...")
                # Choose first subtitle
                sub_lang = subtitles_list[0]
                sub_url = sub_lang.get("subtitle_url")
                if sub_url:
                    if sub_url.startswith("//"):
                        sub_url = "https:" + sub_url
                    sub_resp = client.session.get(sub_url, timeout=10)
                    if sub_resp.status_code == 200:
                        srt_path = final_output_path.with_suffix(f".{sub_lang.get('lan', 'zh-CN')}.srt")
                        json_to_srt(sub_resp.text, str(srt_path))
        except Exception as e:
            _add_log(f"获取字幕异常: {e}")
            
    # 7. Save poster image
    try:
        pic_url = detail.get("pic") if not is_bangumi else None
        if pic_url:
            if pic_url.startswith("//"):
                pic_url = "https:" + pic_url
            pic_resp = client.session.get(pic_url, timeout=10)
            if pic_resp.status_code == 200:
                poster_path = final_output_path.parent / f"{final_output_path.stem}-poster.jpg"
                with open(poster_path, "wb") as f:
                    f.write(pic_resp.content)
    except Exception as e:
        pass
        
    size_bytes = final_output_path.stat().st_size if final_output_path.exists() else (video_size + audio_size)
    add_history_item(full_title, "video", final_output_path, size_bytes, bvid)
    
    return {
        "title": full_title,
        "path": str(final_output_path),
        "size": size_bytes,
        "success": True
    }


def _run_batch_download_thread(items: list, target_dir: Path, quality: str = None):
    """Execution thread for batch download tasks"""
    global _task_cancel_event
    _reset_task_state(len(items))
    
    client = BilibiliClient()
    
    downloaded = 0
    failed = 0
    
    for idx, item in enumerate(items):
        if _task_cancel_event.is_set():
            _add_log("任务已取消。")
            _set_task_state(status="cancelled")
            return
            
        bvid = item.get("bvid")
        page_num = item.get("page_num", 1)
        title = item.get("title", f"BV {bvid}")
        is_bangumi = item.get("is_bangumi", False)
        cid = item.get("cid")
        p_title = item.get("p_title")
        bangumi_title = item.get("bangumi_title")
        
        _set_task_state(current_index=idx + 1, current_title=title)
        _add_log(f"正在下载第 {idx + 1}/{len(items)} 个视频: {title}")
        
        try:
            res = download_video_item(client, bvid, page_num, target_dir,
                                      is_bangumi=is_bangumi, cid=cid, p_title=p_title, bangumi_title=bangumi_title,
                                      quality=quality)
            downloaded += 1
            _set_task_state(downloaded_count=downloaded)
            # Sleep to prevent rate limit
            time.sleep(random.uniform(1.5, 3.0))
        except Exception as e:
            failed += 1
            _add_log(f"下载 {title} 失败: {e}")
            _set_task_state(failed_count=failed)
            time.sleep(random.uniform(2.0, 4.0))
            
    if failed == len(items):
        _set_task_state(status="failed")
        _add_log("所有下载任务均失败。")
    elif _task_cancel_event.is_set():
        _set_task_state(status="cancelled")
    else:
        _set_task_state(status="completed")
        _add_log("全部下载任务执行完毕。")


# ── Blueprint API 路由 ────────────────────────────────────

@bilibili_bp.route("/detect-url", methods=["POST"])
def detect_url():
    """Identify type of input Bilibili url"""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "请输入有效的链接"}), 400
        
    ensure_bili_dirs()
    
    try:
        client = BilibiliClient()
        
        # 1. Check Bangumi (番剧)
        ep_match = re.search(r"bangumi/play/ep(\d+)", url)
        ss_match = re.search(r"bangumi/play/ss(\d+)", url)
        md_match = re.search(r"bangumi/media/md(\d+)", url)
        
        if ep_match or ss_match or md_match:
            season_id = ""
            if ep_match:
                season_id = client.get_bangumi_season_by_ep_id(ep_match.group(1))
            elif md_match:
                season_id = client.get_bangumi_season_by_media_id(md_match.group(1))
            else:
                season_id = ss_match.group(1)
                
            res = client.get_bangumi_list(season_id)
            pages = []
            for idx, item in enumerate(res["episodes"]):
                pages.append({
                    "page": idx + 1,
                    "part": item["title"],
                    "bvid": item["bvid"],
                    "cid": item["cid"],
                    "is_bangumi": True,
                    "bangumi_title": res["title"]
                })
                
            return jsonify({
                "type": "bangumi",
                "id": season_id,
                "title": res["title"],
                "cover": "",
                "author": "番剧",
                "page_count": len(pages),
                "pages": pages,
                "message": f"番剧 「{res['title']}」"
            })
            
        # 2. Check Collection / Series (合集/视频列表)
        coll_match = (re.search(r"lists/(\d+)\?type=season", url) or
                      re.search(r"channel/collectiondetail\?.*sid=(\d+)", url) or
                      re.search(r"channel/seriesdetail\?.*sid=(\d+)", url) or
                      re.search(r"favlist\?.*fid=(\d+)&ftype=collect", url))
                      
        if coll_match:
            series_id = coll_match.group(1)
            mid_match = re.search(r"space\.bilibili\.com/(\d+)", url)
            if not mid_match:
                return jsonify({"error": "解析合集失败: 链接中未找到UP主UID(space.bilibili.com/{uid})"}), 400
            mid = mid_match.group(1)
            
            res = client.get_collection_details(series_id, mid)
            pages = []
            for idx, item in enumerate(res["videos"]):
                pages.append({
                    "page": idx + 1,
                    "part": item["title"],
                    "bvid": item["bvid"],
                    "is_bangumi": False
                })
                
            return jsonify({
                "type": "playlist",
                "id": series_id,
                "title": res["title"],
                "cover": "",
                "author": f"合集(UID: {mid})",
                "page_count": len(pages),
                "pages": pages,
                "message": f"合集 「{res['title']}」"
            })

        # 3. Check Favorites (收藏夹)
        fav_match = (re.search(r"favlist\?fid=(\d+)", url) or
                     re.search(r"medialist/play/ml(\d+)", url))
                     
        if fav_match:
            fid = fav_match.group(1)
            res = client.get_favorite_details(fid)
            pages = []
            for idx, item in enumerate(res["videos"]):
                pages.append({
                    "page": item.get("page", 1),
                    "part": item["title"],
                    "bvid": item["bvid"],
                    "is_bangumi": False
                })
                
            return jsonify({
                "type": "playlist",
                "id": fid,
                "title": res["title"],
                "cover": "",
                "author": "收藏夹",
                "page_count": len(pages),
                "pages": pages,
                "message": f"收藏夹 「{res['title']}」"
            })

        # 4. Check space / creator profile
        space_match = re.search(r"space\.bilibili\.com/(\d+)", url)
        if space_match:
            if "favlist" not in url and "lists" not in url and "channel" not in url:
                mid = space_match.group(1)
                up_name = "未知UP主"
                avatar = ""
                desc = ""
                try:
                    up_info = client.get_user_detail(mid)
                    up_name = up_info.get("name", "未知UP主")
                    avatar = up_info.get("face", "")
                    desc = up_info.get("sign", "")
                except Exception:
                    pass
                return jsonify({
                    "type": "space",
                    "id": mid,
                    "nickname": up_name,
                    "avatar": avatar,
                    "desc": desc,
                    "message": f"UP主 「{up_name}」 的个人空间"
                })
            
        # 5. Check regular video BV/av
        id_type, val = client.extract_bvid_or_avid(url)
        if id_type:
            detail = client.get_video_detail(id_type, val)
            bvid = detail.get("bvid")
            title = detail.get("title")
            pic = detail.get("pic")
            owner = detail.get("owner", {}).get("name", "")
            pages = detail.get("pages", [])
            
            return jsonify({
                "type": "single",
                "id": bvid,
                "title": title,
                "cover": pic,
                "author": owner,
                "page_count": len(pages),
                "pages": [{"page": p.get("page"), "part": p.get("part"), "cid": p.get("cid")} for p in pages],
                "message": f"视频 「{title}」"
            })
            
        return jsonify({"error": "无法识别此 B站 链接，请检查输入"}), 400
    except Exception as e:
        return jsonify({"error": f"链接检测失败: {str(e)}"}), 500


@bilibili_bp.route("/download-single", methods=["POST"])
def download_single():
    """Parse and download single video (supports multiple pages)"""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    selected_pages = data.get("pages")  # Expect a list of page indices, e.g. [1, 2]
    quality = data.get("quality")
    
    if not url:
        return jsonify({"error": "请输入有效的链接"}), 400
        
    ensure_bili_dirs()
    
    try:
        client = BilibiliClient()
        id_type, val = client.extract_bvid_or_avid(url)
        if not id_type:
            return jsonify({"error": "无法解析视频 ID，请填写正确的BV/AV号或网页链接"}), 400
            
        detail = client.get_video_detail(id_type, val)
        bvid = detail.get("bvid")
        title = detail.get("title")
        pages = detail.get("pages", [])
        
        # Build queue
        download_queue = []
        if selected_pages:
            for p in selected_pages:
                if p <= len(pages):
                    download_queue.append({
                        "bvid": bvid,
                        "page_num": p,
                        "title": f"{title} (P{p})"
                    })
        else:
            # Download page 1 by default
            download_queue.append({
                "bvid": bvid,
                "page_num": 1,
                "title": title
            })
            
        if _task_state["status"] == "running":
            return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400
            
        global _task_cancel_event
        _task_cancel_event.clear()
        
        thread = threading.Thread(
            target=_run_batch_download_thread,
            args=(download_queue, BILI_DIR, quality)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "message": "下载已启动",
            "task_started": True
        })
        
    except Exception as e:
        return jsonify({"error": f"下载失败: {str(e)}"}), 500


@bilibili_bp.route("/accounts", methods=["GET"])
def get_accounts():
    """Get list of subscribed hosts"""
    accounts = load_json(ACCOUNTS_FILE, [])
    return jsonify({"accounts": accounts, "total": len(accounts)})


@bilibili_bp.route("/accounts/parse", methods=["POST"])
def parse_account():
    """Parse host main page before subscribing"""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "请输入UP主主页链接"}), 400
        
    # Match mid
    mid_match = re.search(r"space\.bilibili\.com/(\d+)", url)
    mid = mid_match.group(1) if mid_match else url.strip()
    
    if not mid.isdigit():
        return jsonify({"error": "无法解析MID，请填写正确的个人空间地址或MID"}), 400
        
    try:
        client = BilibiliClient()
        up_info = client.get_user_detail(mid)
        
        return jsonify({
            "mid": mid,
            "nickname": up_info.get("name", ""),
            "avatar": up_info.get("face", ""),
            "desc": up_info.get("sign", ""),
            "url": f"https://space.bilibili.com/{mid}"
        })
    except Exception as e:
        return jsonify({"error": f"解析失败: {str(e)}"}), 500


@bilibili_bp.route("/accounts", methods=["POST"])
def add_account():
    """Add host to accounts JSON list"""
    data = request.get_json() or {}
    mid = data.get("mid")
    nickname = data.get("nickname")
    avatar = data.get("avatar")
    desc = data.get("desc")
    url = data.get("url")
    
    if not mid or not nickname:
        return jsonify({"error": "缺少必要字段"}), 400
        
    accounts = load_json(ACCOUNTS_FILE, [])
    
    # Avoid duplicate
    for acc in accounts:
        if acc.get("mid") == mid:
            return jsonify({"message": "已存在收藏夹中", "account": acc})
            
    new_acc = {
        "mid": mid,
        "nickname": nickname,
        "avatar": avatar,
        "desc": desc,
        "url": url,
        "added_time": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    accounts.append(new_acc)
    save_json(ACCOUNTS_FILE, accounts)
    return jsonify({"message": "添加成功", "account": new_acc})


@bilibili_bp.route("/accounts/<mid>", methods=["DELETE"])
def remove_account(mid):
    """Remove host from accounts list"""
    accounts = load_json(ACCOUNTS_FILE, [])
    filtered = [acc for acc in accounts if acc.get("mid") != mid]
    save_json(ACCOUNTS_FILE, filtered)
    return jsonify({"message": "取消收藏成功"})


@bilibili_bp.route("/accounts/<mid>/videos", methods=["GET"])
def get_account_videos(mid):
    """Get videos published by host"""
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 30))
    
    try:
        client = BilibiliClient()
        vlist, total = client.get_user_videos(mid, page, page_size)
        return jsonify({
            "videos": vlist,
            "total": total
        })
    except Exception as e:
        return jsonify({"error": f"获取投稿失败: {str(e)}"}), 500


@bilibili_bp.route("/download-batch", methods=["POST"])
def download_batch():
    """Download a queue of BV ids in batch"""
    data = request.get_json() or {}
    items = data.get("items", [])  # Expect [{bvid: '...', title: '...', page_num: 1}]
    quality = data.get("quality")
    
    if not items:
        return jsonify({"error": "下载列表不能为空"}), 400
        
    if _task_state["status"] == "running":
        return jsonify({"error": "当前已有正在运行的批量下载任务，请等待完成"}), 400
        
    ensure_bili_dirs()
    
    global _task_cancel_event
    _task_cancel_event.clear()
    
    thread = threading.Thread(
        target=_run_batch_download_thread,
        args=(items, BILI_DIR, quality)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "message": "批量下载已启动",
        "task_started": True
    })


@bilibili_bp.route("/progress", methods=["GET"])
def get_progress():
    """Check current batch download task progress"""
    with _task_lock:
        return jsonify(_task_state)


@bilibili_bp.route("/cancel-download", methods=["POST"])
def cancel_download():
    """Cancel current batch task"""
    global _task_cancel_event
    _task_cancel_event.set()
    _add_log("正在请求取消任务...")
    return jsonify({"message": "已发送取消请求"})


@bilibili_bp.route("/history", methods=["GET"])
def get_history():
    """List completed download history"""
    history = load_json(HISTORY_FILE, [])
    return jsonify({"history": history, "total": len(history)})


@bilibili_bp.route("/history", methods=["DELETE"])
def clear_history():
    """Clear history entries only (does not delete disk files)"""
    save_json(HISTORY_FILE, [])
    return jsonify({"message": "下载记录已清空"})


@bilibili_bp.route("/history/<int:index>", methods=["DELETE"])
def delete_history_item(index):
    """Delete a single history entry and optionally delete local files"""
    history = load_json(HISTORY_FILE, [])
    if index < 0 or index >= len(history):
        return jsonify({"error": "记录索引无效"}), 400
        
    item = history.pop(index)
    save_json(HISTORY_FILE, history)
    
    # Try deleting target folder/file on disk
    path_str = item.get("path", "")
    if path_str:
        try:
            path = Path(path_str)
            if path.exists():
                if path.is_file():
                    # Delete video file, along with poster, subtitles, danmakus in the same folder
                    folder = path.parent
                    path.unlink()
                    for f in folder.glob(f"{path.stem}*"):
                        f.unlink()
                    # Also try deleting directory if it is empty
                    try:
                        folder.rmdir()
                    except Exception:
                        pass
                elif path.is_dir():
                    import shutil
                    shutil.rmtree(path)
        except Exception as e:
            _add_log(f"删除物理文件失败: {e}")
            
    return jsonify({"message": "记录已删除"})


@bilibili_bp.route("/open-folder", methods=["POST"])
def open_folder():
    """Reveal download folder in file browser"""
    import subprocess
    import sys
    try:
        ensure_bili_dirs()
        if sys.platform == "darwin":
            subprocess.run(["open", str(BILI_DIR)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(BILI_DIR)])
        else:
            subprocess.run(["xdg-open", str(BILI_DIR)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@bilibili_bp.route("/open-file", methods=["POST"])
def open_file():
    """Play the video file using system player"""
    import subprocess
    import sys
    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400
    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件不存在"}), 404
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.run(["start", "", str(path)], shell=True)
        else:
            subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "已播放"})
    except Exception as e:
        return jsonify({"error": f"播放失败: {str(e)}"}), 500


@bilibili_bp.route("/open-parent", methods=["POST"])
def open_parent():
    """Reveal file location in file browser"""
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
