import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
        await page.goto("https://www.douyin.com/", wait_until="load")
        
        try:
            login_btn = await page.wait_for_selector('div:has-text("登录")', state="visible", timeout=10000)
            if login_btn:
                await login_btn.click()
        except:
            pass
            
        await asyncio.sleep(5)
        html = await page.content()
        with open("dy_login.html", "w") as f:
            f.write(html)
        await browser.close()

asyncio.run(main())
