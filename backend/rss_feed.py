"""RSS 2.0 订阅输出

替代 backend/articles.py 里原来的 get_rss 内联实现。改动动机与效果见
deploy/rss/README.md，核心是四点：

1. rss_articles.json 每次请求只读一次并建索引。原实现按订阅数循环调用
   rss_scheduler.get_articles()，每个账号都把整个文件重新解析一遍（N 次全量
   JSON 解析），订阅越多越慢。
2. 单次输出有上限（limit / offset），按发布时间倒序。原实现把全部历史 + 全部
   订阅文章一次性塞进一个 feed，账号多了会生成几 MB 的 XML。
3. 进程内结果缓存 + ETag + 304。原实现每次轮询都要重新读文件、重新读
   content.txt、重新拼字符串。
4. 反向代理下用 X-Forwarded-* 或显式 rss_base_url 生成链接。原实现用
   request.host_url，容器/反代后面会生成 http://内网地址 的订阅链接。
"""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import threading
import time
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote

from flask import Response

from backend.avatar_cache import article_avatar, avatar_account_ids, cache_avatar
from backend.config import (
    ACCOUNTS_FILE,
    DATA_DIR,
    DOWNLOAD_HISTORY_FILE,
    get_settings,
    load_json,
    normalize_wechat_url,
)
from backend.wechat_url import normalize_wechat_short_url

RSS_ARTICLES_FILE = DATA_DIR / "rss_articles.json"

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
DEFAULT_TTL = 300
MAX_CONTENT_CHARS = 300_000
EXCLUDED_ACCOUNTS = {"微信视频号"}

_cache: dict = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------- 数据加载


def _file_mtimes() -> tuple:
    """文章与公众号元数据三个文件的修改时间，作为缓存失效依据。"""
    out = []
    for path in (DOWNLOAD_HISTORY_FILE, RSS_ARTICLES_FILE, ACCOUNTS_FILE):
        try:
            out.append(path.stat().st_mtime_ns)
        except OSError:
            out.append(0)
    return tuple(out)


def get_account_avatar(account: str, items: list | None = None,
                       from_article: bool = True) -> str:
    """仅使用能确认归属的头像；昵称有歧义时省略头像。"""
    accounts = load_json(ACCOUNTS_FILE, [])
    matches = [item for item in (accounts if isinstance(accounts, list) else [])
               if isinstance(item, dict) and item.get("nickname") == account]
    identities = [avatar_account_ids(item) for item in matches]
    account_ids = set().union(*identities)
    # 现有 RSS 地址以昵称定位，不能替同名的不同账号任意选一张头像。
    if len(account_ids) != 1 or any(ids != account_ids for ids in identities):
        return ""
    for item in matches:
        avatar = str(item.get("round_head_img") or "").strip()
        if avatar.startswith(("https://", "http://")):
            return avatar
    if not from_article:
        return ""
    account_id = next(iter(account_ids))
    candidates = []
    for item in items or []:
        item_ids = avatar_account_ids(item)
        if item.get("account") == account and (not item_ids or item_ids == {account_id}):
            candidates.append(item)
            if len(candidates) == 3:
                break
    for item in candidates:
        if not item.get("path"):
            continue
        metadata = load_json(Path(item["path"]) / "metadata.json", {})
        if (isinstance(metadata, dict) and avatar_account_ids(metadata) == {account_id}
                and (not metadata.get("source") or metadata["source"] == account)
                and (not metadata.get("url") or not item.get("link")
                     or normalize_wechat_url(metadata["url"]) == normalize_wechat_url(item["link"]))):
            avatar = str(metadata.get("round_head_img") or "").strip()
            if avatar.startswith(("https://", "http://")):
                return avatar
    for item in candidates:
        if item.get("link"):
            avatar = article_avatar(item["link"], account_id)
            if avatar:
                return avatar
    return ""


def _load_all_articles() -> dict:
    """一次性读出 {公众号: [文章...]}，不按账号重复解析。"""
    data = load_json(RSS_ARTICLES_FILE, {})
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, list)}


def _load_history() -> list:
    data = load_json(DOWNLOAD_HISTORY_FILE, [])
    return data if isinstance(data, list) else []


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cfg(settings: dict, key: str, env: str, default):
    """环境变量优先，其次设置文件，最后默认值。

    环境变量只在部署时显式设置（Docker 的 .env），所以它代表部署级配置；
    桌面版不会设这些变量，自然走 app_settings.json。
    """
    env_val = os.environ.get(env)
    if env_val is not None and str(env_val).strip() != "":
        return env_val

    value = settings.get(key)
    if value not in (None, ""):
        return value
    return default


def _cfg_int(settings: dict, key: str, env: str, default: int) -> int:
    return int(_to_float(_cfg(settings, key, env, default), default))


def _cfg_bool(settings: dict, key: str, env: str, default: bool) -> bool:
    return str(_cfg(settings, key, env, default)).strip().lower() not in ("0", "false", "no", "off")


def _item_key(link: str, account: str, title: str) -> str:
    normalized = normalize_wechat_url(link) if link else ""
    if normalized:
        return normalized
    return f"{account}\x00{title}"


def _normalize_item(raw: dict, account: str) -> dict | None:
    """把下载历史条目或 RSS 缓存条目统一成内部结构。"""
    if not isinstance(raw, dict):
        return None
    link = raw.get("link") or raw.get("url") or ""
    link = normalize_wechat_short_url(str(link)) if link else ""
    title = raw.get("title") or ""
    if not link and not title:
        return None
    published = (
        raw.get("publish_time")
        or raw.get("update_time")
        or raw.get("time")
        or 0
    )
    # 公众号分组应优先使用准确的公众号名称（account 或 source），避免使用文章作者署名
    acc_name = account or str(raw.get("account") or raw.get("source") or "")
    if not acc_name:
        acc_name = str(raw.get("author") or "")

    # 文章署名作者单独保存（与公众号名称区分开）
    author = str(raw.get("author") or "")
    path = str(raw.get("path") or "")
    if not author and path:
        try:
            meta = load_json(Path(path) / "metadata.json", {})
            if isinstance(meta, dict):
                author = str(meta.get("author") or "")
        except Exception:
            pass

    return {
        "title": str(title),
        "link": str(link),
        "account": acc_name,
        "author": author,
        "published": _to_float(published),
        "digest": str(raw.get("digest") or ""),
        "cover": str(raw.get("cover_url") or raw.get("cover") or ""),
        "path": path,
    }


def collect_items(account: str | None = None) -> list:
    """合并「手动下载历史」与「订阅抓取缓存」，按链接去重、按时间倒序。"""
    articles = _load_all_articles()
    history = _load_history()

    merged: dict = {}
    order: list = []

    def push(raw, acc):
        item = _normalize_item(raw, acc)
        if not item:
            return
        if item["account"] in EXCLUDED_ACCOUNTS:
            return
        if account and item["account"] != account:
            return
        key = _item_key(item["link"], item["account"], item["title"])
        if key in merged:
            # 同一篇：补全缺失字段，保留更早拿到的那条
            exist = merged[key]
            for field in ("digest", "cover", "path", "author"):
                if not exist.get(field) and item.get(field):
                    exist[field] = item[field]
            if not exist.get("published") and item.get("published"):
                exist["published"] = item["published"]
            return
        merged[key] = item
        order.append(key)

    if account:
        for raw in articles.get(account, []):
            push(raw, account)
        for raw in history:
            if isinstance(raw, dict) and raw.get("success"):
                push(raw, raw.get("account") or account)
    else:
        for acc, arts in articles.items():
            for raw in arts:
                push(raw, acc)
        for raw in history:
            if isinstance(raw, dict) and raw.get("success"):
                push(raw, raw.get("account") or "")

    items = [merged[k] for k in order]
    items.sort(key=lambda x: x["published"], reverse=True)
    return items


# ---------------------------------------------------------------- XML 渲染


def _text(value: str) -> str:
    return html.escape(str(value or ""), quote=False)


def _attr(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _cdata(value: str) -> str:
    # CDATA 内出现 ]]> 必须拆成 ]]]]><![CDATA[>
    return "<![CDATA[%s]]>" % str(value or "").replace("]]>", "]]]]><![CDATA[>")


_RSS_STRIP_BLOCKS = re.compile(r"<(script|style|iframe|noscript)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_RSS_EVENT_ATTRS = re.compile(r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)
_RSS_RELATIVE_URL = re.compile(r"""(\s(?:src|href|poster)\s*=\s*)(["'])(?!(?:[a-z][a-z0-9+.-]*:|//|#))([^"']+)\2""", re.IGNORECASE)
_RSS_RELATIVE_CSS_URL = re.compile(r"""(url\(\s*(?:&quot;|["'])?)(media/[^)"'&]+)""", re.IGNORECASE)


def _find_article_html(art_dir: Path) -> Path | None:
    """定位文章目录中实际保存的离线 HTML"""
    if not art_dir.is_dir():
        return None
    candidates = sorted(f for f in art_dir.glob("*.html") if not f.name.endswith(("_raw.html", ".bak")))
    for c in candidates:
        if c.stem == art_dir.name:
            return c
    return candidates[0] if candidates else None


def _rss_article_body(html_text: str, base_url: str) -> str:
    """提取离线 HTML 的 #js_content 正文，保留富文本格式与图片"""
    start = re.search(r'<div id="js_content"[^>]*>', html_text)
    if start:
        end = html_text.rfind("</div>")
        body = html_text[start.end():end if end > start.end() else None]
    else:
        m = re.search(r"<body[^>]*>(.*)</body>", html_text, re.IGNORECASE | re.DOTALL)
        body = m.group(1) if m else html_text
    body = _RSS_STRIP_BLOCKS.sub("", body)
    body = _RSS_EVENT_ATTRS.sub("", body)
    body = _RSS_RELATIVE_URL.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{base_url}{quote(m.group(3), safe='/%?=&;')}{m.group(2)}",
        body,
    )
    body = _RSS_RELATIVE_CSS_URL.sub(lambda m: f"{m.group(1)}{base_url}{quote(m.group(2))}", body)
    return body.strip()


def _read_content(item: dict, base_url: str) -> str:
    path = item.get("path") or ""
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.exists():
            return ""

        # 优先读取离线 HTML，保留完整富文本与图片排版
        html_file = _find_article_html(p)
        if html_file:
            parent, name = p.parent.name, p.name
            art_base_url = "%sapi/articles/serve-file/%s/%s/" % (
                base_url,
                quote(parent, safe=""),
                quote(name, safe=""),
            )
            body = _rss_article_body(html_file.read_text(encoding="utf-8", errors="replace"), art_base_url)
            if body:
                if len(body) > MAX_CONTENT_CHARS:
                    body = body[:MAX_CONTENT_CHARS]
                return body

        # 回落方案：读取 content.txt 并用段落包裹
        txt = p / "content.txt"
        if txt.is_file():
            text = txt.read_text(encoding="utf-8", errors="replace")
            lines = [f"<p>{html.escape(ln)}</p>" for ln in text.splitlines() if ln.strip()]
            body = "\n".join(lines)
            if len(body) > MAX_CONTENT_CHARS:
                body = body[:MAX_CONTENT_CHARS]
            return body
    except OSError:
        return ""
    return ""


def _local_link(base_url: str, item: dict) -> str:
    path = item.get("path") or ""
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.exists():
            return ""
        html_file = _find_article_html(p)
        parent, name = p.parent.name, p.name
        file_name = html_file.name if html_file else f"{name}.html"
    except OSError:
        return ""
    return "%sapi/articles/serve-file/%s/%s/%s" % (
        base_url,
        quote(parent, safe=""),
        quote(name, safe=""),
        quote(file_name, safe=""),
    )


def render_feed(items: list, title: str, description: str,
                base_url: str, fulltext: bool = True, image_url: str = "") -> str:
    xml_items = []
    for item in items:
        # 本地有落盘文件就走本地预览链接，否则回落到公众号原始链接
        xml_link = _local_link(base_url, item) or item["link"]
        if not xml_link:
            continue

        pub_ts = item["published"] or time.time()
        parts = [
            "    <item>",
            "      <title>%s</title>" % _text(item["title"]),
            "      <link>%s</link>" % _text(xml_link),
            "      <guid isPermaLink=\"false\">%s</guid>" % _text(item["link"] or xml_link),
            "      <pubDate>%s</pubDate>" % formatdate(pub_ts, usegmt=True),
            "      <description>%s</description>" % _text(item["digest"]),
        ]
        if item.get("author"):
            parts.append("      <dc:creator>%s</dc:creator>" % _text(item["author"]))
        if item["cover"]:
            parts.append(
                '      <enclosure url="%s" type="image/jpeg" length="0"/>'
                % _attr(item["cover"])
            )
        if fulltext:
            content = _read_content(item, base_url)
            if content:
                parts.append(
                    "      <content:encoded>%s</content:encoded>" % _cdata(content)
                )
        parts.append("    </item>")
        xml_items.append("\n".join(parts))

    image_xml = ""
    if image_url:
        image_xml = (
            "    <image>\n"
            "      <url>%s</url>\n"
            "      <title>%s</title>\n"
            "      <link>%s</link>\n"
            "    </image>\n"
        ) % (_text(image_url), _text(title), _text(base_url))

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        "  <channel>\n"
        "    <title>%s</title>\n"
        "    <link>%s</link>\n"
        "    <description>%s</description>\n"
        "%s"
        "    <language>zh-CN</language>\n"
        "    <generator>wechat-mp-tools</generator>\n"
        "    <lastBuildDate>%s</lastBuildDate>\n"
        "%s\n"
        "  </channel>\n"
        "</rss>"
    ) % (
        _text(title),
        _text(base_url),
        _text(description),
        image_xml,
        formatdate(time.time(), usegmt=True),
        "\n".join(xml_items),
    )


# ---------------------------------------------------------------- 对外入口


def _resolve_base_url(request, settings: dict) -> str:
    explicit = str(_cfg(settings, "rss_base_url", "RSS_BASE_URL", "") or "").strip()
    if explicit:
        if not explicit.startswith(("http://", "https://")):
            explicit = "https://" + explicit
        return explicit.rstrip("/") + "/"
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = request.headers.get("X-Forwarded-Host") or request.host
    return "%s://%s/" % (proto.split(",")[0].strip(), host)


def _check_token(request, settings: dict):
    expected = str(_cfg(settings, "rss_token", "RSS_TOKEN", "") or "").strip()
    if not expected:
        return None
    supplied = request.args.get("token") or ""
    if not supplied:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        return Response(
            "RSS access denied",
            status=401,
            mimetype="text/plain",
            headers={"WWW-Authenticate": 'Bearer realm="rss"'},
        )
    return None


def build_feed(account: str | None, limit: int, offset: int,
               fulltext: bool, base_url: str) -> tuple[str, str]:
    """返回 (xml, etag)，结果按 (参数 + 文件 mtime) 缓存在进程内。"""
    settings = get_settings()
    ttl = _cfg_int(settings, "rss_cache_ttl", "RSS_CACHE_TTL", DEFAULT_TTL)
    local_avatar = _cfg_bool(settings, "rss_local_avatar", "RSS_LOCAL_AVATAR", True)
    from_article = _cfg_bool(settings, "rss_avatar_from_article", "RSS_AVATAR_FROM_ARTICLE", True)
    current_mtimes = _file_mtimes()
    key = (account or "", limit, offset, bool(fulltext), base_url,
           local_avatar, from_article, current_mtimes)

    now = time.time()
    with _cache_lock:
        # 主动清理过期或因底层文件更新而失效的旧缓存，防止全文字符串堆积导致内存泄漏
        stale_keys = [
            k for k, v in _cache.items()
            if (ttl > 0 and (now - v[0]) >= ttl) or (len(k) >= 8 and k[7] != current_mtimes)
        ]
        for k in stale_keys:
            _cache.pop(k, None)

        hit = _cache.get(key)
        if hit and (ttl <= 0 or now - hit[0] < ttl):
            return hit[1], hit[2]

    items = collect_items(account)
    # limit <= 0 表示不限制（桌面版默认），返回 offset 之后的全部
    window = items[offset:] if limit <= 0 else items[offset:offset + limit]

    if account:
        title = account
        description = f"{account}的微信公众号文章"
        image_url = get_account_avatar(account, items, from_article=from_article)
        if image_url and local_avatar:
            filename = cache_avatar(image_url)
            if filename:
                image_url = f"{base_url}api/articles/avatars/{filename}"
    else:
        title = "微信公众号 RSS"
        description = "微信公众号文章订阅"
        image_url = ""

    xml = render_feed(window, title, description, base_url,
                      fulltext=fulltext, image_url=image_url)
    etag = 'W/"%s"' % hashlib.sha1(xml.encode("utf-8")).hexdigest()[:20]

    with _cache_lock:
        # 控制缓存容量在极小范围（至多保留最近 8 个），超限时淘汰一半
        if len(_cache) >= 8:
            oldest_keys = sorted(_cache.keys(), key=lambda k: _cache[k][0])[:4]
            for k in oldest_keys:
                _cache.pop(k, None)
        _cache[key] = (now, xml, etag)
    return xml, etag


def clear_rss_cache():
    """主动清空 RSS 渲染结果缓存"""
    with _cache_lock:
        _cache.clear()


def rss_response(request, account: str | None = None) -> Response:
    settings = get_settings()

    denied = _check_token(request, settings)
    if denied is not None:
        return denied

    raw_limit = request.args.get("limit")
    if raw_limit is not None and str(raw_limit).strip() != "":
        try:
            limit = max(1, min(int(raw_limit), MAX_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
    else:
        # 未显式指定时看配置：0 表示不限制，保持桌面版原有行为
        configured = _cfg_int(settings, "rss_max_items", "RSS_MAX_ITEMS", 0)
        limit = max(1, min(configured, MAX_LIMIT)) if configured > 0 else 0

    try:
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    fulltext = str(request.args.get("full", "1")).lower() not in ("0", "false", "no")

    base_url = _resolve_base_url(request, settings)
    xml, etag = build_feed(account, limit, offset, fulltext, base_url)

    ttl = _cfg_int(settings, "rss_cache_ttl", "RSS_CACHE_TTL", DEFAULT_TTL)
    mtime_ns = max(_file_mtimes()) or 0
    headers = {
        "ETag": etag,
        "Cache-Control": ("public, max-age=%d" % ttl) if ttl > 0 else "no-cache",
        "Last-Modified": formatdate(mtime_ns / 1e9 if mtime_ns else time.time(), usegmt=True),
    }

    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers=headers)

    return Response(xml, mimetype="application/xml", headers=headers)
