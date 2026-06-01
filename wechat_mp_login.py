#!/usr/bin/env python3
"""
微信公众号后台自动登录工具
运行后浏览器打开微信公众平台 → 扫码 → 自动保存 cookie + token

依赖：pip install playwright && playwright install chromium
保存路径：脚本所在目录 / data / wechat_mp_config.json
"""

import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── 路径（动态，基于脚本所在目录）──────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
CONFIG_FILE = DATA_DIR / "wechat_mp_config.json"
# ============================================================


def login():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--window-size=1280,900"])
        ctx  = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        print("🚀 正在打开微信公众平台...")
        page.goto("https://mp.weixin.qq.com/", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        print()
        print("=" * 50)
        print("  📱 请用微信扫描浏览器中的二维码登录")
        print("  （如果已登录，请先退出登录再运行本脚本）")
        print("=" * 50)
        print()

        # 等待登录成功后的首页跳转（最多等 5 分钟）
        try:
            page.wait_for_url("**/cgi-bin/home**", timeout=300_000)
        except PWTimeout:
            print("❌ 扫码超时（5分钟），请重新运行脚本")
            browser.close()
            return False

        # 从 URL 提取 token
        url_params = parse_qs(urlparse(page.url).query)
        token = url_params.get("token", [""])[0]

        if not token:
            print("⚠️  未能从 URL 提取 token，请检查是否登录成功")
            print(f"   当前 URL: {page.url}")
            browser.close()
            return False

        print(f"✅ 登录成功！token = {token[:12]}...")

        # 保存 cookies（playwright 格式，可直接 load）
        cookies = ctx.cookies()
        # 同时存一份 "Cookie" 请求头格式的字符串（给 requests 用）
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        config = {
            "token":      token,
            "cookie_str":  cookie_str,   # 给 requests 用的字符串
            "cookies":     cookies,       # 给 playwright 用的列表
            "save_time":   __import__("time").time(),
        }
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2))
        print(f"💾 凭证已保存至: {CONFIG_FILE}")
        print()
        print("现在可以运行 wechat_mp_article_fetcher.py 获取文章列表了！")
        browser.close()
        return True


if __name__ == "__main__":
    login()
