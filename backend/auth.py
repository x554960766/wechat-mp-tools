"""
登录认证模块
管理微信读书平台扫码登录、凭证验证和状态查询
"""

import json
import time
import threading
import requests as req
from pathlib import Path
from flask import Blueprint, jsonify, request

from backend.config import (
    CONFIG_FILE, DATA_DIR, load_json, save_json,
    get_settings, WEREAD_PLATFORM_URL, get_proxies_dict, report_proxy_status
)
from backend.account_pool import account_pool, LOGIN_VALID_SECONDS as POOL_LOGIN_VALID_SECONDS

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

LOGIN_VALID_SECONDS = 30 * 24 * 60 * 60  # 凭证延长保存

# 登录状态管理
_login_state = {
    "status": "idle",       # idle / scanning / success / failed
    "message": "",
    "progress": 0,
    "qrcode": "",           # scanUrl
    "uuid": "",
}
_login_lock = threading.Lock()


def _set_login_state(status: str, message: str = "", progress: int = 0, qrcode: str = "", uuid: str = ""):
    with _login_lock:
        _login_state["status"] = status
        _login_state["message"] = message
        _login_state["progress"] = progress
        if qrcode:
            _login_state["qrcode"] = qrcode
        if uuid:
            _login_state["uuid"] = uuid


@auth_bp.route("/status", methods=["GET"])
def get_status():
    """获取登录状态（聚合账号池中第一个 active 账号）"""
    accounts = account_pool.list_accounts()
    active_acc = None
    for acc in accounts:
        if acc["status"] == "active":
            active_acc = acc
            break

    if not active_acc:
        if accounts:
            last_acc = accounts[0]
            return jsonify({
                "logged_in": False,
                "login_state": _login_state,
                "token_preview": last_acc.get("token_preview", ""),
                "save_time": last_acc.get("save_time", 0),
                "message": "暂无可用微信读书账号，请重新登录",
                "account_info": {
                    "nickname": last_acc.get("nickname", ""),
                    "avatar": last_acc.get("avatar", ""),
                },
                "pool_summary": account_pool.get_summary(),
            })
        return jsonify({
            "logged_in": False,
            "login_state": _login_state,
            "message": "未登录，请先在账号池页面添加微信读书账号"
        })

    save_time = active_acc.get("save_time", 0)

    return jsonify({
        "logged_in": True,
        "login_state": _login_state,
        "token_preview": active_acc.get("token_preview", ""),
        "save_time": save_time,
        "message": "登录有效",
        "account_info": {
            "nickname": active_acc.get("nickname", ""),
            "avatar": active_acc.get("avatar", ""),
        },
        "pool_summary": account_pool.get_summary(),
    })


@auth_bp.route("/login", methods=["POST"])
def start_login():
    """启动微信读书扫码登录（无页面弹窗，仅生成二维码数据）"""
    with _login_lock:
        if _login_state["status"] == "scanning":
            return jsonify({"message": "正在登录中，请扫码", "login_state": _login_state})

    thread = threading.Thread(target=_do_login, daemon=True)
    thread.start()

    return jsonify({"message": "已请求微信读书扫码二维码"})


@auth_bp.route("/login-browser", methods=["POST"])
def start_browser_login():
    """兼容旧前端方法"""
    return start_login()


def _do_login():
    """执行微信读书扫码登录（后台线程轮询，不弹出浏览器窗口）"""
    platform_url = get_settings().get("weread_platform_url") or WEREAD_PLATFORM_URL

    _set_login_state("scanning", "正在从微信读书获取登录二维码...", 10)

    try:
        resp = req.get(f"{platform_url}/api/v2/login/platform", timeout=15)
        if resp.status_code != 200:
            _set_login_state("failed", f"获取二维码失败 (HTTP {resp.status_code})")
            return
        data = resp.json()
        uuid_str = data.get("uuid")
        scan_url = data.get("scanUrl")
        if not uuid_str or not scan_url:
            _set_login_state("failed", "未能获取到登录二维码数据")
            return

        _set_login_state("scanning", "请使用微信 App 扫描页面二维码登录", 30, qrcode=scan_url, uuid=uuid_str)

        start_time = time.time()
        while time.time() - start_time < 300:
            with _login_lock:
                if _login_state["status"] == "idle":
                    return

            time.sleep(3)
            try:
                r = req.get(f"{platform_url}/api/v2/login/platform/{uuid_str}", timeout=30)
                if r.status_code == 200:
                    r_data = r.json()
                    vid = r_data.get("vid")
                    token = r_data.get("token")
                    if vid and token:
                        username = r_data.get("username") or f"WeRead_{vid}"
                        
                        config = {
                            "vid": str(vid),
                            "token": token,
                            "nickname": username,
                            "save_time": time.time(),
                        }
                        DATA_DIR.mkdir(parents=True, exist_ok=True)
                        save_json(CONFIG_FILE, config)
                        
                        account_pool.add_or_update({
                            "token": token,
                            "vid": str(vid),
                            "nickname": username,
                            "save_time": time.time(),
                        })
                        
                        _set_login_state("success", f"登录成功！欢迎，{username}", 100)
                        return
                    
                    msg = r_data.get("message", "等待扫码登录...")
                    _set_login_state("scanning", msg, 50, qrcode=scan_url, uuid=uuid_str)
            except Exception as pe:
                print(f"Polling login exception: {pe}")

        _set_login_state("failed", "扫码登录超时（5分钟），请重新点击登录")

    except Exception as e:
        _set_login_state("failed", f"请求登录异常: {str(e)}")


@auth_bp.route("/cancel", methods=["POST"])
def cancel_login():
    """取消扫码登录"""
    _set_login_state("idle", "登录已取消")
    return jsonify({"message": "登录已取消"})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """清除登录凭据"""
    if CONFIG_FILE.exists():
        try:
            CONFIG_FILE.unlink()
        except Exception:
            pass
    _set_login_state("idle", "已退出登录")
    return jsonify({"message": "已退出登录"})


@auth_bp.route("/check-credentials", methods=["GET"])
def check_credentials():
    """验证账号池凭证有效性"""
    from backend.account_pool import borrow_session

    try:
        acc_id, token, cookie_str = borrow_session()
    except RuntimeError:
        return jsonify({"valid": False, "message": "账号池中无可用账号，请先添加微信读书账号"})

    return jsonify({"valid": True, "message": f"账号 (ID: {acc_id}) 凭证正常可用"})
