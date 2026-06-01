#!/usr/bin/env python3
"""
微信公众号文章批量获取工具（方式一：appmsgpublish 后台接口）

原理：
  利用微信公众平台后台「写文章 → 搜索其他公众号文章」的官方功能 API，
  无需 Playwright 打开浏览器，直接用 cookie + token 调 appmsgpublish 接口拉取。

  API：GET https://mp.weixin.qq.com/cgi-bin/appmsgpublish
  这是微信官方后台搜索接口，token 由 wechat_mp_login.py 扫码登录后自动保存。

使用方式：
  1. 先运行 python3 wechat_mp_login.py 扫码登录（只需一次，cookie + token 长期有效）
  2. 运行本脚本：python3 wechat_mp_article_fetcher.py

对比旧版：
  - 旧版需要 Playwright 打开浏览器 → 提取动态 key → 调 profile_ext?action=getmsg
  - 新版直接用 requests 调 appmsgpublish，更简洁、更稳定、不需要浏览器

所有数据文件保存在脚本所在目录的 data/wechat_articles/ 子目录下。
"""

import json
import time
import re
import requests
from pathlib import Path
from datetime import datetime

# ============================================================
#  ⚡ 配置区
# ============================================================

# ── 目标公众号列表（名称 -> fakeid / __biz）─────────────────────
# fakeid 就是公众号的 __biz 参数（Base64 字符串）
TARGET_ACCOUNTS = {
    "潇湘晨报": "Mjk0MDY5NjMyMA==",
}

PAGE_SIZE    = 10    # 每次拉取篇数（官方限制最大20，建议10稳妥）
MAX_ARTICLES = 10    # 最多抓取篇数（0=不限制）

# ── 路径（动态，基于脚本所在目录）─────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
OUTPUT_DIR  = DATA_DIR / "wechat_articles"
CONFIG_FILE = DATA_DIR / "wechat_mp_config.json"
# ============================================================


def load_credentials():
    """从配置文件读取 token + cookie（由 wechat_mp_login.py 生成）"""
    if not CONFIG_FILE.exists():
        raise RuntimeError(
            "\n❌ 未找到凭证！请先运行：\n"
            "     python3 wechat_mp_login.py   （扫码登录，自动保存）\n"
            f"  配置文件路径: {CONFIG_FILE}"
        )
    cfg = json.loads(CONFIG_FILE.read_text())
    token = cfg.get("token", "")
    cookie_str = cfg.get("cookie_str", "")
    if not token or not cookie_str:
        raise RuntimeError(
            "\n❌ 凭证不完整（token 或 cookie 为空）！\n"
            "  请重新运行：python3 wechat_mp_login.py"
        )
    print(f"  📂 已从配置文件加载凭证（token={token[:8]}...）")
    return token, cookie_str, cfg


def fetch_page_via_appmsgpublish(
    cookie_str: str,
    token: str,
    fakeid: str,
    begin: int,
    count: int,
    keyword: str = "",
) -> dict:
    """
    方式一：调用微信后台 appmsgpublish 接口获取文章列表

    API: GET https://mp.weixin.qq.com/cgi-bin/appmsgpublish

    参数说明：
      sub=list       — 获取全部文章列表（search=按关键字搜索）
      begin=0        — 分页偏移量
      count=10       — 每页数量（最大 20）
      fakeid=xxx     — 公众号唯一标识
      token=xxx      — 后台登录 token（由 wechat_mp_login.py 获取）
    """
    url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"

    is_searching = bool(keyword)
    params = {
        "sub":               "search" if is_searching else "list",
        "search_field":      "7" if is_searching else "null",
        "begin":             str(begin),
        "count":             str(count),
        "query":             keyword,
        "fakeid":            fakeid,
        "type":              "101_1",
        "free_publish_type": "1",
        "sub_action":        "list_ex",
        "token":             token,
        "lang":              "zh_CN",
        "f":                 "json",
        "ajax":              "1",
    }

    headers = {
        "Cookie":      cookie_str,
        "User-Agent":  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Referer":     "https://mp.weixin.qq.com/",
        "Origin":      "https://mp.weixin.qq.com",
        "Accept":      "application/json, text/plain, */*",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=30)

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    base_resp = data.get("base_resp", {})

    # 检查登录态
    ret = base_resp.get("ret", 0)
    if ret == 200003:
        raise RuntimeError(
            "\n❌ 登录已过期（session expired）！\n"
            "  请重新运行：python3 wechat_mp_login.py"
        )
    if ret != 0:
        err_msg = base_resp.get("err_msg", "未知错误")
        raise RuntimeError(f"微信API错误 (ret={ret}): {err_msg}")

    return data


def parse_articles_from_response(data: dict) -> tuple[list[dict], int]:
    """
    从 appmsgpublish 返回中提取文章列表

    返回结构（两层 JSON 字符串嵌套）：
    {
      "base_resp": {"ret": 0, ...},
      "publish_page": '{"publish_list":[{...}], "total_count":150}'
    }
    → publish_page.publish_list[].publish_info（又是一层 JSON 字符串）:
      '{"appmsgex":[{title,link,cover,...}]}'
    """
    publish_page_str = data.get("publish_page", "")
    if not publish_page_str:
        return [], 0

    publish_page = json.loads(publish_page_str)
    publish_list = publish_page.get("publish_list", [])
    total_count = publish_page.get("total_count", 0)

    articles = []
    for item in publish_list:
        publish_info_str = item.get("publish_info", "")
        if not publish_info_str:
            continue
        publish_info = json.loads(publish_info_str)
        appmsgex = publish_info.get("appmsgex", [])
        for a in appmsgex:
            articles.append(normalize_article(a))

    return articles, total_count


def normalize_article(item: dict) -> dict:
    """将 appmsgpublish 返回的文章数据标准化"""
    return {
        "title":       item.get("title", ""),
        "link":        item.get("link", ""),
        "cover":       item.get("cover", ""),
        "digest":      item.get("digest", ""),
        "author":      item.get("author", ""),
        "update_time": item.get("update_time", item.get("create_time", 0)),
        "is_original": item.get("copyright_type", "0") != "0",
        "item_show_type": item.get("item_show_type", 0),
    }


def fetch_all_articles(
    token: str,
    cookie_str: str,
    fakeid: str,
    account_name: str,
) -> list[dict]:
    """
    完整流程：分页调用 appmsgpublish，拉取全部文章

    分页逻辑：begin 从 0 开始，每次 +count，直到返回列表为空
    """
    all_articles = []
    begin = 0

    print(f"  📡 正在获取 [{account_name}] 的文章列表（方式一：appmsgpublish）...")

    while True:
        # 检查上限
        if MAX_ARTICLES > 0 and len(all_articles) >= MAX_ARTICLES:
            all_articles = all_articles[:MAX_ARTICLES]
            print(f"\n  ⚡ 已达上限 {MAX_ARTICLES} 篇，停止抓取")
            break

        data = fetch_page_via_appmsgpublish(
            cookie_str, token, fakeid, begin, PAGE_SIZE
        )

        articles, total_count = parse_articles_from_response(data)

        if not articles:
            # 返回为空 → 全部拉完
            break

        all_articles.extend(articles)
        fetched = len(all_articles)
        target = min(fetched, MAX_ARTICLES) if MAX_ARTICLES > 0 else fetched
        total_msg = f"（总计 {total_count} 篇）" if total_count else ""
        print(f"  ⬇️  已获取 {target}/{total_count if total_count else '未知'} 篇", end="\r")

        if MAX_ARTICLES > 0 and fetched >= MAX_ARTICLES:
            all_articles = all_articles[:MAX_ARTICLES]
            print(f"\n  ⚡ 已达上限 {MAX_ARTICLES} 篇，停止抓取")
            break

        begin += PAGE_SIZE

        # 适当延迟，避免频率过高
        time.sleep(0.5)

    print(f"\n  ✅ [{account_name}] 共获取 {len(all_articles)} 篇文章")
    return all_articles


def save_results(account_name: str, fakeid: str, articles: list[dict]):
    """保存文章列表到 JSON 和 Markdown"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", account_name)

    # JSON（完整数据）
    json_path = OUTPUT_DIR / f"{safe_name}_{fakeid[:10]}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"account": account_name, "fakeid": fakeid, "articles": articles},
            f, ensure_ascii=False, indent=2,
        )
    print(f"  💾 JSON 已保存: {json_path}")

    # Markdown（简洁列表，含链接）
    md_path = OUTPUT_DIR / f"{safe_name}_{fakeid[:10]}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {account_name} — 文章列表\n\n")
        f.write(f"- 公众号 fakeid: `{fakeid}`\n")
        f.write(f"- 获取时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"- 共 {len(articles)} 篇文章\n\n")
        f.write("---\n\n")
        for i, a in enumerate(articles, 1):
            title   = a.get("title", "")
            link    = a.get("link", "")
            cover   = a.get("cover", "")
            digest  = a.get("digest", "")
            author  = a.get("author", "")
            ctime   = a.get("update_time", 0)
            date_str = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d") if ctime else ""
            f.write(f"{i}. **{title}**  {date_str}")
            if author:
                f.write(f"  @{author}")
            f.write(f"\n")
            if digest:
                f.write(f"   > {digest}\n")
            if cover:
                f.write(f"   ![封面]({cover})\n")
            f.write(f"   {link}\n\n")
    print(f"  📝 Markdown 已保存: {md_path}")
    return json_path, md_path


# ──────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────

def main():
    print("🚀 微信公众号文章获取工具（方式一：appmsgpublish 后台接口）")
    print(f"   目标账号数 : {len(TARGET_ACCOUNTS)}")
    print(f"   输出目录   : {OUTPUT_DIR}")
    print(f"   配置文件   : {CONFIG_FILE}")
    print()

    token, cookie_str, _ = load_credentials()

    for account_name, fakeid in TARGET_ACCOUNTS.items():
        print(f"🔍 处理: {account_name} (fakeid={fakeid[:20]}...)")

        try:
            articles = fetch_all_articles(
                token, cookie_str, fakeid, account_name,
            )
        except RuntimeError as e:
            print(f"  ❌ 获取失败: {e}")
            print()
            continue

        if articles:
            save_results(account_name, fakeid, articles)
        else:
            print(f"  ⚠️  [{account_name}] 没有文章")
        print()

    print("🎉 全部完成！")


if __name__ == "__main__":
    main()
