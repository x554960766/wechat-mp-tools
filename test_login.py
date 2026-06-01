#!/usr/bin/env python3
"""
测试抖音登录功能
"""
import sys
import time
from backend.douyin_auth import _do_login, _login_state

print("测试抖音登录功能...")
print("=" * 60)

# 重置状态
_login_state["status"] = "idle"

print("启动登录流程...")
try:
    _do_login()
    print(f"\n最终状态: {_login_state['status']}")
    print(f"消息: {_login_state['message']}")
except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
