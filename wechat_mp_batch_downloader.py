#!/usr/bin/env python3
"""
微信公众号批量下载工具
结合 article_list + scrapling_download，实现指定公众号文章批量下载

用法：
  1. 先扫码登录：python3 wechat_mp_login.py
  2. 配置目标公众号后运行：python3 wechat_mp_batch_downloader.py

依赖：
  pip install scrapling playwright requests && python3 -m playwright install chromium chromium-headless-shell
"""

import sys
import re
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, quote, unquote
from html import unescape

from scrapling.fetchers import DynamicFetcher

# ============================================================
#  ⚡ 配置区
# ============================================================
TARGET_ACCOUNTS  = ["潇湘晨报"]   # 公众号名称列表
MAX_ARTICLES    = 20             # 每个号最多下载篇数（0=不限制）
PAGE_SIZE       = 10             # 每次拉取篇数（最大20）
MAX_RETRIES     = 3              # 单篇最大重试次数
# ============================================================

BASE_URL    = "https://mp.weixin.qq.com"
SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
OUTPUT_DIR  = DATA_DIR / "articles_full"
CONFIG_FILE = DATA_DIR / "wechat_mp_config.json"
HEADERS     = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer":    f"{BASE_URL}/cgi-bin/home?t=home/index&lang=zh_CN",
}


# ============================================================
#  Part 1：登录凭证 & 文章列表获取
# ============================================================

def load_credentials():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["token"], cfg["cookie_str"]


TOKEN, COOKIE = load_credentials()

SESSION = requests.Session()
SESSION.headers.update({**HEADERS, "Cookie": COOKIE})


def check_resp(resp, label=""):
    if resp.status_code != 200:
        raise RuntimeError(f"[{label}] HTTP {resp.status_code}")
    data = resp.json()
    ret  = data.get("base_resp", {}).get("ret", 0)
    if ret != 0:
        err = data.get("base_resp", {}).get("err_msg", "")
        if ret == 200003:
            raise SystemExit(f"[{label}] cookie/token 已失效！请重新运行 wechat_mp_login.py")
        raise RuntimeError(f"[{label}] ret={ret}: {err}")
    return data


def search_account(keyword: str) -> dict | None:
    resp  = SESSION.get(
        f"{BASE_URL}/cgi-bin/searchbiz",
        params={"action": "search_biz", "token": TOKEN, "lang": "zh_CN",
                "f": "json", "ajax": "1", "query": keyword, "begin": "0", "count": "5"},
        timeout=25,
    )
    lst = check_resp(resp, "search").get("list", [])
    return lst[0] if lst else None


def get_article_list(fakeid: str, begin: int, count: int = PAGE_SIZE) -> dict:
    resp = SESSION.get(
        f"{BASE_URL}/cgi-bin/appmsg",
        params={"action": "list_ex", "token": TOKEN, "lang": "zh_CN",
                "f": "json", "ajax": "1", "type": "9", "query": "",
                "fakeid": fakeid, "begin": str(begin), "count": str(count)},
        timeout=25,
    )
    return check_resp(resp, f"list({begin})")


def fetch_account_articles(fakeid: str, name: str) -> list[dict]:
    articles, begin, total = [], 0, None
    while True:
        data     = get_article_list(fakeid, begin)
        batch    = data.get("app_msg_list", [])
        if total is None:
            total = data.get("app_msg_cnt", 0)
            print(f"  📊 共 {total} 篇", end="  ")
        if not batch:
            break
        articles.extend(batch)
        fetched = len(articles)
        cap     = min(fetched, MAX_ARTICLES) if MAX_ARTICLES else fetched
        print(f"⏳{cap}/{total}", end="\r")
        if MAX_ARTICLES and fetched >= MAX_ARTICLES:
            articles = articles[:MAX_ARTICLES];  print(f"  ⚡ 上限 {MAX_ARTICLES} 篇");  break
        if fetched >= total:  break
        begin += len(batch);  time.sleep(0.8)
    print(f"  ✅ 获取 {len(articles)} 篇     ")
    # ── 按发布时间正序（最早的排前面）────────────────────
    def parse_dt(a: dict) -> float:
        s = a.get("datetime", "") or ""
        try:
            # 微信返回的是 Unix 时间戳（秒），但有时是毫秒
            v = int(s)
            return v if v > 1e12 else v * 1000
        except (ValueError, TypeError):
            return 0.0
    articles.sort(key=parse_dt)
    return articles


# ============================================================
#  Part 2：单篇文章下载（Scrapling）
# ============================================================

def sanitize(name: str, mx: int = 60) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name.strip())[:mx].rstrip("_") or "article"


def get_ext(url: str) -> str:
    qs  = parse_qs(urlparse(url).query)
    if "wx_fmt" in qs:  return qs["wx_fmt"][0][:10]
    fn  = urlparse(url).path.rsplit("/", 1)[-1].split("?")[0]
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    return ext if 1 <= len(ext) <= 5 else "jpg"


def download(url: str, path: Path) -> bool:
    try:
        r = requests.get(url, headers={
            "Referer": "https://mp.weixin.qq.com/",
            "User-Agent": HEADERS["User-Agent"],
        }, timeout=60, stream=True)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):  f.write(chunk)
        print(f"    ✅ {path.name} ({path.stat().st_size/1024/1024:.1f} MB)")
        return True
    except Exception as e:
        print(f"    ⚠️  {e}");  return False


def extract_title(page) -> str:
    for sel, attr in [('meta[property="og:title"]::attr(content)', None),
                      ("#activity-name::text", None)]:
        t = page.css(sel).get() if attr is None else page.css(sel).get()
        if t and t.strip():  return t.strip()
    return "article"


def find_mmbiz_urls(html: str) -> set:
    urls = set()
    dec  = html.replace("&amp;", "&")
    for m in re.finditer(r'(?:https?:)?//(?:mmbiz|mpvideo)\.qpic\.cn/[^"\'\\s<>]+', dec):
        u = m.group()
        if u.startswith("//"):  u = "https:" + u
        elif not u.startswith("http"):  u = "https://" + u
        urls.add(u)
    for m in re.finditer(r'%3A%2F%2Fmmbiz\.qpic\.cn%2F[^\'"\\s&%]+', html, re.I):
        d = unquote(m.group()).replace("&amp;", "&")
        if d.startswith("//"):  d = "https:" + d
        elif not d.startswith("http"):  d = "https://" + d
        urls.add(d)
    return urls


def replace_variants(html: str, orig: str, local: str) -> str:
    out = html
    for v in {orig, orig.replace("&", "&amp;")}:
        out = out.replace(f'"{v}"', f'"{local}"')
        out = out.replace(f"'{v}'", f"'{local}'")
        out = out.replace(f"url({v})",  f"url({local})")
        out = out.replace(f"url('{v}')", f"url('{local}')")
        out = out.replace(f'url("{v}")', f'url("{local}")')
    if orig.startswith("https:"):
        rel = orig.replace("https:", "", 1)
        out = out.replace(f'"{rel}"', f'"{local}"')
    enc_o = quote(orig, safe="")
    enc_l = quote(local, safe="")
    if enc_o != orig:  out = out.replace(enc_o, enc_l)
    return out


def download_article(url: str, out_dir: Path) -> bool:
    """下载单篇文章，返回是否成功"""
    media = out_dir / "media"
    media.mkdir(parents=True, exist_ok=True)

    print(f"    🌐 渲染页面...")
    try:
        page = DynamicFetcher.fetch(url, headless=True,
                                    network_idle=True, timeout=60000)
        print(f"    ✅ HTTP {page.status}")
    except Exception as e:
        print(f"    ❌ 渲染失败: {e}");  return False

    title = sanitize(extract_title(page))
    print(f"    📄 {title}")

    # 提取封面 URL
    cover_url = page.css('meta[property="og:image"]::attr(content)').get() or ""
    cover_url = unescape(cover_url).replace("&amp;", "&").strip()
    if cover_url.startswith("//"):
        cover_url = "https:" + cover_url
    elif cover_url.startswith("/"):
        cover_url = "https://mp.weixin.qq.com" + cover_url

    # 提取发布时间
    publish_time = int(datetime.now(timezone.utc).timestamp())
    for script_el in page.css("script"):
        script_text = script_el.html_content or ""
        ct_match = re.search(r'\bct\s*=\s*["\']?(\d+)["\']?', script_text)
        if ct_match:
            publish_time = int(ct_match.group(1))
            break
        pt_match = re.search(r'\bpublish_time\s*=\s*["\']?(\d+)["\']?', script_text)
        if pt_match:
            publish_time = int(pt_match.group(1))
            break

    content = page.css("#js_content")
    if not content:
        print("    ⚠️  未找到 #js_content");  return False
    raw_html = content[0].html_content

    # ── 第1轮：下载正文图片+视频 ───────────────────────────
    url_map, seen = {}, set()
    for el in page.css("#js_content img"):
        src = el.attrib.get("data-src") or el.attrib.get("src") or ""
        if not src or src.startswith("data:"):  continue
        if src.startswith("//"):  src = "https:" + src
        elif src.startswith("/"):  src = "https://mp.weixin.qq.com" + src
        key = re.sub(r"\?.*", "", src)
        if key in seen:  continue
        seen.add(key)
        ext = get_ext(src);  n = len(url_map) + 1
        fname = f"img_{n:03d}.{ext}"
        if download(src, media / fname):
            url_map[src] = f"media/{fname}"

    for el in page.css("#js_content video"):
        src = el.attrib.get("src") or (el.css("source::attr(src)").get() or "")
        if not src:  continue
        if src.startswith("//"):  src = "https:" + src
        elif src.startswith("/"):  src = "https://mp.weixin.qq.com" + src
        ext = get_ext(src) or "mp4";  n = len([v for v in url_map.values() if v.startswith("media/video")]) + 1
        fname = f"video_{n}.{ext}"
        if download(src, media / fname):
            url_map[src] = f"media/{fname}"

    print(f"    ⬇️  第1轮完成，{len(url_map)} 个资源")

    # ── 替换正文 URL ───────────────────────────────────────
    localized = raw_html
    for orig, local in url_map.items():
        localized = replace_variants(localized, orig, local)

    leftover = len(find_mmbiz_urls(localized))

    # ── 保存 HTML ─────────────────────────────────────────
    full = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "  <meta charset=\"UTF-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"  <title>{title}</title>\n"
        "  <style>\n"
        "    body{max-width:680px;margin:0 auto;padding:20px;\n"
        "         font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;\n"
        "         line-height:1.8;color:#333}\n"
        "    img,video{max-width:100%;height:auto;display:block;margin:10px auto}\n"
        "    #js_content, .rich_media_content { visibility: visible !important; }\n"
        "  </style>\n</head>\n<body>\n"
        "<div id=\"js_content\">\n" + localized + "\n</div>\n"
        "</body>\n</html>"
    )
    html_path = out_dir / f"{title}.html"
    html_path.write_text(full, encoding="utf-8")

    # ── 保存原始 HTML ───────────────────────────────────────
    raw_localized = raw_html
    raw_localized = re.sub(r'data-src="([^"]*)"', r'src="\1"', raw_localized)
    raw_full = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "  <meta charset=\"UTF-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"  <title>{title}</title>\n"
        "  <style>\n"
        "    body{max-width:680px;margin:0 auto;padding:20px;\n"
        "         font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;\n"
        "         line-height:1.8;color:#333}\n"
        "    img,video{max-width:100%;height:auto;display:block;margin:10px auto}\n"
        "    #js_content, .rich_media_content { visibility: visible !important; }\n"
        "  </style>\n</head>\n<body>\n"
        "<div id=\"js_content\">\n" + raw_localized + "\n</div>\n"
        "</body>\n</html>"
    )
    (out_dir / f"{title}_raw.html").write_text(raw_full, encoding="utf-8")

    source = out_dir.parent.name
    new_json_data = {
        "source": source,
        "title": title,
        "url": url,
        "cover_url": cover_url,
        "publish_time": publish_time,
        "content": raw_localized
    }
    (out_dir / "data.json").write_text(json.dumps(new_json_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # metadata
    meta = {"title": title, "url": url, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(url_map), "leftover": leftover}
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    sz = sum(f.stat().st_size for f in media.glob("*") if f.is_file())
    print(f"    ✅ 完成（{len(url_map)} 资源，{sz/1024/1024:.1f} MB，剩余外链 {leftover} 处）")
    return True


# ============================================================
#  Part 3：批量主流程
# ============================================================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_ok = total_fail = 0

    for target in TARGET_ACCOUNTS:
        print(f"\n{'='*54}")
        print(f"📌 处理公众号：{target}")
        print(f"{'='*54}")

        # 搜索公众号
        acct = search_account(target)
        if not acct:
            print(f"  ❌ 未找到公众号：{target}");  total_fail += 1;  continue
        fakeid = acct["fakeid"]
        name   = acct["nickname"]
        print(f"  ✅ 找到：{name}（fakeid={fakeid[:10]}...）")

        # 获取文章列表
        try:
            articles = fetch_account_articles(fakeid, name)
        except RuntimeError as e:
            print(f"  ❌ 获取失败：{e}");  total_fail += 1;  continue
        if not articles:
            print(f"  ⚠️  无文章");  total_fail += 1;  continue

        # 保存列表 JSON
        json_path = OUTPUT_DIR / f"{name}_{fakeid[:10]}.json"
        json_path.write_text(json.dumps({"account": name, "fakeid": fakeid,
                                         "articles": articles}, ensure_ascii=False, indent=2))
        print(f"  💾 列表已保存：{json_path.name}")

        # 下载每篇文章
        acct_dir = OUTPUT_DIR / name
        acct_dir.mkdir(parents=True, exist_ok=True)

        for i, art in enumerate(articles, 1):
            link  = art.get("link", "")
            title = art.get("title", "")
            if not link:
                print(f"  [{i}/{len(articles)}] ⚠️  无链接，跳过：{title}")
                continue

            print(f"\n  [{i}/{len(articles)}] {title[:50]}")
            art_dir = acct_dir / sanitize(title)
            art_dir.mkdir(parents=True, exist_ok=True)

            success = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if download_article(link, art_dir):
                        success = True;  break
                except Exception as e:
                    print(f"    ⚠️  第{attempt}次失败：{e}")
                time.sleep(2)

            if success:
                total_ok += 1
                print(f"    🎉 成功")
            else:
                total_fail += 1
                print(f"    ❌ 失败")
            time.sleep(1)

    print(f"\n{'='*54}")
    print(f"🎉 全部完成！")
    print(f"  ✅ 成功：{total_ok} 篇")
    print(f"  ❌ 失败：{total_fail} 篇")
    print(f"  📂 输出目录：{OUTPUT_DIR}")
    print(f"{'='*54}\n")


if __name__ == "__main__":
    main()
