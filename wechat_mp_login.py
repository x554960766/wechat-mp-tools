#!/usr/bin/env python3
"""
微信读书账号登录工具
运行后获取微信读书扫码 URL → 扫码 → 自动保存 vid + token
保存路径：脚本所在目录 / data / wechat_mp_config.json
"""

import json
import time
import requests
from pathlib import Path

# ── 路径（动态，基于脚本所在目录）──────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
CONFIG_FILE = DATA_DIR / "wechat_mp_config.json"
PLATFORM_URL = "https://weread.111965.xyz"


def login():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("🚀 正在获取微信读书扫码登录二维码...")

    try:
        resp = requests.get(f"{PLATFORM_URL}/api/v2/login/platform", timeout=15)
        if resp.status_code != 200:
            print(f"❌ 请求失败: HTTP {resp.status_code}")
            return False
        data = resp.json()
        uuid = data.get("uuid")
        scan_url = data.get("scanUrl")
        if not uuid or not scan_url:
            print("❌ 获取扫码凭证失败")
            return False

        # 生成包含二维码图片的本地 HTML 页面并在浏览器中打开
        import urllib.parse
        qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?size=260x260&data={urllib.parse.quote(scan_url)}"
        html_file = DATA_DIR / "login_qrcode.html"
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>微信读书扫码登录</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #f7f9fa; color: #333; }}
        .card {{ background: white; border-radius: 16px; padding: 32px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); text-align: center; max-width: 360px; }}
        h2 {{ margin-top: 0; color: #111; font-size: 1.25rem; }}
        p {{ color: #666; font-size: 0.9rem; line-height: 1.5; }}
        .qr-box {{ margin: 20px 0; padding: 12px; border: 1px solid #eee; border-radius: 12px; display: inline-block; background: #fff; }}
        img {{ display: block; border-radius: 8px; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>📱 微信扫码登录</h2>
        <p>打开手机微信 APP -> 扫一扫，扫描下方二维码并在手机上确认登录</p>
        <div class="qr-box">
            <img src="{qr_img_url}" width="260" height="260" alt="二维码" />
        </div>
        <p style="font-size: 0.8rem; color: #888;">登录成功后本页面可直接关闭</p>
    </div>
</body>
</html>"""
        html_file.write_text(html_content, encoding="utf-8")

        print()
        print("=" * 55)
        print("  📱 已为您在浏览器中打开二维码页面！")
        print(f"  如果未自动打开，请用浏览器打开: {html_file.to_uri()}")
        print("=" * 55)
        print()

        try:
            import webbrowser
            webbrowser.open(html_file.to_uri())
        except Exception:
            pass

        print("⌛ 正在轮询等待扫码结果...")
        start_time = time.time()
        while time.time() - start_time < 300:
            time.sleep(3)
            try:
                res = requests.get(f"{PLATFORM_URL}/api/v2/login/platform/{uuid}", timeout=30)
                res_data = res.json()
                vid = res_data.get("vid")
                token = res_data.get("token")
                if vid and token:
                    username = res_data.get("username") or f"WeRead_{vid}"
                    print(f"✅ 登录成功！用户: {username}, vid: {vid}")

                    config = {
                        "vid": str(vid),
                        "token": token,
                        "nickname": username,
                        "save_time": time.time(),
                    }
                    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2))
                    print(f"💾 凭证已保存至: {CONFIG_FILE}")
                    return True

                msg = res_data.get("message", "等待扫码...")
                print(f"  ... {msg}")
            except Exception as e:
                print(f"  ... 轮询重试: {e}")

        print("❌ 扫码登录超时（5分钟）")
        return False
    except Exception as e:
        print(f"❌ 登录过程出现异常: {e}")
        return False


if __name__ == "__main__":
    login()
