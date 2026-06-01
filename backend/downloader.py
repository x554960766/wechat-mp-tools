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


def sanitize(name: str, mx: int = 60) -> str:
    """清理文件名"""
    return re.sub(r'[\\/*?:"<>|]', "_", name.strip())[:mx].rstrip("_") or "article"


def clean_html_to_text(html: str) -> str:
    """清理 HTML 并提取出纯文本，排除标签与非必要元素，用于 AI 快速转写"""
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


def get_ext(url: str) -> str:
    """从 URL 猜测文件扩展名"""
    qs = parse_qs(urlparse(url).query)
    if "wx_fmt" in qs:
        return qs["wx_fmt"][0][:10]
    fn = urlparse(url).path.rsplit("/", 1)[-1].split("?")[0]
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    return ext if 1 <= len(ext) <= 5 else "jpg"


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

    except Exception as e:
        report_proxy_status(proxy_url, success=False)
        return {"success": False, "title": safe_title, "error": f"获取页面失败: {str(e)}"}

    # 从 HTML 提取标题
    title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', raw_html)
    if title_match:
        page_title = title_match.group(1).strip()
        if page_title:
            safe_title = sanitize(page_title)
            # 更新目录名
            new_art_dir = out_dir / safe_title
            if new_art_dir != art_dir and not new_art_dir.exists():
                art_dir.rename(new_art_dir)
                art_dir = new_art_dir
                media_dir = art_dir / "media"
                media_dir.mkdir(parents=True, exist_ok=True)

    # 提取正文区域
    content_match = re.search(
        r'<div[^>]*id="js_content"[^>]*>(.*?)</div>\s*(?:<div|<script|$)',
        raw_html,
        re.DOTALL
    )
    content_html = content_match.group(1) if content_match else raw_html

    # 下载图片
    url_map = {}
    if save_images:
        img_urls = set()
        # 仅在文章正文 content_html 内提取图片，避免下载外部页眉、页脚、微信小图标等多余资源
        for m in re.finditer(r'data-src="([^"]*mmbiz[^"]*)"', content_html):
            img_urls.add(m.group(1).replace("&amp;", "&"))
        for m in re.finditer(r'src="([^"]*mmbiz[^"]*)"', content_html):
            img_urls.add(m.group(1).replace("&amp;", "&"))

        for i, img_url in enumerate(img_urls, 1):
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            ext = get_ext(img_url)
            fname = f"img_{i:03d}.{ext}"
            if download_resource(img_url, media_dir / fname):
                url_map[img_url] = f"media/{fname}"

    # 下载视频
    if save_videos:
        video_urls = set()
        # 仅在文章正文 content_html 内提取视频
        for m in re.finditer(r'src="([^"]*mpvideo[^"]*)"', content_html):
            video_urls.add(m.group(1).replace("&amp;", "&"))

        for i, vid_url in enumerate(video_urls, 1):
            if vid_url.startswith("//"):
                vid_url = "https:" + vid_url
            ext = get_ext(vid_url) or "mp4"
            fname = f"video_{i}.{ext}"
            if download_resource(vid_url, media_dir / fname):
                url_map[vid_url] = f"media/{fname}"

    # 替换 URL
    localized = content_html
    for orig, local in url_map.items():
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
        "resources_count": len(url_map),
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
    }
