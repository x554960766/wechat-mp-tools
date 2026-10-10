#!/usr/bin/env python3
"""
微信读书账号扫码登录与凭证配置工具
生成微信读书 App 官方授权二维码并在授权后换取长效 Token 保存至账号池
保存路径：data / account_pool.json
"""

import sys
import time
import json
from pathlib import Path

SCRIPT_DIR  = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from backend.auth import get_qr_data, exchange_weread_token
from backend.account_pool import account_pool


def login():
    print("=" * 60)
    print(" 🚀 微信读书官方授权扫码登录")
    print("=" * 60)
    print("正在请求微信读书官方登录二维码...")

    try:
        qr_info = get_qr_data()
        uuid_val = qr_info.get("uuid")
        if not uuid_val:
            print("❌ 获取二维码失败，未能生成 uuid")
            return False

        qr_url = f"https://open.weixin.qq.com/connect/qrcode/{uuid_val}"
        print(f"\n请在浏览器打开以下链接扫码，或使用微信扫码：")
        print(f"👉 {qr_url}\n")

        # 尝试使用 qrcode 打印终端二维码
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(qr_url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except Exception:
            pass

        print("正在等待微信确认授权 (有效时间 5 分钟)...")
        import urllib.request
        import ssl
        ctx = ssl._create_unverified_context()
        poll_url = f"https://long.open.weixin.qq.com/connect/l/qrconnect?f=json&uuid={uuid_val}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }

        start = time.time()
        while time.time() - start < 300:
            try:
                req = urllib.request.Request(poll_url, headers=headers)
                with urllib.request.urlopen(req, context=ctx, timeout=28) as resp:
                    res = json.loads(resp.read().decode('utf-8'))
                    errcode = res.get('wx_errcode') if 'wx_errcode' in res else res.get('errcode')
                    if errcode == 405:
                        wx_code = res.get('wx_code')
                        print("✅ 用户已授权，正在换取微信读书长效 Token...")
                        info = exchange_weread_token(wx_code)
                        user = info.get("user", {})
                        acc = account_pool.add_or_update({
                            "vid": info.get("vid"),
                            "accessToken": info.get("accessToken"),
                            "refreshToken": info.get("refreshToken"),
                            "skey": info.get("skey"),
                            "deviceId": info.get("deviceId"),
                            "nickname": user.get("name", "微信读书账号"),
                            "avatar": user.get("avatar", ""),
                        })
                        print(f"🎉 登录成功！用户：{acc.get('nickname')} (VID: {acc.get('vid')})")
                        print("已保存至账号池。")
                        return True
                    elif errcode == 404:
                        print("📱 已扫码，请在手机上点击确认...")
                    elif errcode in (402, 403):
                        print("❌ 二维码已过期，请重新运行脚本。")
                        return False
            except Exception:
                time.sleep(1)

        print("❌ 扫码超时。")
        return False

    except Exception as e:
        print(f"❌ 登录异常: {e}")
        return False


if __name__ == "__main__":
    login()
