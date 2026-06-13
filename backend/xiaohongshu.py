"""
小红书视频与图文下载模块
"""

import os
import re
import time
import random
import yaml
import shutil
import threading
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, quote
from lxml import etree
from flask import Blueprint, jsonify, request

from backend.config import (
    DATA_DIR, load_json, save_json, get_settings, get_proxies_dict, report_proxy_status
)

# 签名接口依赖（可选）：用于博主笔记列表的 user_posted API（见设计文档 §7）。
# 未安装时签名路径自动禁用，get_user_profile 会降级到主页 SSR 解析。
try:
    from xhshow import Xhshow
except Exception:
    Xhshow = None

xhs_bp = Blueprint("xhs", __name__, url_prefix="/api/xhs")

# ── 存储路径 ──────────────────────────────────────────────
XHS_DIR = DATA_DIR / "xhs_downloads"
XHS_ACCOUNTS_FILE = DATA_DIR / "xhs_accounts.json"
XHS_HISTORY_FILE = DATA_DIR / "xhs_history.json"

# ── 任务管理器 ────────────────────────────────────────────
_download_tasks = {}
_download_lock = threading.Lock()

# ── 工具函数 ──────────────────────────────────────────────
def clean_filename(filename: str) -> str:
    """清理文件名，移除不支持的字符"""
    filename = re.sub(r'[\\/:*?"<>|\n\r\t]', "", filename)
    filename = filename.strip().replace(" ", "_")
    return filename[:80] if filename else "untitled"

def write_note_text(note_dir: Path, detail: dict) -> int:
    """把笔记文字内容写入 文案.txt（对应 XHS-Downloader 记录的作品标题/描述/标签/数据字段）。
    返回写入字节数；失败不抛异常（不影响媒体下载）。"""
    try:
        stats = detail.get("stats", {}) or {}
        tags = detail.get("tags", []) or []
        lines = [
            f"标题: {detail.get('title', '')}",
            f"作者: {detail.get('author', {}).get('nickname', '')}",
            f"发布时间: {detail.get('publish_time', '')}",
            f"类型: {detail.get('type', '')}",
            f"链接: {detail.get('note_url', '')}",
            f"点赞: {stats.get('liked', '')}  收藏: {stats.get('collected', '')}  "
            f"评论: {stats.get('comment', '')}  分享: {stats.get('share', '')}",
            f"标签: {' '.join('#' + t for t in tags)}",
            "",
            detail.get("desc", "") or "",
        ]
        content = "\n".join(lines)
        text_path = note_dir / "文案.txt"
        text_path.write_text(content, encoding="utf-8")
        return text_path.stat().st_size
    except Exception as e:
        print(f"写入文案失败: {e}")
        return 0

def write_note_html(note_dir: Path, detail: dict, video_file: str = None, image_items: list = None) -> int:
    """生成与原文相似、可直接双击打开的 index.html，引用本地已下载的媒体文件（离线可看）。
    返回写入字节数；失败不抛异常。"""
    try:
        from html import escape
        image_items = image_items or []
        stats = detail.get("stats", {}) or {}
        tags = detail.get("tags", []) or []
        title = escape(detail.get("title", "") or "无标题")
        author = escape(detail.get("author", {}).get("nickname", ""))
        avatar = escape(detail.get("author", {}).get("avatar", "") or "")
        ptime = escape(detail.get("publish_time", ""))
        link = escape(detail.get("note_url", ""))
        desc = escape(detail.get("desc", "") or "")

        media_html = ""
        if video_file:
            media_html += f'<video controls src="{escape(video_file)}"></video>'
        for it in image_items:
            if it.get("img"):
                media_html += f'<img src="{escape(it["img"])}" />'
            if it.get("live"):
                media_html += f'<video controls src="{escape(it["live"])}"></video>'

        tags_html = " ".join(f'<span class="tag">#{escape(t)}</span>' for t in tags)
        stats_line = (f"赞 {escape(str(stats.get('liked','')))} · 藏 {escape(str(stats.get('collected','')))}"
                      f" · 评 {escape(str(stats.get('comment','')))} · 分享 {escape(str(stats.get('share','')))}")
        avatar_html = f'<img class="avatar" src="{avatar}" referrerpolicy="no-referrer" />' if avatar else ''

        html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{margin:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;color:#333;}}
.card{{max-width:600px;margin:24px auto;background:#fff;border-radius:12px;padding:20px 24px;box-shadow:0 2px 12px rgba(0,0,0,.06);}}
.author{{display:flex;align-items:center;gap:10px;margin-bottom:16px;}}
.avatar{{width:40px;height:40px;border-radius:50%;object-fit:cover;background:#eee;}}
.name{{font-weight:600;}}
.time{{color:#999;font-size:.85rem;}}
h1{{font-size:1.25rem;margin:8px 0 12px;line-height:1.4;}}
img,video{{width:100%;border-radius:8px;margin:8px 0;display:block;}}
.desc{{white-space:pre-wrap;line-height:1.7;font-size:1rem;margin:12px 0;}}
.tags{{margin:12px 0;}}
.tag{{color:#13386c;margin-right:8px;font-size:.95rem;}}
.stats{{color:#999;font-size:.85rem;border-top:1px solid #eee;padding-top:12px;margin-top:12px;}}
.link{{font-size:.8rem;color:#bbb;word-break:break-all;margin-top:6px;}}
</style></head>
<body><div class="card">
<div class="author">{avatar_html}<div><div class="name">{author}</div><div class="time">{ptime}</div></div></div>
<h1>{title}</h1>
{media_html}
<div class="desc">{desc}</div>
<div class="tags">{tags_html}</div>
<div class="stats">{stats_line}</div>
<div class="link">原文: <a href="{link}">{link}</a></div>
</div></body></html>"""
        html_path = note_dir / "index.html"
        html_path.write_text(html_doc, encoding="utf-8")
        return html_path.stat().st_size
    except Exception as e:
        print(f"写入 HTML 失败: {e}")
        return 0

# ── 小红书 API 客户端 ─────────────────────────────────────
class XhsClient:
    USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    BASE_HEADERS = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "referer": "https://www.xiaohongshu.com/explore",
        "user-agent": USER_AGENT,
    }
    
    def get_headers(self) -> dict:
        headers = self.BASE_HEADERS.copy()
        settings = get_settings()
        cookie = settings.get("xhs_cookie", "")
        if cookie:
            headers["cookie"] = cookie.strip()
        return headers
        
    def _request(self, url: str, method: str = "GET", retries: int = None, **kwargs) -> requests.Response:
        if retries is None:
            settings = get_settings()
            retries = settings.get("max_retries", 3)
            
        headers = self.get_headers()
        if "headers" in kwargs:
            headers.update(kwargs["headers"])
        kwargs["headers"] = headers
        
        last_err = None
        for attempt in range(retries):
            # Anti-scraping delay
            time.sleep(random.uniform(1.0, 2.5))
            
            proxies = get_proxies_dict()
            proxy_url = proxies.get("http") if proxies else None
            
            try:
                resp = requests.request(method, url, proxies=proxies, timeout=20, **kwargs)
                
                if proxy_url:
                    report_proxy_status(proxy_url, success=True)
                    
                if resp.status_code in (461, 406):
                    raise ValueError("触发小红书风控限制（461/406），请在登录页更新 Cookie 或稍后再试。")
                    
                return resp
            except Exception as e:
                if proxy_url:
                    report_proxy_status(proxy_url, success=False)
                last_err = e
                print(f"Request failed (attempt {attempt + 1}/{retries}) for {url}: {e}")
                
        if last_err:
            raise last_err
        raise RuntimeError(f"请求 {url} 失败且未捕获到具体异常")

    def resolve_short_link(self, url: str) -> str:
        if not url.startswith("http"):
            url = "http://" + url
        headers = self.get_headers()
        proxies = get_proxies_dict()
        proxy_url = proxies.get("http") if proxies else None
        try:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=15, allow_redirects=True)
            if proxy_url:
                report_proxy_status(proxy_url, success=True)
            return resp.url
        except Exception as e:
            if proxy_url:
                report_proxy_status(proxy_url, success=False)
            print(f"Error resolving short link {url}: {e}")
            return url

    def extract_links(self, text: str) -> list[str]:
        words = text.split()
        results = []
        
        short_pattern = re.compile(r"(?:https?://)?xhslink\.com/[^\s\"<>\\^`{|}，。；！？、【】《》]+")
        share_pattern = re.compile(r"(?:https?://)?(?:www\.)?xiaohongshu\.com/discovery/item/\S+")
        link_pattern = re.compile(r"(?:https?://)?(?:www\.)?xiaohongshu\.com/explore/\S+")
        user_note_pattern = re.compile(r"(?:https?://)?(?:www\.)?xiaohongshu\.com/user/profile/[a-z0-9]+/\S+")
        
        for word in words:
            # Check SHORT first
            short_match = short_pattern.search(word)
            if short_match:
                matched_url = short_match.group(0)
                resolved_url = self.resolve_short_link(matched_url)
                if resolved_url:
                    if share_pattern.search(resolved_url) or link_pattern.search(resolved_url) or user_note_pattern.search(resolved_url):
                        results.append(resolved_url)
                continue
            
            # Check SHARE
            share_match = share_pattern.search(word)
            if share_match:
                url = share_match.group(0)
                if not url.startswith("http"):
                    url = "https://" + url
                results.append(url)
                continue
                
            # Check LINK
            link_match = link_pattern.search(word)
            if link_match:
                url = link_match.group(0)
                if not url.startswith("http"):
                    url = "https://" + url
                results.append(url)
                continue
                
            # Check USER_NOTE
            user_note_match = user_note_pattern.search(word)
            if user_note_match:
                url = user_note_match.group(0)
                if not url.startswith("http"):
                    url = "https://" + url
                results.append(url)
                continue
                
        return results

    def extract_note_id(self, url: str) -> str:
        # explore / discovery 链接：/explore/<id> 或 /item/<id>
        match = re.search(r"(?:explore|item)/([0-9a-zA-Z]+)", url)
        if match:
            return match.group(1)
        # 主页内笔记链接：/user/profile/<uid>/<note_id>（对齐 XHS-Downloader 的 ID_USER）
        match = re.search(r"user/profile/[0-9a-zA-Z]+/([0-9a-zA-Z]+)", url)
        if match:
            return match.group(1)
        return ""

    def get_initial_state(self, url: str) -> dict:
        resp = self._request(url)
        if resp.status_code != 200:
            raise RuntimeError(f"请求失败，HTTP 状态码: {resp.status_code}")
            
        html = resp.text
        parser = etree.HTMLParser()
        tree = etree.HTML(html, parser)
        if tree is None:
            raise ValueError("无法解析网页 HTML 结构")
            
        scripts = tree.xpath("//script/text()")
        state_text = None
        for script in reversed(scripts):
            script = script.strip()
            if script.startswith("window.__INITIAL_STATE__"):
                state_text = script
                break
                
        if not state_text:
            if "login" in resp.url or "captcha" in html or "验证码" in html or "404?source=" in resp.url or "error_code=" in resp.url:
                raise ValueError("触发风控或 Cookie 失效，请在登录页更新 Cookie。")
            raise ValueError("未在页面中找到 window.__INITIAL_STATE__，请检查链接或更新 Cookie。")
            
        eq_idx = state_text.find("=")
        if eq_idx == -1:
            raise ValueError("window.__INITIAL_STATE__ 格式不正确，缺少 '=' 赋值")
            
        state_json_str = state_text[eq_idx+1:].strip()
        if state_json_str.endswith(";"):
            state_json_str = state_json_str[:-1].strip()
            
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", state_json_str)
        
        try:
            state = yaml.safe_load(cleaned)
            if not isinstance(state, dict):
                raise ValueError("解析得到的数据类型不是 dict")
            return state
        except Exception as e:
            raise ValueError(f"解析 window.__INITIAL_STATE__ 失败: {str(e)}")

    def format_count(self, count) -> str:
        if count is None:
            return "0"
        try:
            c = int(count)
            if c >= 10000:
                return f"{round(c / 10000, 1)}万"
            return str(c)
        except Exception:
            return str(count)

    def get_note_detail(self, url: str) -> dict:
        state = self.get_initial_state(url)
        
        note = None
        if "noteData" in state:
            note = state.get("noteData", {}).get("data", {}).get("noteData")
        if not note and "note" in state:
            note_detail_map = state.get("note", {}).get("noteDetailMap", {})
            if note_detail_map and isinstance(note_detail_map, dict):
                note = list(note_detail_map.values())[-1].get("note")
                
        if not note:
            raise ValueError("触发风控或 Cookie 失效，请在登录页更新 Cookie。")
            
        note_id = note.get("noteId") or note.get("id", "")
        title = note.get("title", "").strip()
        desc = note.get("desc", "").strip()
        
        tag_list = note.get("tagList", [])
        tags = []
        if isinstance(tag_list, list):
            for tag in tag_list:
                if isinstance(tag, dict) and tag.get("name"):
                    tags.append(tag["name"])
                    
        interact = note.get("interactInfo", {})
        stats = {
            "liked": self.format_count(interact.get("likedCount")),
            "collected": self.format_count(interact.get("collectedCount")),
            "comment": self.format_count(interact.get("commentCount")),
            "share": self.format_count(interact.get("shareCount"))
        }
        
        t = note.get("time")
        if t:
            try:
                dt = datetime.fromtimestamp(float(t) / 1000)
                publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                timestamp = int(float(t) / 1000)
            except Exception:
                publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                timestamp = int(time.time())
        else:
            publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            timestamp = int(time.time())
            
        user_info = note.get("user", {})
        author_id = user_info.get("userId", "")
        author = {
            "user_id": author_id,
            "nickname": user_info.get("nickname", user_info.get("nickName", "未知博主")),
            "avatar": user_info.get("avatar", ""),
            "url": f"https://www.xiaohongshu.com/user/profile/{author_id}" if author_id else ""
        }
        
        note_type_raw = note.get("type", "normal")
        image_list = note.get("imageList", [])
        if not isinstance(image_list, list):
            image_list = []
            
        if note_type_raw == "video":
            if len(image_list) > 1:
                note_type = "图集"
            else:
                note_type = "视频"
        else:
            note_type = "图文"
            
        images = []
        lives = []
        
        settings = get_settings()
        img_fmt = settings.get("xhs_image_format", "png")
        live_enabled = settings.get("xhs_download_live", True)
        
        for img in image_list:
            if not isinstance(img, dict):
                continue
            img_url = img.get("urlDefault") or img.get("url") or ""
            if img_url:
                try:
                    img_url = bytes(img_url, "utf-8").decode("unicode_escape")
                except Exception:
                    pass
                
                parts = img_url.split("/")
                if len(parts) >= 6:
                    token = "/".join(parts[5:]).split("!")[0]
                else:
                    token = parts[-1].split("!")[0]
                    
                if img_fmt == "auto":
                    final_img_url = f"https://sns-img-bd.xhscdn.com/{token}"
                else:
                    final_img_url = f"https://ci.xiaohongshu.com/{token}?imageView2/format/{img_fmt}"
                    
                images.append(final_img_url)
                
                live_url = None
                if live_enabled:
                    stream = img.get("stream")
                    if isinstance(stream, dict):
                        h264 = stream.get("h264")
                        if isinstance(h264, list) and len(h264) > 0:
                            item = h264[0]
                            if isinstance(item, dict):
                                live_url = item.get("masterUrl")
                lives.append(live_url)
                
        video = None
        if note_type == "视频":
            video_info = note.get("video")
            if isinstance(video_info, dict):
                origin_key = video_info.get("consumer", {}).get("originVideoKey")
                if origin_key:
                    video = f"https://sns-video-bd.xhscdn.com/{origin_key}"
                else:
                    media = video_info.get("media", {})
                    stream = media.get("stream", {})
                    h264_list = stream.get("h264", [])
                    h265_list = stream.get("h265", [])
                    all_streams = []
                    if isinstance(h264_list, list):
                        all_streams.extend(h264_list)
                    if isinstance(h265_list, list):
                        all_streams.extend(h265_list)
                    
                    valid_streams = [s for s in all_streams if isinstance(s, dict) and s.get("height")]
                    if valid_streams:
                        valid_streams.sort(key=lambda s: float(s.get("height", 0)), reverse=True)
                        best_stream = valid_streams[0]
                        backup_urls = best_stream.get("backupUrls", [])
                        if isinstance(backup_urls, list) and len(backup_urls) > 0:
                            video = backup_urls[0]
                        else:
                            video = best_stream.get("masterUrl")
                            
        cover = images[0] if images else ""
        if note_type == "视频":
            cover_info = note.get("cover", {})
            if isinstance(cover_info, dict):
                cover_url = cover_info.get("urlDefault") or cover_info.get("url")
                if cover_url:
                    try:
                        cover = bytes(cover_url, "utf-8").decode("unicode_escape")
                    except Exception:
                        cover = cover_url
                        
        token_match = re.search(r"xsec_token=([a-zA-Z0-9_\-]+)", url)
        xsec_token = token_match.group(1) if token_match else ""
        clean_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        if xsec_token:
            clean_url += f"?xsec_token={xsec_token}"
            
        return {
            "note_id": note_id,
            "note_url": clean_url,
            "title": title,
            "desc": desc,
            "type": note_type,
            "tags": tags,
            "author": author,
            "stats": stats,
            "publish_time": publish_time,
            "timestamp": timestamp,
            "images": images,
            "lives": lives,
            "video": video,
            "cover": cover
        }

    def get_user_posted(self, user_id: str, xsec_token: str = "", cursor: str = "") -> tuple:
        """用 xhshow 签名调用 user_posted API，返回 (notes_list, has_more, next_cursor)。
        签名失败 / 风控 / 空数据时抛异常，由调用方降级到 SSR 解析。"""
        if Xhshow is None:
            raise RuntimeError("未安装 xhshow，签名接口不可用")
        cookie = get_settings().get("xhs_cookie", "").strip()
        if not cookie:
            raise RuntimeError("签名接口需要登录 Cookie，请在登录页配置")
        if not user_id:
            raise ValueError("缺少 user_id")

        uri = "/api/sns/web/v1/user_posted"
        params = {
            "num": "30",
            "cursor": cursor,
            "user_id": user_id,
            "image_formats": "jpg,webp,avif",
            "xsec_token": xsec_token,
            "xsec_source": "pc_feed",
        }
        # 签名头按 xhshow 对 GET 的序列化规则（quote(value, safe=",")）计算，
        # 发送的查询串必须与签名时完全一致，否则 406。
        headers = Xhshow().sign_headers_get(
            uri=uri, cookies=cookie, params=params, sign_format="xyw", user_id=user_id
        )
        headers["cookie"] = cookie
        headers["user-agent"] = self.USER_AGENT
        headers["referer"] = "https://www.xiaohongshu.com/"

        query = "&".join(f"{k}={quote(str(v), safe=',')}" for k, v in params.items())
        full_url = "https://edith.xiaohongshu.com" + uri + "?" + query

        proxies = get_proxies_dict()
        proxy_url = proxies.get("http") if proxies else None
        time.sleep(random.uniform(1.0, 2.5))
        try:
            resp = requests.get(full_url, headers=headers, proxies=proxies, timeout=20)
            if proxy_url:
                report_proxy_status(proxy_url, success=True)
        except Exception as e:
            if proxy_url:
                report_proxy_status(proxy_url, success=False)
            raise

        try:
            body = resp.json()
        except Exception:
            raise ValueError(f"签名接口返回非 JSON（HTTP {resp.status_code}）")

        data = body.get("data") or {}
        raw = data.get("notes") or []
        if not raw:
            raise ValueError(
                f"签名接口未返回笔记（HTTP {resp.status_code}，msg={body.get('msg')}），可能触发风控或 Cookie 失效"
            )

        notes_list = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            cover_obj = it.get("cover") or {}
            cover = cover_obj.get("url_default") or cover_obj.get("url") or ""
            if not cover:
                info_list = cover_obj.get("info_list") or []
                if info_list:
                    cover = (info_list[-1] or {}).get("url", "")
            interact = it.get("interact_info") or {}
            notes_list.append({
                "note_id": it.get("note_id", ""),
                "xsec_token": it.get("xsec_token", ""),
                "title": it.get("display_title", ""),
                "cover": cover,
                "type": "video" if it.get("type") == "video" else "normal",
                "liked": self.format_count(interact.get("liked_count", "0")),
            })
        return notes_list, bool(data.get("has_more")), data.get("cursor", "")

    def _parse_ssr_notes(self, state: dict) -> list:
        """从主页 __INITIAL_STATE__ 解析首屏笔记（降级用）。
        注意：小红书已从主页 SSR 中移除 note_id，此结果仅供浏览、无法下载。"""
        user_data = state.get("user", {})
        raw_notes = user_data.get("notes", [])
        flat_notes = []
        if isinstance(raw_notes, list):
            for group in raw_notes:
                if isinstance(group, list):
                    flat_notes.extend(group)
                elif isinstance(group, dict):
                    flat_notes.append(group)

        notes_list = []
        for item in flat_notes:
            if not isinstance(item, dict):
                continue
            note_card = item.get("noteCard", {})
            cover = note_card.get("cover", {}).get("urlDefault", "")
            try:
                cover = bytes(cover, "utf-8").decode("unicode_escape")
            except Exception:
                pass
            interact = note_card.get("interactInfo", {})
            liked = self.format_count(interact.get("likedCount", "0")) if isinstance(interact, dict) else "0"
            notes_list.append({
                "note_id": item.get("id", "") or note_card.get("noteId", ""),
                "xsec_token": item.get("xsecToken", ""),
                "title": note_card.get("displayTitle", ""),
                "cover": cover,
                "type": "video" if note_card.get("type") == "video" else "normal",
                "liked": liked,
            })
        return notes_list

    def get_user_profile(self, url: str) -> dict:
        state = self.get_initial_state(url)
        user_data = state.get("user", {})
        page_data = user_data.get("userPageData", {})
        basic_info = page_data.get("basicInfo", {})

        nickname = basic_info.get("nickname", "").strip()
        avatar = basic_info.get("images", "").strip()
        desc = basic_info.get("desc", "").strip()
        red_id = basic_info.get("redId", "").strip()

        fans = "0"
        interactions = page_data.get("interactions", [])
        if isinstance(interactions, list):
            for item in interactions:
                if isinstance(item, dict) and item.get("type") == "fans":
                    fans = self.format_count(item.get("count", "0"))
                    break

        user_id = basic_info.get("userId", "")
        if not user_id:
            path_parts = urlparse(url).path.split("/")
            if len(path_parts) >= 4 and path_parts[2] == "profile":
                user_id = path_parts[3]

        user_obj = {
            "user_id": user_id,
            "nickname": nickname,
            "avatar": avatar,
            "desc": desc,
            "red_id": red_id,
            "fans": fans,
            "url": url
        }

        token_match = re.search(r"xsec_token=([^&]+)", url)
        xsec_token = token_match.group(1) if token_match else ""

        # 优先用签名接口（带 note_id、约 30 条）；失败时降级 SSR（无 note_id，仅供浏览）。
        result = {"user": user_obj}
        try:
            notes_list, _has_more, _cursor = self.get_user_posted(user_id, xsec_token)
            result["notes"] = notes_list
        except Exception as e:
            result["notes"] = self._parse_ssr_notes(state)
            result["warning"] = (
                f"博主笔记列表签名接口不可用（{e}）；已降级为主页首屏解析，"
                "该列表缺少笔记 ID 无法下载，请改用「链接下载」逐条下载。"
            )
        return result

    def download_file(self, url: str, save_path: Path) -> int:
        headers = self.get_headers()
        proxies = get_proxies_dict()
        proxy_url = proxies.get("http") if proxies else None
        
        tmp_path = save_path.with_suffix(save_path.suffix + ".tmp")
        try:
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            r = requests.get(url, headers=headers, proxies=proxies, stream=True, timeout=30)
            if r.status_code == 200:
                with open(tmp_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                if save_path.exists():
                    save_path.unlink()
                tmp_path.rename(save_path)
                if proxy_url:
                    report_proxy_status(proxy_url, success=True)
                return save_path.stat().st_size
            else:
                raise Exception(f"HTTP 状态码: {r.status_code}")
        except Exception as e:
            if proxy_url:
                report_proxy_status(proxy_url, success=False)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise e

# ── 登录态校验 ────────────────────────────────────────────
def check_xhs_login(cookie: str) -> dict:
    """用 xhshow 签名调 /user/me 判定登录态。
    返回 {logged_in, guest, user_id}；logged_in 为 None 表示无法判定（未装 xhshow / 网络失败）。
    注意：小红书对游客也会下发 web_session，故不能用 web_session 是否存在判断登录。"""
    cookie = (cookie or "").strip()
    if not cookie:
        return {"logged_in": False, "guest": True, "user_id": ""}
    if Xhshow is None:
        return {"logged_in": None, "guest": None, "user_id": "", "error": "未安装 xhshow"}
    uri = "/api/sns/web/v2/user/me"
    try:
        headers = Xhshow().sign_headers_get(uri=uri, cookies=cookie, params={}, sign_format="xyw")
        headers["cookie"] = cookie
        headers["user-agent"] = XhsClient.USER_AGENT
        headers["referer"] = "https://www.xiaohongshu.com/"
        proxies = get_proxies_dict()
        resp = requests.get("https://edith.xiaohongshu.com" + uri, headers=headers, proxies=proxies, timeout=15)
        body = resp.json()
        inner = body.get("data") or {}
        if body.get("success") and "guest" in inner:
            guest = bool(inner.get("guest", True))
            return {"logged_in": (not guest), "guest": guest, "user_id": inner.get("user_id", "")}
        # success:false 或缺 guest 字段（如「登录已过期」「账号存在异常」）→ 无法确认登录态，
        # 不能据此判为游客（避免把真登录误判成游客）。
        return {"logged_in": None, "guest": None, "user_id": "", "error": body.get("msg") or f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"logged_in": None, "guest": None, "user_id": "", "error": str(e)}

# ── 线程下载工作逻辑 ───────────────────────────────────────
def _do_xhs_download_thread(task_id: str, urls: list, account_name: str):
    client = XhsClient()
    history = load_json(XHS_HISTORY_FILE, [])
    
    downloaded_ids = {item.get("note_id") for item in history if isinstance(item, dict) and item.get("success")}
    
    for idx, url in enumerate(urls):
        with _download_lock:
            task = _download_tasks.get(task_id)
            if not task:
                return
            if task.get("cancel_requested"):
                task["status"] = "cancelled"
                task["end_time"] = time.time()
                task["current"] = "下载已取消"
                save_json(XHS_HISTORY_FILE, history)
                return
                
        note_id = client.extract_note_id(url)
        
        if note_id and note_id in downloaded_ids:
            with _download_lock:
                task = _download_tasks.get(task_id)
                if task:
                    task["skipped"] += 1
                    task["current"] = f"跳过已下载: {note_id}"
                    task["results"].append({
                        "title": f"Note_{note_id}",
                        "success": True,
                        "skipped": True
                    })
            continue
            
        with _download_lock:
            task = _download_tasks.get(task_id)
            if task:
                task["current"] = f"正在解析第 {idx + 1}/{len(urls)} 篇..."
                
        try:
            detail = client.get_note_detail(url)
            author_nick = clean_filename(detail["author"]["nickname"])
            pub_date = detail["publish_time"].split()[0]
            clean_title = clean_filename(detail["title"]) or detail["note_id"]
            
            note_dir = XHS_DIR / author_nick / f"{pub_date}_{clean_title}"
            note_dir.mkdir(parents=True, exist_ok=True)

            # 写入笔记文字内容（标题/正文/标签/数据），与媒体文件一同保存
            text_size = write_note_text(note_dir, detail)

            with _download_lock:
                task = _download_tasks.get(task_id)
                if task:
                    task["current"] = f"正在下载: {clean_title}..."

            success = True
            error_msg = None
            total_size = text_size
            video_file_name = None      # 用于生成 HTML：本地视频文件名
            image_items = []            # 用于生成 HTML：[{img, live}]

            settings = get_settings()
            img_fmt = settings.get("xhs_image_format", "png")
            img_ext = img_fmt if img_fmt != "auto" else "jpg"
            if img_ext == "jpeg":
                img_ext = "jpg"

            if detail["type"] == "视频":
                video_url = detail["video"]
                if video_url:
                    save_file = note_dir / f"{clean_title}.mp4"
                    try:
                        size = client.download_file(video_url, save_file)
                        total_size += size
                        video_file_name = save_file.name
                    except Exception as e:
                        success = False
                        error_msg = f"视频下载失败: {str(e)}"
                else:
                    success = False
                    error_msg = "未找到可用无水印视频链接"
            else:
                for img_idx, img_url in enumerate(detail["images"]):
                    save_file = note_dir / f"{img_idx + 1:02d}.{img_ext}"
                    try:
                        size = client.download_file(img_url, save_file)
                        total_size += size
                    except Exception as e:
                        success = False
                        error_msg = f"图片 {img_idx + 1} 下载失败: {str(e)}"
                        break

                    item = {"img": save_file.name, "live": None}
                    live_url = detail["lives"][img_idx] if img_idx < len(detail["lives"]) else None
                    if live_url:
                        live_file = note_dir / f"{img_idx + 1:02d}.mp4"
                        try:
                            time.sleep(random.uniform(0.3, 0.8))
                            size = client.download_file(live_url, live_file)
                            total_size += size
                            item["live"] = live_file.name
                        except Exception as e:
                            print(f"Warning: Live Photo download failed: {e}")

                    image_items.append(item)
                    time.sleep(random.uniform(0.3, 0.8))

            # 生成可直接打开的 HTML（引用本地已下载媒体）
            total_size += write_note_html(note_dir, detail, video_file_name, image_items)
                    
            with _download_lock:
                task = _download_tasks.get(task_id)
                if task:
                    if success:
                        task["completed"] += 1
                        task["results"].append({
                            "title": detail["title"] or detail["note_id"],
                            "success": True,
                            "path": str(note_dir)
                        })
                    else:
                        task["failed"] += 1
                        task["results"].append({
                            "title": detail["title"] or detail["note_id"],
                            "success": False,
                            "error": error_msg
                        })
                        
            history.append({
                "title": detail["title"] or detail["note_id"],
                "note_id": detail["note_id"],
                "type": detail["type"],
                "author": detail["author"]["nickname"],
                "path": str(note_dir),
                "size": total_size,
                "time": time.time(),
                "success": success,
                "error": error_msg
            })
            save_json(XHS_HISTORY_FILE, history)
            
        except Exception as e:
            err_str = str(e)
            with _download_lock:
                task = _download_tasks.get(task_id)
                if task:
                    task["failed"] += 1
                    task["results"].append({
                        "title": f"Link_{idx + 1}",
                        "success": False,
                        "error": err_str
                    })
                    
            history.append({
                "title": f"解析失败链接_{idx + 1}",
                "note_id": note_id or f"failed_{idx}",
                "type": "未知",
                "author": "未知",
                "path": "",
                "size": 0,
                "time": time.time(),
                "success": False,
                "error": err_str
            })
            save_json(XHS_HISTORY_FILE, history)
            
        if idx < len(urls) - 1:
            time.sleep(random.uniform(1.5, 3.0))
            
    with _download_lock:
        task = _download_tasks.get(task_id)
        if task:
            if task["status"] == "cancelling":
                task["status"] = "cancelled"
            else:
                task["status"] = "completed"
            task["end_time"] = time.time()
            task["current"] = ""

# ── API 路由 ──────────────────────────────────────────────
@xhs_bp.route("/parse", methods=["POST"])
def parse_url():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "请输入小红书链接"}), 400
        
    client = XhsClient()
    try:
        links = client.extract_links(url)
        if not links:
            return jsonify({"error": "未能从输入中提取到有效小红书链接，请确保包含explore或discovery链接"}), 400
        
        detail = client.get_note_detail(links[0])
        return jsonify(detail)
    except Exception as e:
        return jsonify({"error": f"解析失败: {str(e)}"}), 500

@xhs_bp.route("/download", methods=["POST"])
def download_urls():
    data = request.get_json() or {}
    urls = data.get("urls", [])
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split("\n") if u.strip()]
        
    if not urls:
        return jsonify({"error": "请输入小红书链接"}), 400
        
    client = XhsClient()
    extracted_urls = []
    for u in urls:
        extracted_urls.extend(client.extract_links(u))
        
    if not extracted_urls:
        return jsonify({"error": "未能从输入中提取到任何有效小红书链接"}), 400
        
    task_id = f"xhs_{int(time.time())}"
    with _download_lock:
        _download_tasks[task_id] = {
            "status": "running",
            "total": len(extracted_urls),
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "current": "正在初始化任务...",
            "results": [],
            "start_time": time.time(),
            "cancel_requested": False
        }
        
    thread = threading.Thread(
        target=_do_xhs_download_thread,
        args=(task_id, extracted_urls, "url_download"),
        daemon=True
    )
    thread.start()
    
    return jsonify({"task_id": task_id, "message": f"已启动下载任务，共 {len(extracted_urls)} 个链接", "count": len(extracted_urls)})

@xhs_bp.route("/accounts", methods=["GET"])
def list_accounts():
    accounts = load_json(XHS_ACCOUNTS_FILE, [])
    return jsonify({"accounts": accounts, "total": len(accounts)})

@xhs_bp.route("/accounts/parse", methods=["POST"])
def parse_user_url():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "请输入博主主页链接"}), 400
        
    client = XhsClient()
    try:
        profile = client.get_user_profile(url)
        return jsonify(profile)
    except Exception as e:
        return jsonify({"error": f"解析博主主页失败: {str(e)}"}), 500

@xhs_bp.route("/accounts", methods=["POST"])
def add_account():
    user = request.get_json() or {}
    user_id = user.get("user_id", "").strip()
    nickname = user.get("nickname", "").strip()
    if not user_id or not nickname:
        return jsonify({"error": "博主 ID 和昵称不能为空"}), 400
        
    accounts = load_json(XHS_ACCOUNTS_FILE, [])
    for acc in accounts:
        if acc.get("user_id") == user_id:
            return jsonify({"error": "该博主已在收藏中"}), 400
            
    user["added_time"] = time.time()
    accounts.append(user)
    save_json(XHS_ACCOUNTS_FILE, accounts)
    return jsonify({"message": "添加成功", "account": user})

@xhs_bp.route("/accounts/<user_id>", methods=["DELETE"])
def remove_account(user_id):
    accounts = load_json(XHS_ACCOUNTS_FILE, [])
    new_accounts = [a for a in accounts if a.get("user_id") != user_id]
    if len(new_accounts) == len(accounts):
        return jsonify({"error": "未找到该博主"}), 404
        
    save_json(XHS_ACCOUNTS_FILE, new_accounts)
    return jsonify({"message": "已取消收藏"})

@xhs_bp.route("/accounts/<user_id>/notes", methods=["GET"])
def list_user_notes(user_id):
    accounts = load_json(XHS_ACCOUNTS_FILE, [])
    url = None
    for acc in accounts:
        if acc.get("user_id") == user_id:
            url = acc.get("url")
            break
            
    if not url:
        url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
        
    client = XhsClient()
    try:
        profile = client.get_user_profile(url)
        return jsonify({"notes": profile.get("notes", []), "warning": profile.get("warning")})
    except Exception as e:
        return jsonify({"error": f"获取博主笔记失败: {str(e)}"}), 500

@xhs_bp.route("/download-notes", methods=["POST"])
def download_notes():
    data = request.get_json() or {}
    notes = data.get("notes", [])
    account_name = data.get("account_name", "unknown")
    if not notes:
        return jsonify({"error": "没有选中要下载的笔记"}), 400
        
    urls = []
    for note in notes:
        nid = note.get("note_id")
        token = note.get("xsec_token")
        if nid:
            url = f"https://www.xiaohongshu.com/explore/{nid}"
            if token:
                url += f"?xsec_token={token}&xsec_source=pc_user"
            urls.append(url)
            
    if not urls:
        return jsonify({"error": "选中的笔记缺少笔记 ID，无法下载（博主列表可能来自降级解析）。请改用「链接下载」逐条下载。"}), 400
        
    task_id = f"xhs_{int(time.time())}"
    with _download_lock:
        _download_tasks[task_id] = {
            "status": "running",
            "total": len(urls),
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "current": "正在初始化任务...",
            "results": [],
            "start_time": time.time(),
            "cancel_requested": False
        }
        
    thread = threading.Thread(
        target=_do_xhs_download_thread,
        args=(task_id, urls, account_name),
        daemon=True
    )
    thread.start()
    
    return jsonify({"task_id": task_id})

@xhs_bp.route("/download-status/<task_id>", methods=["GET"])
def get_task_status(task_id):
    with _download_lock:
        task = _download_tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task)

@xhs_bp.route("/download-cancel/<task_id>", methods=["POST"])
def cancel_task(task_id):
    with _download_lock:
        task = _download_tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    if task["status"] != "running":
        return jsonify({"message": "任务已结束"})
        
    task["cancel_requested"] = True
    task["status"] = "cancelling"
    return jsonify({"message": "正在取消任务..."})

@xhs_bp.route("/history", methods=["GET"])
def get_history():
    history = load_json(XHS_HISTORY_FILE, [])
    limit = request.args.get("limit", 100, type=int)
    
    indexed_history = []
    for idx, item in enumerate(history):
        if isinstance(item, dict):
            indexed = dict(item)
            indexed["_index"] = idx
            indexed_history.append(indexed)
            
    indexed_history.sort(key=lambda x: x.get("time", 0), reverse=True)
    if limit > 0:
        indexed_history = indexed_history[:limit]
        
    return jsonify({"history": indexed_history, "total": len(history)})

@xhs_bp.route("/history", methods=["DELETE"])
def clear_history():
    save_json(XHS_HISTORY_FILE, [])
    return jsonify({"message": "历史已清空"})

@xhs_bp.route("/history/<int:index>", methods=["DELETE"])
def delete_history_item(index):
    history = load_json(XHS_HISTORY_FILE, [])
    if index < 0 or index >= len(history):
        return jsonify({"error": "历史记录不存在"}), 404
        
    item = history[index]
    path_str = item.get("path", "") if isinstance(item, dict) else ""
    file_status = "no_path"
    
    if path_str:
        try:
            path = Path(path_str)
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                file_status = "deleted"
            else:
                file_status = "missing"
        except Exception as e:
            file_status = f"error: {str(e)}"
            
    history.pop(index)
    save_json(XHS_HISTORY_FILE, history)
    
    messages = {
        "deleted": "已删除下载文件和记录",
        "missing": "下载文件已不存在，已删除记录",
        "no_path": "记录没有文件路径，已删除记录",
    }
    msg = messages.get(file_status, f"下载文件清理失败（{file_status}），但已清除历史记录")
    return jsonify({"message": msg, "file_status": file_status})

@xhs_bp.route("/open-folder", methods=["POST"])
def open_folder():
    import subprocess
    import sys
    
    data = request.get_json() or {}
    account = data.get("account", "").strip()
    
    path = XHS_DIR
    if account:
        path = path / clean_filename(account)
        
    try:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "文件夹已打开"})
    except Exception as e:
        return jsonify({"error": f"打开文件夹失败: {str(e)}"}), 500

@xhs_bp.route("/open-file", methods=["POST"])
def open_file():
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

@xhs_bp.route("/open-parent", methods=["POST"])
def open_parent():
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
