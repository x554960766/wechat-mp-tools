"""
文章下载核心模块
将微信公众号文章离线下载为 HTML + 图片/视频
从 wechat_mp_batch_downloader.py 重构而来
"""

import sys
import os
import re
import json
import shutil
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, quote, unquote
from html import unescape
from html.parser import HTMLParser

from backend.config import (
    DEFAULT_HEADERS, get_proxies_dict, get_settings, report_proxy_status
)


MPVIDEO_RE = re.compile(r'(?:https?:)?//mpvideo\.qpic\.cn/[^"\'\s<>]+?\.mp4(?:\?[^"\'\s<>]+)?', re.I)


def sanitize(name: str, mx: int = 60) -> str:
    """清理文件名"""
    return re.sub(r'[\\/*?:"<>|]', "_", name.strip())[:mx].rstrip("_") or "article"


class HTMLToMarkdownParser(HTMLParser):
    """将 HTML 解析转换为符合 GitHub Flavored Markdown 规范的文本"""
    def __init__(self, media_map=None, title_to_skip=None):
        super().__init__()
        self.media_map = media_map or {}
        self.title_to_skip = (title_to_skip or "").strip().lower()
        self.pieces = []
        self.list_stack = []
        self.in_pre = False
        self.in_code = False
        self.in_blockquote = False
        self.blockquote_depth = 0
        self.in_table = False
        self.table_data = []
        self.current_row = []
        self.current_cell = []
        self.link_stack = []
        self.bold_depth = 0
        self.italic_depth = 0
        self.strike_depth = 0
        self.heading_level = 0
        self.heading_text = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        tag = tag.lower()

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.heading_level = int(tag[1])
            self.heading_text = []
        elif tag == 'p':
            self._ensure_newline(2)
        elif tag == 'br':
            self.pieces.append('\n')
        elif tag == 'hr':
            self._ensure_newline(2)
            self.pieces.append('---\n\n')
        elif tag in ('strong', 'b'):
            if self.bold_depth == 0:
                self.pieces.append('**')
            self.bold_depth += 1
        elif tag in ('em', 'i'):
            if self.italic_depth == 0:
                self.pieces.append('*')
            self.italic_depth += 1
        elif tag in ('del', 's', 'strike'):
            if self.strike_depth == 0:
                self.pieces.append('~~')
            self.strike_depth += 1
        elif tag == 'code':
            if not self.in_pre:
                self.in_code = True
                self.pieces.append('`')
        elif tag == 'pre':
            self._ensure_newline(2)
            self.in_pre = True
            lang = attr_dict.get('data-lang', '') or attr_dict.get('lang', '')
            self.pieces.append(f'```{lang}\n')
        elif tag == 'blockquote':
            self._ensure_newline(2)
            self.blockquote_depth += 1
            self.in_blockquote = True
            self.pieces.append('> ')
        elif tag == 'ul':
            self._ensure_newline(2 if not self.list_stack else 1)
            self.list_stack.append(('ul', 0))
        elif tag == 'ol':
            self._ensure_newline(2 if not self.list_stack else 1)
            self.list_stack.append(('ol', 0))
        elif tag == 'li':
            self._ensure_newline(1)
            indent = '  ' * (len(self.list_stack) - 1) if self.list_stack else ''
            if self.list_stack:
                ltype, count = self.list_stack[-1]
                if ltype == 'ol':
                    count += 1
                    self.list_stack[-1] = (ltype, count)
                    prefix = f'{indent}{count}. '
                else:
                    prefix = f'{indent}- '
            else:
                prefix = '- '
            self.pieces.append(prefix)
        elif tag == 'a':
            href = attr_dict.get('href', '').strip()
            if href and not href.startswith('javascript:') and href != '#':
                self.link_stack.append(href)
                self.pieces.append('[')
            else:
                self.link_stack.append(None)
        elif tag == 'img':
            src = attr_dict.get('src') or attr_dict.get('data-src') or ''
            if src:
                src = src.strip()
                local_src = self.media_map.get(src, src)
                alt = attr_dict.get('alt', '').strip() or attr_dict.get('data-title', '').strip()
                self._ensure_newline(2)
                self.pieces.append(f'![{alt}]({local_src})\n\n')
        elif tag == 'table':
            self._ensure_newline(2)
            self.in_table = True
            self.table_data = []
        elif tag == 'tr':
            if self.in_table:
                self.current_row = []
        elif tag in ('th', 'td'):
            if self.in_table:
                self.current_cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            level = self.heading_level or 1
            text = ''.join(self.heading_text).strip()
            if self.title_to_skip and text.lower() == self.title_to_skip and len(self.pieces) < 5:
                pass
            elif text:
                self._ensure_newline(2)
                self.pieces.append(f"{'#' * level} {text}\n\n")
            self.heading_level = 0
            self.heading_text = []
        elif tag == 'p':
            self._ensure_newline(2)
        elif tag in ('strong', 'b'):
            self.bold_depth = max(0, self.bold_depth - 1)
            if self.bold_depth == 0:
                self.pieces.append('**')
        elif tag in ('em', 'i'):
            self.italic_depth = max(0, self.italic_depth - 1)
            if self.italic_depth == 0:
                self.pieces.append('*')
        elif tag in ('del', 's', 'strike'):
            self.strike_depth = max(0, self.strike_depth - 1)
            if self.strike_depth == 0:
                self.pieces.append('~~')
        elif tag == 'code':
            if not self.in_pre:
                self.pieces.append('`')
                self.in_code = False
        elif tag == 'pre':
            self.pieces.append('\n```\n\n')
            self.in_pre = False
        elif tag == 'blockquote':
            self.blockquote_depth = max(0, self.blockquote_depth - 1)
            if self.blockquote_depth == 0:
                self.in_blockquote = False
            self._ensure_newline(2)
        elif tag in ('ul', 'ol'):
            if self.list_stack:
                self.list_stack.pop()
            self._ensure_newline(2)
        elif tag == 'li':
            self._ensure_newline(1)
        elif tag == 'a':
            if self.link_stack:
                href = self.link_stack.pop()
                if href:
                    self.pieces.append(f']({href})')
        elif tag == 'table':
            if self.in_table:
                self.in_table = False
                self._render_table()
                self._ensure_newline(2)
        elif tag == 'tr':
            if self.in_table and self.current_row is not None:
                self.table_data.append(self.current_row)
                self.current_row = []
        elif tag in ('th', 'td'):
            if self.in_table:
                cell_text = ''.join(self.current_cell).strip().replace('|', '\\|').replace('\n', ' ')
                self.current_row.append(cell_text)
                self.current_cell = []

    def handle_data(self, data):
        if self.in_table and self.current_cell is not None:
            self.current_cell.append(data)
            return
        if self.heading_level > 0:
            self.heading_text.append(data)
            return
        if self.in_pre:
            self.pieces.append(data)
            return

        clean_data = data
        if not self.in_code:
            clean_data = re.sub(r'[ \t]+', ' ', data)

        if self.in_blockquote:
            lines = clean_data.split('\n')
            clean_data = '\n> '.join(lines)

        self.pieces.append(clean_data)

    def _ensure_newline(self, count=1):
        trailing_newlines = 0
        idx = len(self.pieces) - 1
        while idx >= 0:
            text = self.pieces[idx]
            for ch in reversed(text):
                if ch == '\n':
                    trailing_newlines += 1
                elif ch in (' ', '\t', '\r'):
                    continue
                else:
                    idx = -1
                    break
            idx -= 1
        needed = count - trailing_newlines
        if needed > 0:
            self.pieces.append('\n' * needed)

    def _render_table(self):
        if not self.table_data:
            return
        col_count = max(len(row) for row in self.table_data)
        if col_count == 0:
            return
        for row in self.table_data:
            while len(row) < col_count:
                row.append('')

        header = self.table_data[0]
        rows = self.table_data[1:] if len(self.table_data) > 1 else []

        lines = []
        lines.append('| ' + ' | '.join(header) + ' |')
        lines.append('| ' + ' | '.join(['---'] * col_count) + ' |')
        for r in rows:
            lines.append('| ' + ' | '.join(r) + ' |')
        self.pieces.append('\n' + '\n'.join(lines) + '\n\n')

    def get_markdown(self):
        md = ''.join(self.pieces)
        md = unescape(md)
        raw_lines = md.split('\n')
        clean_lines = []
        in_code_block = False
        for line in raw_lines:
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                clean_lines.append(line.strip())
                continue
            if in_code_block:
                clean_lines.append(line)
            else:
                stripped = line.strip()
                if stripped:
                    if stripped.startswith('>'):
                        quote_content = stripped.lstrip('>').strip()
                        if quote_content:
                            clean_lines.append(f"> {quote_content}")
                    else:
                        leading_spaces = len(line) - len(line.lstrip(' '))
                        indent = ' ' * (leading_spaces if leading_spaces > 1 and stripped.startswith(('-', '*', '1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.')) else 0)
                        clean_lines.append(indent + stripped)
                elif not clean_lines or clean_lines[-1] != "":
                    clean_lines.append("")
        return '\n'.join(clean_lines).strip()


def html_to_markdown(html_content: str, meta: dict = None, media_map: dict = None) -> str:
    """参考 qiye45/wechatDownload 风格，将文章 HTML 转换为标准美化 Markdown"""
    cleaned = re.sub(r'<script\b[^>]*>([\s\S]*?)</script>', '', html_content, flags=re.I)
    cleaned = re.sub(r'<style\b[^>]*>([\s\S]*?)</style>', '', cleaned, flags=re.I)
    cleaned = re.sub(r'<noscript\b[^>]*>([\s\S]*?)</noscript>', '', cleaned, flags=re.I)
    cleaned = re.sub(r'<svg\b[^>]*>([\s\S]*?)</svg>', '', cleaned, flags=re.I)
    cleaned = re.sub(r'<div\b[^>]*id="(?:js_pc_qr_code|js_profile_qrcode|js_tags_preview_toast)"[^>]*>[\s\S]*?</div>', '', cleaned, flags=re.I)

    title = (meta.get('title') if meta else '') or ''
    parser = HTMLToMarkdownParser(media_map=media_map, title_to_skip=title)
    parser.feed(cleaned)
    body_md = parser.get_markdown()

    header_parts = []
    if meta:
        cover = meta.get('cover_url') or meta.get('cover')
        if cover:
            local_cover = (media_map or {}).get(cover, cover)
            header_parts.append(f'![cover_image]({local_cover})\n\n')

        if title:
            header_parts.append(f'# {title}\n\n')

        author_info = []
        author = meta.get('author')
        account = meta.get('source') or meta.get('account')
        if author:
            author_info.append(f'作者：{author}')
        if account:
            author_info.append(f'公众号：{account}')
        if author_info:
            header_parts.append('  '.join(author_info) + '\n\n')

        date_info = []
        publish_time = meta.get('publish_time')
        if publish_time:
            try:
                if isinstance(publish_time, (int, float)):
                    dt = datetime.fromtimestamp(publish_time)
                    date_info.append(f"_{dt.strftime('%Y年%m月%d日 %H:%M')}_")
                elif isinstance(publish_time, str):
                    date_info.append(f"_{publish_time}_")
            except Exception:
                pass
        if date_info:
            header_parts.append(' '.join(date_info) + '\n\n')

        if header_parts:
            header_parts.append('---\n\n')

    return ''.join(header_parts) + body_md


def find_chromium_executable() -> str | None:
    """查找系统中可用的 Chrome / Edge / Chromium 可执行文件路径"""
    custom_path = os.environ.get("CHROME_PATH") or os.environ.get("CHROMIUM_PATH")
    if custom_path and Path(custom_path).exists():
        return custom_path

    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            os.path.expanduser("~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    elif sys.platform == "win32":
        for base in [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")]:
            if base:
                candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
                candidates.append(str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    else:
        for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "edge"]:
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for p in candidates:
        if p and Path(p).exists() and (os.access(p, os.X_OK) if sys.platform != "win32" else True):
            return p
    return None


def export_html_to_pdf(html_path: Path, pdf_path: Path, title: str = "") -> bool:
    """使用 Headless Chrome 将 HTML 文件转换为 PDF"""
    chrome_exe = find_chromium_executable()
    if not chrome_exe:
        return False

    html_path = html_path.resolve()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_exe,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw",
        f"--print-to-pdf={str(pdf_path)}",
        str(html_path)
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, timeout=60)
        return pdf_path.exists() and pdf_path.stat().st_size > 0
    except Exception:
        return False


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
    marker = 'id="js_content"'
    marker_pos = raw_html.find(marker)
    if marker_pos < 0:
        gallery_html = try_extract_gallery_article(raw_html)
        return gallery_html or raw_html

    start_tag_begin = raw_html.rfind("<div", 0, marker_pos)
    start_tag_end = raw_html.find(">", marker_pos)
    if start_tag_begin < 0 or start_tag_end < 0:
        gallery_html = try_extract_gallery_article(raw_html)
        return gallery_html or raw_html

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
    if content.strip():
        return content

    gallery_html = try_extract_gallery_article(raw_html)
    return gallery_html or raw_html


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


def download_single_article(url: str, out_dir: Path, title_hint: str = "", _redirect_depth: int = 0) -> dict:
    """
    下载单篇文章

    Args:
        url: 文章 URL
        out_dir: 输出目录
        title_hint: 标题提示（可选）
        _redirect_depth: 内部参数，用于防止"阅读全文"跳转无限递归

    Returns:
        dict: {"success": bool, "title": str, "path": str, "error": str}
    """
    MAX_REDIRECT_DEPTH = 3
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
        try:
            raw_html = resp.content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                raw_html = resp.content.decode("gb18030", errors="replace")
            except Exception:
                raw_html = resp.content.decode("utf-8", errors="replace")

        # 检测 captcha 验证码页面（微信反爬限制，请求被 302 跳转到验证码页）
        final_url = resp.url if hasattr(resp, 'url') else url
        if 'wappoc_appmsgcaptcha' in final_url or 'wappoc_appmsgcaptcha' in raw_html:
            report_proxy_status(proxy_url, success=False)
            return {"success": False, "title": safe_title, "error": "微信验证码拦截，请稍后重试或更换 IP", "is_permanent": False}

        # 检测是否为微信屏蔽、删除或出错页面（贴图画廊类型可能不含 js_content/js_article，但含有 picture_page_info_list）
        has_js_content = 'id="js_content"' in raw_html or 'id="js_article"' in raw_html or 'picture_page_info_list' in raw_html

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
            elif 'id="app"' in raw_html:
                is_error = True
                error_msg = "隐私保护，仅内部分享可见"
                is_permanent = True
            else:
                is_error = True
                error_msg = "未知页面"
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

        # 检测"阅读全文"跳转类型文章（转载/分享文章，js_content 为空，原文 URL 在 js_share_source 的 data-url 中）
        share_source_match = re.search(r'data-url="([^"]+)"[^>]*id="js_share_source"', raw_html)
        if not share_source_match:
            share_source_match = re.search(r'id="js_share_source"[^>]*data-url="([^"]+)"', raw_html)
        if share_source_match:
            # 检查 js_content 是否实际为空
            content_check = re.search(r'id="js_content"[^>]*>([\s\S]*?)</div>', raw_html)
            content_is_empty = not content_check or not content_check.group(1).strip()
            if content_is_empty and _redirect_depth < MAX_REDIRECT_DEPTH:
                original_url = unescape(share_source_match.group(1))
                # 清理 URL: 去掉 scene=45 和 #wechat_redirect 以避免触发验证码
                from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
                parsed = urlparse(original_url)
                params = parse_qs(parsed.query)
                params.pop('scene', None)
                clean_query = urlencode(params, doseq=True)
                original_url = urlunparse(('https', parsed.netloc, parsed.path, '', clean_query, ''))
                # 清理临时目录
                import shutil
                if art_dir.exists():
                    shutil.rmtree(art_dir, ignore_errors=True)
                return download_single_article(original_url, out_dir, title_hint, _redirect_depth=_redirect_depth + 1)

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
        '    @page { margin: 15mm 12mm; size: A4; }\n'
        '    body { max-width: 720px; margin: 0 auto; padding: 20px;\n'
        "           font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Hiragino Sans GB', 'Helvetica Neue', sans-serif;\n"
        '           line-height: 1.8; color: #333; word-break: break-word; }\n'
        '    img, video { max-width: 100%; height: auto; display: block; margin: 14px auto;\n'
        '                 page-break-inside: avoid; break-inside: avoid; border-radius: 4px; }\n'
        '    h1, h2, h3, h4, h5, h6 { page-break-after: avoid; break-after: avoid; color: #111; }\n'
        '    h1 { font-size: 24px; margin-bottom: 16px; }\n'
        '    blockquote { margin: 14px 0; padding: 10px 16px; background: #f8f9fa; border-left: 4px solid #07c160; color: #555; }\n'
        '    table { width: 100%; border-collapse: collapse; margin: 15px 0; }\n'
        '    th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }\n'
        '    th { background-color: #f2f2f2; font-weight: 600; }\n'
        '    code, pre { font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; background-color: #f6f8fa; border-radius: 4px; }\n'
        '    pre { padding: 12px; overflow-x: auto; line-height: 1.5; }\n'
        '    #js_content, .rich_media_content { visibility: visible !important; }\n'
        '  </style>\n</head>\n<body>\n'
        f'<h1>{safe_title}</h1>\n'
        '<div id="js_content">\n' + localized + '\n</div>\n'
        '</body>\n</html>'
    )

    html_path = art_dir / f"{safe_title}.html"
    html_path.write_text(full_html, encoding="utf-8")

    # 生成并保存原始 HTML (保持外部资源链接不变)
    raw_localized = content_html
    raw_localized = re.sub(r'data-src="([^"]*)"', r'src="\1"', raw_localized)
    raw_full_html = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'  <title>{safe_title}</title>\n'
        '  <style>\n'
        '    @page { margin: 15mm 12mm; size: A4; }\n'
        '    body { max-width: 720px; margin: 0 auto; padding: 20px;\n'
        "           font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Hiragino Sans GB', 'Helvetica Neue', sans-serif;\n"
        '           line-height: 1.8; color: #333; word-break: break-word; }\n'
        '    img, video { max-width: 100%; height: auto; display: block; margin: 14px auto;\n'
        '                 page-break-inside: avoid; break-inside: avoid; border-radius: 4px; }\n'
        '    h1, h2, h3, h4, h5, h6 { page-break-after: avoid; break-after: avoid; color: #111; }\n'
        '    h1 { font-size: 24px; margin-bottom: 16px; }\n'
        '    blockquote { margin: 14px 0; padding: 10px 16px; background: #f8f9fa; border-left: 4px solid #07c160; color: #555; }\n'
        '    table { width: 100%; border-collapse: collapse; margin: 15px 0; }\n'
        '    th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }\n'
        '    th { background-color: #f2f2f2; font-weight: 600; }\n'
        '    code, pre { font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; background-color: #f6f8fa; border-radius: 4px; }\n'
        '    pre { padding: 12px; overflow-x: auto; line-height: 1.5; }\n'
        '    #js_content, .rich_media_content { visibility: visible !important; }\n'
        '  </style>\n</head>\n<body>\n'
        f'<h1>{safe_title}</h1>\n'
        '<div id="js_content">\n' + raw_localized + '\n</div>\n'
        '</body>\n</html>'
    )
    (art_dir / f"{safe_title}_raw.html").write_text(raw_full_html, encoding="utf-8")

    # 提取公众号 source 名字
    source = ""
    nickname_match = re.search(r'\bnickname\s*=\s*["\']([^"\']+)["\']', raw_html)
    if nickname_match:
        source = unescape(nickname_match.group(1)).strip()
    if not source:
        nickname_match = re.search(r'class="profile_nickname"[^>]*>([^<]+)<', raw_html)
        if nickname_match:
            source = unescape(nickname_match.group(1)).strip()
    if not source:
        nickname_match = re.search(r'id="js_name"[^>]*>([^<]+)<', raw_html)
        if nickname_match:
            source = unescape(nickname_match.group(1)).strip()

    if not source or out_dir.name != "url_download":
        source = out_dir.name

    # 提取作者名字
    author = ""
    author_match = re.search(r'\bauthor\s*=\s*["\']([^"\']+)["\']', raw_html)
    if author_match:
        author = unescape(author_match.group(1)).strip()
    if not author:
        author_match = re.search(r'class="rich_media_meta rich_media_meta_text"[^>]*>([^<]+)<', raw_html)
        if author_match:
            author = unescape(author_match.group(1)).strip()

    new_json_data = {
        "source": source,
        "author": author,
        "title": page_title or safe_title,
        "url": url,
        "cover_url": cover_url,
        "publish_time": publish_time,
        "content": raw_localized
    }
    (art_dir / "data.json").write_text(json.dumps(new_json_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 保存纯文本内容（用于 AI 一键转写）
    clean_text = clean_html_to_text(content_html)
    (art_dir / "content.txt").write_text(clean_text, encoding="utf-8")

    # 1. 保存 Markdown (.md) 文件
    save_markdown = settings.get("save_markdown", True)
    md_file_path = None
    if save_markdown:
        try:
            md_meta = {
                "title": page_title or safe_title,
                "author": author,
                "source": source,
                "cover_url": cover_url,
                "publish_time": publish_time,
            }
            md_text = html_to_markdown(content_html, meta=md_meta, media_map=url_map)
            md_file_path = art_dir / f"{safe_title}.md"
            md_file_path.write_text(md_text, encoding="utf-8")
        except Exception:
            pass

    # 2. 导出 PDF (.pdf) 文件
    save_pdf = settings.get("save_pdf", True)
    pdf_file_path = None
    if save_pdf:
        try:
            target_pdf = art_dir / f"{safe_title}.pdf"
            if export_html_to_pdf(html_path, target_pdf, title=safe_title):
                pdf_file_path = target_pdf
        except Exception:
            pass

    # 保存元数据
    meta = {
        "title": safe_title,
        "url": url,
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "publish_time": publish_time,
        "cover_url": cover_url,
        "digest": digest,
        "source": source,
        "author": author,
        "resources_count": len(url_map),
        "videos_count": len(video_iframes),
        "leftover_urls": len(find_mmbiz_urls(localized)),
        "has_clean_text": True,
        "clean_text_file": "content.txt",
        "has_markdown": md_file_path is not None and md_file_path.exists(),
        "markdown_file": f"{safe_title}.md" if (md_file_path and md_file_path.exists()) else "",
        "has_pdf": pdf_file_path is not None and pdf_file_path.exists(),
        "pdf_file": f"{safe_title}.pdf" if (pdf_file_path and pdf_file_path.exists()) else "",
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
        "has_markdown": meta["has_markdown"],
        "markdown_path": str(md_file_path) if md_file_path and md_file_path.exists() else "",
        "has_pdf": meta["has_pdf"],
        "pdf_path": str(pdf_file_path) if pdf_file_path and pdf_file_path.exists() else "",
    }
