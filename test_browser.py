#!/usr/bin/env python3
"""
简单测试 Playwright 浏览器启动
"""
from playwright.sync_api import sync_playwright
import time

print("测试 Playwright 浏览器启动...")

try:
    with sync_playwright() as p:
        print("✅ Playwright 初始化成功")

        print("正在启动 Chromium 浏览器（非 headless）...")
        browser = p.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )
        print("✅ 浏览器启动成功")

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800}
        )
        print("✅ 浏览器上下文创建成功")

        page = context.new_page()
        print("✅ 页面创建成功")

        print("正在访问抖音...")
        page.goto("https://www.douyin.com/", timeout=30000)
        print("✅ 页面加载成功")

        print("等待 5 秒...")
        time.sleep(5)

        browser.close()
        print("✅ 浏览器关闭成功")

        print("\n🎉 所有测试通过！浏览器可以正常启动")

except Exception as e:
    print(f"\n❌ 错误: {e}")
    import traceback
    traceback.print_exc()
