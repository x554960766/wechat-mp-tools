"""
文章下载核心模块
将微信公众号文章离线下载为 HTML + 图片/视频
从 wechat_mp_batch_downloader.py 重构而来
"""

import re
import json
import requests
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, quote, unquote
from html import unescape

from backend.config import (
    DEFAULT_HEADERS, get_proxies_dict, get_settings, report_proxy_status
)


MPVIDEO_RE = re.compile(r'(?:https?:)?//mpvideo\.qpic\.cn/[^"\'\s<>]+?\.mp4(?:\?[^"\'\s<>]+)?', re.I)


def sanitize(name: str, mx: int = 60) -> str:
    """清理文件名"""
    return re.sub(r'[\\/*?:"<>|]', "_", name.strip())[:mx].rstrip("_") or "article"


def clean_html_to_text(html: str) -> str:
    """清理 HTML 并提取出纯文本，排除标签与非必要元素，用于 AI 快速转写"""
    # 先剥离 script 和 style 标签及其内部的内容，避免污染提取出的文本
    html = re.sub(r'<script\b[^>]*>([\s\S]*?)</script>', '', html, flags=re.I)
    html = re.sub(r'<style\b[^>]*>([\s\S]*?)</style>', '', html, flags=re.I)
    
    # 替换常见换行与段落标签为换行符
    text = re.sub(r'<(p|br|div|h1|h2|h3|h4|h5|h6|li|tr)[^>]*>', '\n', html)
    # 去除所有其他 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 解码 HTML 实体
    text = unescape(text)
    # 按行拆分，过滤多余空白字符
    lines = [line.strip() for line in text.split('\n')]
    clean_lines = []
    for line in lines:
        if line:
            clean_lines.append(line)
        elif not clean_lines or clean_lines[-1] != "":
            clean_lines.append("")
    return '\n'.join(clean_lines).strip()


def try_extract_gallery_article(raw_html: str) -> str:
    """对于没有 js_content 但有 picture_page_info_list 的画廊/贴图文章，从 JS 变量中提取图片和文字重建 HTML"""
    pos = raw_html.find("picture_page_info_list")
    if pos == -1:
        return None

    # 提取 picture_page_info_list 数组的 JS 源码
    content = raw_html[pos:]
    brackets = 0
    list_str = ""
    started = False
    for char in content:
        if char == '[':
            brackets += 1
            started = True
        elif char == ']':
            brackets -= 1
            if started and brackets == 0:
                list_str += char
                break
        if started:
            list_str += char

    if not list_str:
        return None

    # 从数组中提取每个对象 block {}
    items = []
    item_brackets = 0
    current_item = ""
    item_started = False
    for char in list_str:
        if char == '{':
            if item_brackets == 0:
                item_started = True
            item_brackets += 1
        if item_started:
            current_item += char
        if char == '}':
            item_brackets -= 1
            if item_brackets == 0 and item_started:
                items.append(current_item)
                current_item = ""
                item_started = False

    img_urls = []
    for item in items:
        # 兼容单引号和双引号
        cdn_url_match = re.search(r'cdn_url\s*:\s*["\']([^"\']+)["\']', item)
        if cdn_url_match:
            url = cdn_url_match.group(1)
            # 解密可能存在的 JS 十六进制转义符与特殊编码
            url = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), url)
            url = url.replace('&amp;', '&')
            img_urls.append(url)

    # 提取描述文字 window.desc，使用支持转义引号的安全模式匹配 JS 字符串字面量
    desc_match = re.search(r'window\.desc\s*=\s*"((?:[^"\\]|\\.)*)"', raw_html, re.DOTALL)
    if not desc_match:
        desc_match = re.search(r'window\.desc\s*=\s*\'((?:[^\'\\]|\\.)*)\'', raw_html, re.DOTALL)

    desc_text = ""
    if desc_match:
        raw_desc = desc_match.group(1)
        # 解开转义字符，包含 \xXX, \uXXXX, 常见 JS 控制符
        desc_text = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), raw_desc)
        desc_text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), desc_text)
        replacements = {
            '\\n': '\n',
            '\\t': '\t',
            '\\r': '\r',
            '\\"': '"',
            "\\'": "'",
            '\\\\': '\\'
        }
        for k, v in replacements.items():
            desc_text = desc_text.replace(k, v)

    if not img_urls and not desc_text:
        return None

    # 重建具有精美外观的画廊/贴图 HTML 正文
    html_parts = []
    html_parts.append('<div class="gallery-images" style="text-align: center; margin-bottom: 20px;">')
    for u in img_urls:
        html_parts.append(f'  <img src="{u}" style="max-width: 100%; height: auto; display: block; margin: 10px auto; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);" />')
    html_parts.append('</div>')

    if desc_text:
        html_parts.append('<div class="gallery-desc" style="font-size: 16px; color: #333; line-height: 1.6; background: #f9f9f9; padding: 15px; border-radius: 8px; border-left: 4px solid #07c160; margin-top: 20px;">')
        paragraphs = [p.strip() for p in desc_text.split('\n') if p.strip()]
        for p in paragraphs:
            html_parts.append(f'  <p style="margin: 0 0 10px 0;">{p}</p>')
        html_parts.append('</div>')

    return '\n'.join(html_parts)



def get_ext(url: str) -> str:
    """从 URL 猜测文件扩展名"""
    qs = parse_qs(urlparse(url).query)
    if "wx_fmt" in qs:
        return qs["wx_fmt"][0][:10]
    fn = urlparse(url).path.rsplit("/", 1)[-1].split("?")[0]
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    return ext if 1 <= len(ext) <= 5 else "jpg"


def get_video_ext(url: str) -> str:
    path = urlparse(url).path
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext if ext in {"mp4", "m3u8", "mov"} else "mp4"


def normalize_url(url: str) -> str:
    url = (
        unescape(url)
        .replace("\\x26amp;", "&")
        .replace("\\u0026amp;", "&")
        .replace("\\u0026", "&")
        .replace("&amp;", "&")
        .strip()
    )
    if url.startswith("//"):
        return "https:" + url
    return url


def download_resource(url: str, path: Path) -> bool:
    """下载单个资源文件"""
    proxy_url = None
    try:
        proxies = get_proxies_dict()
        if proxies:
            proxy_url = proxies.get("http")
        r = requests.get(
            url,
            headers={
                "Referer": "https://mp.weixin.qq.com/",
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
            },
            proxies=proxies,
            timeout=60,
            stream=True,
        )
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        report_proxy_status(proxy_url, success=True)
        return True
    except Exception:
        report_proxy_status(proxy_url, success=False)
        return False


def find_mmbiz_urls(html: str) -> set:
    """查找微信图片 URL"""
    urls = set()
    dec = html.replace("&amp;", "&")
    for m in re.finditer(r'(?:https?:)?//(?:mmbiz|mpvideo)\.qpic\.cn/[^"\'\s<>]+', dec):
        u = m.group()
        if u.startswith("//"):
            u = "https:" + u
        elif not u.startswith("http"):
            u = "https://" + u
        urls.add(u)
    for m in re.finditer(r'%3A%2F%2Fmmbiz\.qpic\.cn%2F[^\'"\\s&%]+', html, re.I):
        d = unquote(m.group()).replace("&amp;", "&")
        if d.startswith("//"):
            d = "https:" + d
        elif not d.startswith("http"):
            d = "https://" + d
        urls.add(d)
    return urls


def replace_variants(html: str, orig: str, local: str) -> str:
    """替换 HTML 中的远程 URL 为本地路径"""
    out = html
    for v in {orig, orig.replace("&", "&amp;")}:
        out = out.replace(f'"{v}"', f'"{local}"')
        out = out.replace(f"'{v}'", f"'{local}'")
        out = out.replace(f"url({v})", f"url({local})")
        out = out.replace(f"url('{v}')", f"url('{local}')")
        out = out.replace(f'url("{v}")', f'url("{local}")')
    if orig.startswith("https:"):
        rel = orig.replace("https:", "", 1)
        out = out.replace(f'"{rel}"', f'"{local}"')
    enc_o = quote(orig, safe="")
    enc_l = quote(local, safe="")
    if enc_o != orig:
        out = out.replace(enc_o, enc_l)
    return out


def _extract_video_iframes(html: str) -> list:
    videos = []
    for match in re.finditer(r'<iframe\b[^>]*class="[^"]*video_iframe[^"]*"[^>]*>', html, re.I):
        iframe = match.group(0)
        vid_match = re.search(r'data-mpvid="([^"]+)"', iframe)
        src_match = re.search(r'data-src="([^"]+)"', iframe)
        cover_match = re.search(r'data-cover="([^"]+)"', iframe)
        if not vid_match and not src_match:
            continue
        vid = vid_match.group(1) if vid_match else ""
        videos.append({
            "vid": vid,
            "iframe": iframe,
            "src": normalize_url(src_match.group(1)) if src_match else "",
            "cover": unquote(cover_match.group(1)).replace("&amp;", "&") if cover_match else "",
            "start": match.start(),
            "end": match.end(),
        })
    return videos


def _extract_video_urls(html: str) -> list:
    decoded = unescape(html).replace("\\/", "/")
    urls = []
    for url in MPVIDEO_RE.findall(decoded):
        url = normalize_url(url).rstrip("\\")
        if url not in urls:
            urls.append(url)
    return urls


def _group_video_urls(urls: list) -> list:
    groups = []
    seen = {}
    for url in urls:
        path = urlparse(url).path
        key = re.sub(r'\.f\d+\.mp4$', '', path, flags=re.I)
        if key not in seen:
            seen[key] = []
            groups.append(seen[key])
        seen[key].append(url)
    return groups


def _choose_video_url(candidates: list) -> str:
    if not candidates:
        return ""
    for marker in (".f10004.", ".f10002.", ".f10104.", ".f10102."):
        for url in candidates:
            if marker in url:
                return url
    return candidates[0]


def extract_article_content(raw_html: str) -> str:
    # 优先检测并提取画廊/贴图类型文章
    gallery_html = try_extract_gallery_article(raw_html)
    if gallery_html:
        return gallery_html

    marker = 'id="js_content"'
    marker_pos = raw_html.find(marker)
    if marker_pos < 0:
        return raw_html

    start_tag_begin = raw_html.rfind("<div", 0, marker_pos)
    start_tag_end = raw_html.find(">", marker_pos)
    if start_tag_begin < 0 or start_tag_end < 0:
        return raw_html

    end_candidates = [
        raw_html.find('id="js_tags_preview_toast"', start_tag_end),
        raw_html.find('id="js_article_bottom_bar"', start_tag_end),
        raw_html.find('id="js_pc_qr_code"', start_tag_end),
    ]
    end_candidates = [pos for pos in end_candidates if pos > start_tag_end]
    end = min(end_candidates) if end_candidates else len(raw_html)

    content = raw_html[start_tag_end + 1:end]
    last_close = content.rfind("</div>")
    if last_close >= 0:
        content = content[:last_close]
    return content


def replace_video_iframe(html: str, video: dict, local: str) -> str:
    vid = video.get("vid", "")
    if video.get("iframe") and video["iframe"] in html:
        return html.replace(video["iframe"], local)
    if not vid:
        return html
    pattern = re.compile(
        r'<iframe\b(?=[^>]*class="[^"]*video_iframe[^"]*")(?=[^>]*data-mpvid="' + re.escape(vid) + r'")[^>]*>',
        re.I,
    )
    return pattern.sub(local, html)


def download_single_article(url: str, out_dir: Path, title_hint: str = "") -> dict:
    """
    下载单篇文章

    Args:
        url: 文章 URL
        out_dir: 输出目录
        title_hint: 标题提示（可选）

    Returns:
        dict: {"success": bool, "title": str, "path": str, "error": str}
    """
    settings = get_settings()
    save_images = settings.get("auto_save_images", True)
    save_videos = settings.get("auto_save_videos", True)

    safe_title = sanitize(title_hint) if title_hint else "article"
    art_dir = out_dir / safe_title
    media_dir = art_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    proxy_url = None
    try:
        # 使用 requests 直接获取 HTML（不使用 Scrapling 避免依赖问题）
        proxies = get_proxies_dict()
        if proxies:
            proxy_url = proxies.get("http")
        resp = requests.get(
            url,
            headers={
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Referer": "https://mp.weixin.qq.com/",
            },
            proxies=proxies,
            timeout=60,
        )
        resp.raise_for_status()
        report_proxy_status(proxy_url, success=True)
        resp.encoding = "utf-8"
        raw_html = resp.text

        # 检测是否为微信屏蔽、删除或出错页面（贴图画廊类型可能不含 js_content，但含有 picture_page_info_list）
        has_js_content = 'id="js_content"' in raw_html or 'picture_page_info_list' in raw_html
        
        # 提取标题辅助判断
        title_tag_match = re.search(r'<title>([^<]*)</title>', raw_html, re.I)
        page_title_tag = title_tag_match.group(1).strip() if title_tag_match else ""
        
        is_error = False
        error_msg = ""
        is_permanent = False
        
        if not has_js_content:
            if any(kw in raw_html for kw in ["内容已被作者删除", "已被作者删除"]):
                is_error = True
                error_msg = "作者已删除"
                is_permanent = True
            elif any(kw in raw_html for kw in ["该内容暂时无法查看", "此内容因违规无法查看", "此内容无法查看"]):
                is_error = True
                error_msg = "内容已被微信屏蔽"
                is_permanent = True
            elif "链接已过期" in raw_html:
                is_error = True
                error_msg = "链接已过期"
                is_permanent = True
            elif "系统出错" in raw_html:
                is_error = True
                error_msg = "系统出错"
                is_permanent = False
        
        if not is_error and any(kw in page_title_tag for kw in ["该内容暂时无法查看", "系统出错"]):
            is_error = True
            if "该内容暂时无法查看" in page_title_tag:
                error_msg = "内容已被微信屏蔽"
                is_permanent = True
            else:
                error_msg = "系统出错"
                is_permanent = False
                
        if is_error:
            report_proxy_status(proxy_url, success=False)
            return {"success": False, "title": safe_title, "error": error_msg, "is_permanent": is_permanent}

    except Exception as e:
        report_proxy_status(proxy_url, success=False)
        return {"success": False, "title": safe_title, "error": f"获取页面失败: {str(e)}", "is_permanent": False}

    # 提取封面 URL、描述和发布时间
    cover_url = ""
    cover_match = re.search(r'<meta\b[^>]*property="og:image"[^>]*content="([^"]*)"', raw_html)
    if not cover_match:
        cover_match = re.search(r'<meta\b[^>]*content="([^"]*)"[^>]*property="og:image"', raw_html)
    if cover_match:
        cover_url = normalize_url(cover_match.group(1))

    digest = ""
    digest_match = re.search(r'<meta\b[^>]*property="og:description"[^>]*content="([^"]*)"', raw_html)
    if not digest_match:
        digest_match = re.search(r'<meta\b[^>]*content="([^"]*)"[^>]*property="og:description"', raw_html)
    if digest_match:
        digest = unescape(digest_match.group(1)).strip()
        # 解开可能含有的 JS 十六进制转义符和 unicode 转义符，并替换控制字符
        digest = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), digest)
        digest = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), digest)
        replacements = {
            '\\n': '\n',
            '\\t': '\t',
            '\\r': '\r',
            '\\"': '"',
            "\\'": "'",
            '\\\\': '\\'
        }
        for k, v in replacements.items():
            digest = digest.replace(k, v)

    publish_time = int(datetime.now(timezone.utc).timestamp())
    # 提取微信文章中的发布时间戳 ct
    ct_match = re.search(r'\bct\s*=\s*["\']?(\d+)["\']?', raw_html)
    if ct_match:
        publish_time = int(ct_match.group(1))
    else:
        pt_match = re.search(r'\bpublish_time\s*=\s*["\']?(\d+)["\']?', raw_html)
        if pt_match:
            publish_time = int(pt_match.group(1))

    # 从 HTML 提取标题，增加多路 Fallback 兜底（属性顺序无关）
    page_title = ""
    title_match = re.search(r'<meta\b[^>]*property="og:title"[^>]*content="([^"]*)"', raw_html)
    if not title_match:
        title_match = re.search(r'<meta\b[^>]*content="([^"]*)"[^>]*property="og:title"', raw_html)
    if title_match:
        page_title = title_match.group(1).strip()
    if not page_title:
        title_match = re.search(r'window\.title\s*=\s*["\'](.*?)["\']', raw_html)
        if title_match:
            page_title = title_match.group(1).strip()
    if not page_title:
        title_match = re.search(r'<title>([^<]*)</title>', raw_html, re.I)
        if title_match:
            page_title = title_match.group(1).strip()

    if page_title:
        safe_title = sanitize(page_title)
        # 更新目录名，如果冲突则通过递增后缀消解
        new_art_dir = out_dir / safe_title
        if new_art_dir != art_dir:
            counter = 1
            while new_art_dir.exists() and new_art_dir != art_dir:
                new_art_dir = out_dir / f"{safe_title}_{counter}"
                counter += 1
            
            if new_art_dir != art_dir:
                art_dir.rename(new_art_dir)
                art_dir = new_art_dir
                media_dir = art_dir / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                safe_title = new_art_dir.name

    # 提取正文区域
    content_html = extract_article_content(raw_html)
    video_iframes = _extract_video_iframes(raw_html)
    video_urls = _extract_video_urls(raw_html)

    # 下载图片
    url_map = {}
    if save_images:
        img_urls = set()
        
        # 优先下载封面图
        if cover_url:
            cover_ext = get_ext(cover_url) or "jpg"
            cover_filename = f"cover.{cover_ext}"
            if download_resource(cover_url, media_dir / cover_filename):
                url_map[cover_url] = f"media/{cover_filename}"

        # 仅在文章正文 content_html 内提取图片，避免下载外部页眉、页脚、微信小图标等多余资源
        for m in re.finditer(r'data-src="([^"]*mmbiz[^"]*)"', content_html):
            img_urls.add(normalize_url(m.group(1)))
        for m in re.finditer(r'src="([^"]*mmbiz[^"]*)"', content_html):
            img_urls.add(normalize_url(m.group(1)))

        video_covers = {normalize_url(v["cover"]) for v in video_iframes if v.get("cover")}
        img_urls = {u for u in img_urls if u not in video_covers}

        for i, img_url in enumerate(img_urls, 1):
            if img_url in url_map:
                continue
            ext = get_ext(img_url)
            fname = f"img_{i:03d}.{ext}"
            if download_resource(img_url, media_dir / fname):
                url_map[img_url] = f"media/{fname}"

    # 下载视频
    if save_videos:
        video_items = []
        used_video_urls = set()
        grouped_urls = _group_video_urls(video_urls)
        for idx, video in enumerate(video_iframes):
            candidates = grouped_urls[idx] if idx < len(grouped_urls) else []
            vid_url = _choose_video_url(candidates)
            if vid_url and vid_url not in used_video_urls:
                video_items.append({**video, "url": vid_url})
                used_video_urls.add(vid_url)

        if not video_items:
            for group in grouped_urls:
                vid_url = _choose_video_url(group)
                if vid_url not in used_video_urls:
                    video_items.append({"vid": "", "iframe": "", "src": "", "cover": "", "url": vid_url})
                    used_video_urls.add(vid_url)

        for i, video in enumerate(video_items, 1):
            vid_url = video["url"]
            ext = get_ext(vid_url) or "mp4"
            ext = get_video_ext(vid_url) if ext == "jpg" else ext
            fname = f"video_{i}.{ext}"
            if download_resource(vid_url, media_dir / fname):
                url_map[vid_url] = f"media/{fname}"
                if video.get("src"):
                    url_map[video["src"]] = f"media/{fname}"
                poster_attr = ""
                if save_images and video.get("cover"):
                    cover_ext = get_ext(video["cover"])
                    cover_name = f"video_cover_{i}.{cover_ext}"
                    if download_resource(video["cover"], media_dir / cover_name):
                        poster_attr = f' poster="media/{cover_name}"'
                        url_map[video["cover"]] = f"media/{cover_name}"
                url_map[f"__video_iframe__{video.get('vid', i)}"] = (
                    video,
                    f'<video controls src="media/{fname}"{poster_attr}></video>',
                )

    # 替换 URL
    localized = content_html
    for orig, local in url_map.items():
        if orig.startswith("__video_iframe__"):
            video, video_tag = local
            localized = replace_video_iframe(localized, video, video_tag)
        else:
            localized = replace_variants(localized, orig, local)
    # 同时处理 data-src -> src 替换
    localized = re.sub(r'data-src="([^"]*)"', r'src="\1"', localized)

    # 生成离线 HTML
    full_html = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'  <title>{safe_title}</title>\n'
        '  <style>\n'
        '    body{max-width:680px;margin:0 auto;padding:20px;\n'
        "         font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;\n"
        '         line-height:1.8;color:#333}\n'
        '    img,video{max-width:100%;height:auto;display:block;margin:10px auto}\n'
        '  </style>\n</head>\n<body>\n'
        f'<h1>{safe_title}</h1>\n'
        '<div id="js_content">\n' + localized + '\n</div>\n'
        '</body>\n</html>'
    )

    html_path = art_dir / f"{safe_title}.html"
    html_path.write_text(full_html, encoding="utf-8")

    # 保存纯文本内容（用于 AI 一键转写）
    clean_text = clean_html_to_text(content_html)
    (art_dir / "content.txt").write_text(clean_text, encoding="utf-8")

    # 保存元数据
    meta = {
        "title": safe_title,
        "url": url,
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "publish_time": publish_time,
        "cover_url": cover_url,
        "digest": digest,
        "resources_count": len(url_map),
        "videos_count": len(video_iframes),
        "leftover_urls": len(find_mmbiz_urls(localized)),
        "has_clean_text": True,
        "clean_text_file": "content.txt",
    }
    (art_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    return {
        "success": True,
        "title": safe_title,
        "path": str(art_dir),
        "resources": len(url_map),
        "cover_url": cover_url,
        "digest": digest,
        "publish_time": publish_time,
    }
