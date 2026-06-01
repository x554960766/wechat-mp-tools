#!/usr/bin/env python3
"""
测试抖音模块修复
验证登录、API 请求等功能
"""

import sys
import time
from backend.config import get_settings, save_settings
from backend.douyin import DouyinClient
from backend.douyin_sign import sign_detail

def test_sign():
    """测试签名算法"""
    print("=" * 60)
    print("测试 1: 签名算法")
    print("=" * 60)

    test_params = "device_platform=webapp&aid=6383&channel=channel_pc_web"
    test_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    try:
        signature = sign_detail(test_params, test_ua)
        print(f"✅ 签名生成成功")
        print(f"   参数: {test_params[:50]}...")
        print(f"   签名: {signature[:50]}...")
        print(f"   长度: {len(signature)}")
        return True
    except Exception as e:
        print(f"❌ 签名生成失败: {e}")
        return False


def test_client_init():
    """测试客户端初始化"""
    print("\n" + "=" * 60)
    print("测试 2: 客户端初始化")
    print("=" * 60)

    try:
        settings = get_settings()
        cookie = settings.get("douyin_cookie", "")

        if not cookie:
            print("⚠️  未找到 Cookie，将使用空 Cookie 初始化")
        else:
            print(f"✅ 找到 Cookie (长度: {len(cookie)})")

        client = DouyinClient()
        print("✅ 客户端初始化成功")

        # 测试通用参数
        params = client._get_common_params()
        print(f"✅ 通用参数生成成功 (共 {len(params)} 个参数)")

        # 检查关键参数
        required_params = ['device_platform', 'aid', 'msToken', 'verifyFp', 'pc_libra_divert']
        missing = [p for p in required_params if p not in params]

        if missing:
            print(f"⚠️  缺少参数: {', '.join(missing)}")
        else:
            print(f"✅ 所有关键参数都存在")

        return True
    except Exception as e:
        print(f"❌ 客户端初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_url_parsing():
    """测试 URL 解析"""
    print("\n" + "=" * 60)
    print("测试 3: URL 解析")
    print("=" * 60)

    test_cases = [
        ("7123456789012345678", "7123456789012345678"),
        ("https://www.douyin.com/video/7123456789012345678", "7123456789012345678"),
        ("https://www.douyin.com/note/7123456789012345678", "7123456789012345678"),
    ]

    all_passed = True
    for url, expected in test_cases:
        try:
            result = DouyinClient.extract_aweme_id(url)
            if result == expected:
                print(f"✅ {url[:50]}... → {result}")
            else:
                print(f"❌ {url[:50]}... → {result} (期望: {expected})")
                all_passed = False
        except Exception as e:
            print(f"❌ {url[:50]}... → 错误: {e}")
            all_passed = False

    return all_passed


def test_login_status():
    """测试登录状态检查"""
    print("\n" + "=" * 60)
    print("测试 4: 登录状态")
    print("=" * 60)

    try:
        settings = get_settings()
        cookie = settings.get("douyin_cookie", "")

        if not cookie:
            print("⚠️  未登录 - 请先使用扫码登录功能")
            print("   访问: http://localhost:5200 → 抖音扫码登录")
            return False

        # 检查 Cookie 中的关键字段
        cookie_fields = ['sessionid', 'sessionid_ss', 'sid_guard', 'uid_tt']
        found_fields = [f for f in cookie_fields if f in cookie]

        if found_fields:
            print(f"✅ Cookie 包含登录字段: {', '.join(found_fields)}")
            print(f"   Cookie 长度: {len(cookie)}")
            return True
        else:
            print(f"⚠️  Cookie 不包含登录字段，可能已失效")
            return False

    except Exception as e:
        print(f"❌ 检查登录状态失败: {e}")
        return False


def main():
    print("\n" + "🔧 抖音模块修复验证测试")
    print("=" * 60)

    results = []

    # 运行测试
    results.append(("签名算法", test_sign()))
    results.append(("客户端初始化", test_client_init()))
    results.append(("URL 解析", test_url_parsing()))
    results.append(("登录状态", test_login_status()))

    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name:20s} {status}")

    passed_count = sum(1 for _, p in results if p)
    total_count = len(results)

    print("\n" + "=" * 60)
    print(f"总计: {passed_count}/{total_count} 测试通过")
    print("=" * 60)

    if passed_count == total_count:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print(f"\n⚠️  {total_count - passed_count} 个测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
