#!/usr/bin/env python3
"""
微信公众平台账号凭证配置工具
用于保存/更新 mp.weixin.qq.com 后台登录态 Cookie 与 Token
保存路径：脚本所在目录 / data / wechat_mp_config.json 及 data / account_pool.json
"""

import json
import time
from pathlib import Path

SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
CONFIG_FILE = DATA_DIR / "wechat_mp_config.json"
POOL_FILE   = DATA_DIR / "account_pool.json"


def login():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print(" 🚀 微信公众平台凭证配置 (mp.weixin.qq.com)")
    print("=" * 60)
    print("说明：本工具使用微信公众平台官方后台 API 检索文章列表。")
    print("请在浏览器登录 mp.weixin.qq.com，在地址栏复制 token，并在开发者工具中复制 Cookie 字符串。")
    print()

    token = input("1. 请输入公众平台 Token (如 123456789): ").strip()
    cookie_str = input("2. 请输入公众平台 Cookie 字符串: ").strip()

    if not token or not cookie_str:
        print("❌ Token 或 Cookie 不能为空！设置失败。")
        return False

    config = {
        "token": token,
        "cookie_str": cookie_str,
        "save_time": time.time(),
    }
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同步写入账号池
    accounts = []
    if POOL_FILE.exists():
        try:
            accounts = json.loads(POOL_FILE.read_text(encoding="utf-8"))
        except Exception:
            accounts = []

    acc_id = f"acc_{int(time.time())}"
    new_acc = {
        "id": acc_id,
        "token": token,
        "cookie_str": cookie_str,
        "nickname": "公众号官方账号",
        "save_time": time.time(),
        "status": "active",
        "failures": 0,
        "risk_hits": 0,
        "last_used": time.time(),
    }
    accounts.insert(0, new_acc)
    POOL_FILE.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("✅ 微信公众平台凭证已成功保存！")
    print(f"   - 配置文件: {CONFIG_FILE}")
    print(f"   - 账号池  : {POOL_FILE}")
    return True


if __name__ == "__main__":
    login()
