"""
代理管理模块
管理 HTTP/HTTPS/SOCKS5 代理配置和测试
"""

import requests
import time
from flask import Blueprint, jsonify, request

from backend.config import (
    get_proxy_config, save_proxy_config, get_proxy_url, get_proxies_dict
)

proxy_bp = Blueprint("proxy", __name__, url_prefix="/api/proxy")


@proxy_bp.route("/config", methods=["GET"])
def get_config():
    """获取代理配置"""
    config = get_proxy_config()
    # 隐藏密码
    safe_config = {**config}
    if safe_config.get("password"):
        safe_config["password"] = "***"
    for item in safe_config.get("proxy_list", []):
        if item.get("password"):
            item["password"] = "***"
    return jsonify(safe_config)


@proxy_bp.route("/config", methods=["POST"])
def update_config():
    """更新代理配置"""
    data = request.get_json() or {}

    current = get_proxy_config()

    # 更新字段
    for key in ["enabled", "type", "host", "port", "username", "rotation"]:
        if key in data:
            current[key] = data[key]

    # 密码特殊处理：如果传 "***" 则保持不变
    if "password" in data and data["password"] != "***":
        current["password"] = data["password"]

    # 代理池列表
    if "proxy_list" in data:
        current["proxy_list"] = data["proxy_list"]

    save_proxy_config(current)
    return jsonify({"message": "代理配置已保存"})


@proxy_bp.route("/test", methods=["POST"])
def test_proxy():
    """测试代理连接"""
    data = request.get_json() or {}

    # 构建临时代理配置进行测试
    test_config = {
        "enabled": True,
        "type": data.get("type", "http"),
        "host": data.get("host", ""),
        "port": data.get("port", ""),
        "username": data.get("username", ""),
        "password": data.get("password", ""),
    }

    proxy_url = get_proxy_url(test_config)
    if not proxy_url:
        return jsonify({"success": False, "message": "代理地址不完整", "latency": 0})

    proxies = {"http": proxy_url, "https": proxy_url}

    # 测试连接
    test_urls = [
        "https://httpbin.org/ip",
        "https://api.ipify.org?format=json",
    ]

    for test_url in test_urls:
        try:
            start = time.time()
            resp = requests.get(
                test_url,
                proxies=proxies,
                timeout=10,
            )
            latency = round((time.time() - start) * 1000)
            resp.raise_for_status()

            # 尝试获取代理 IP
            try:
                ip_info = resp.json()
                ip = ip_info.get("origin", ip_info.get("ip", "未知"))
            except Exception:
                ip = "已连接"

            return jsonify({
                "success": True,
                "message": f"代理连接成功，IP: {ip}",
                "latency": latency,
                "ip": ip,
            })

        except requests.exceptions.ProxyError as e:
            return jsonify({
                "success": False,
                "message": f"代理连接失败: {str(e)[:200]}",
                "latency": 0,
            })
        except requests.exceptions.Timeout:
            return jsonify({
                "success": False,
                "message": "代理连接超时（10秒）",
                "latency": 10000,
            })
        except Exception as e:
            continue

    return jsonify({
        "success": False,
        "message": "所有测试地址均无法连接",
        "latency": 0,
    })


@proxy_bp.route("/pool", methods=["GET"])
def get_pool():
    """获取代理池列表"""
    config = get_proxy_config()
    pool = config.get("proxy_list", [])
    return jsonify({"pool": pool, "total": len(pool)})


@proxy_bp.route("/pool", methods=["POST"])
def add_to_pool():
    """添加代理到代理池"""
    data = request.get_json() or {}
    proxy_item = {
        "type": data.get("type", "http"),
        "host": data.get("host", ""),
        "port": data.get("port", ""),
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "label": data.get("label", ""),
        "added_time": time.time(),
    }

    if not proxy_item["host"]:
        return jsonify({"error": "代理地址不能为空"}), 400

    config = get_proxy_config()
    if "proxy_list" not in config:
        config["proxy_list"] = []
    config["proxy_list"].append(proxy_item)
    save_proxy_config(config)

    return jsonify({"message": "已添加到代理池"})


@proxy_bp.route("/pool/<int:index>", methods=["DELETE"])
def remove_from_pool(index):
    """从代理池中移除代理"""
    config = get_proxy_config()
    pool = config.get("proxy_list", [])

    if 0 <= index < len(pool):
        removed = pool.pop(index)
        config["proxy_list"] = pool
        save_proxy_config(config)
        return jsonify({"message": f"已移除代理 {removed.get('host', '')}"})

    return jsonify({"error": "索引超出范围"}), 400
