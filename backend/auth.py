"""
登录认证模块
管理微信读书平台官方扫码登录、凭证验证和状态查询
完全基于微信读书 App 扫码协议 (wxab9b71ad2b90ff34) 与长效 Token 交换
"""

import os
import json
import time
import uuid
import random
import hashlib
import threading
import urllib.request
import urllib.parse
import ssl
from pathlib import Path
from flask import Blueprint, jsonify, request

from backend.config import (
    CONFIG_FILE, DATA_DIR, load_json, save_json,
    get_proxies_dict, get_settings
)
from backend.account_pool import (
    account_pool, borrow_weread_session, get_weread_session,
    generate_random_device_profile
)
from backend.weread_signer import gen_signature

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

APP_HEADERS = {
    'User-Agent': 'WeRead/7.5.0 (Android; 33; arm64-v8a)',
    'Content-Type': 'application/json; charset=UTF-8',
}

# 登录状态管理
_login_state = {
    "status": "idle",       # idle / scanning / scanned / success / failed / expired
    "message": "",
    "progress": 0,
    "qrcode": "",           # 二维码图片 URL
    "uuid": "",
}
_login_lock = threading.Lock()
_poll_stop_event = threading.Event()


def _set_login_state(status: str, message: str = "", progress: int = 0, qrcode: str = "", uuid_val: str = ""):
    with _login_lock:
        _login_state["status"] = status
        _login_state["message"] = message
        _login_state["progress"] = progress
        if qrcode:
            _login_state["qrcode"] = qrcode
        if uuid_val:
            _login_state["uuid"] = uuid_val


def get_wx_ticket() -> dict:
    """获取微信读书 wxticket"""
    url = 'https://i.weread.qq.com/wxticket?nonceStr=weread'
    s = get_weread_session()
    resp = s.get(url, headers=APP_HEADERS, timeout=10)
    return resp.json()


def get_qr_data() -> dict:
    """获取微信读书扫码登录二维码数据 (uuid)"""
    ticket = get_wx_ticket()
    appid = 'wxab9b71ad2b90ff34'
    scope = 'snsapi_userinfo,snsapi_timeline,snsapi_friend,snsapi_favorites'
    params = urllib.parse.urlencode({
        'appid': appid,
        'noncestr': 'weread',
        'timestamp': ticket['timeStamp'],
        'scope': scope,
        'signature': ticket['signature']
    })
    url = f'https://open.weixin.qq.com/connect/sdk/qrconnect?{params}'
    s = get_weread_session()
    resp = s.get(url, headers=APP_HEADERS, timeout=10)
    return resp.json()


def calculate_anti_replay_signature(device_id: str, timestamp: int, rand_val: int) -> str:
    raw_str = f"{timestamp}{device_id}{rand_val}"
    return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()


def exchange_weread_token(auth_code: str, device_profile: dict = None) -> dict:
    """使用扫码回调的 auth_code 换取微信读书长效 Token（绑定独立设备指纹）"""
    if not device_profile:
        device_profile = generate_random_device_profile()
    device_id = device_profile.get("deviceId") or hashlib.md5(uuid.uuid4().bytes).hexdigest()
    device_name = device_profile.get("deviceName", "Xiaomi 14")
    install_id = device_profile.get("installId", str(uuid.uuid4()))
    ua = device_profile.get("userAgent", APP_HEADERS['User-Agent'])

    ts = int(time.time() * 1000)
    rand_val = random.randint(0, 999)
    signature = calculate_anti_replay_signature(device_id, ts, rand_val)

    payload = {
        "code": auth_code,
        "deviceId": device_id,
        "trackId": "",
        "timestamp": ts,
        "random": rand_val,
        "signature": signature,
        "wxToken": 1,
        "appFirstInstall": 0,
        "virtualChannelId": "",
        "installId": install_id,
        "isFromQrcode": 1,
        "isAutoLogout": 0,
        "deviceName": "Android",
        "deviceType": 1
    }

    url = 'https://i.weread.qq.com/login'
    s = get_weread_session()
    headers = {**APP_HEADERS, "User-Agent": ua}
    resp = s.post(url, json=payload, headers=headers, timeout=12)
    res = resp.json()
    res['deviceId'] = device_id
    res['deviceName'] = device_name
    res['installId'] = install_id
    res['userAgent'] = ua
    return res


def _background_poll_login(uuid_val: str, device_profile: dict):
    """后台长轮询监听微信扫码授权"""
    _poll_stop_event.clear()
    poll_url = f'https://long.open.weixin.qq.com/connect/l/qrconnect?f=json&uuid={uuid_val}'
    poll_headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
    }

    s = get_weread_session()
    start_time = time.time()
    while time.time() - start_time < 300 and not _poll_stop_event.is_set():
        try:
            resp = s.get(poll_url, headers=poll_headers, timeout=28)
            res = resp.json()
            errcode = res.get('wx_errcode') if 'wx_errcode' in res else res.get('errcode')

            if errcode == 405:
                # 授权成功！
                wx_code = res.get('wx_code')
                _set_login_state("success", "授权成功，正在初始化微信读书会话...", 90)
                try:
                    login_info = exchange_weread_token(wx_code, device_profile=device_profile)
                    user = login_info.get("user", {})
                    vid = login_info.get("vid")
                    if not vid or not login_info.get("accessToken"):
                        raise RuntimeError(f"换取微信读书凭据失败: {login_info}")

                    # 写入账号池并持久化绑定独立设备指纹
                    saved_acc = account_pool.add_or_update({
                        "vid": str(vid),
                        "accessToken": login_info.get("accessToken"),
                        "refreshToken": login_info.get("refreshToken"),
                        "skey": login_info.get("skey"),
                        "deviceId": login_info.get("deviceId"),
                        "deviceName": login_info.get("deviceName"),
                        "installId": login_info.get("installId"),
                        "userAgent": login_info.get("userAgent"),
                        "nickname": user.get("name", f"微信读书用户_{vid}"),
                        "avatar": user.get("avatar", ""),
                    })

                    # 立即模拟真机在线同步会话 (shelf/sync 与 mp/list)，完成新设备在线态激活
                    try:
                        from backend.weread_signer import activate_terminal_session
                        auth_h = {
                            "accessToken": login_info.get("accessToken"),
                            "vid": str(vid),
                            "basekey": login_info.get("accessToken"),
                            "deviceId": login_info.get("deviceId"),
                            "User-Agent": login_info.get("userAgent", APP_HEADERS['User-Agent']),
                            "Cookie": f"wr_vid={vid}; wr_skey={login_info.get('skey')}"
                        }
                        activate_terminal_session(auth_h, session=s)
                    except Exception as ex:
                        logger.debug("登录后激活终端会话异常: %s", ex)

                    logger.info("微信扫码登录入库成功: %s (VID: %s, 绑定设备: %s)", saved_acc.get("nickname"), vid, saved_acc.get("deviceName"))
                    _set_login_state("success", f"登录成功！欢迎，{user.get('name', '微信读书用户')}（已绑定专属设备：{saved_acc.get('deviceName')}）", 100)
                    return
                except Exception as e:
                    logger.exception("扫码登录换票入库异常: %s", e)
                    _set_login_state("failed", f"登录入库失败: {e}", 0)
                    return

            elif errcode == 404:
                _set_login_state("scanned", "已扫码，请在手机微信上点击确认登录", 60)
            elif errcode == 408:
                # 继续等待
                pass
            elif errcode in (402, 403):
                _set_login_state("expired", "二维码已过期，请点击重新刷新二维码", 0)
                return
        except Exception as e:
            # 超时重试
            time.sleep(1)

    if not _poll_stop_event.is_set():
        _set_login_state("expired", "扫码超时，请重新刷新二维码", 0)



@auth_bp.route("/status", methods=["GET"])
def get_status():
    """获取登录状态（当前可用微信读书账号）"""
    accounts = account_pool.list_accounts()
    active_acc = next((a for a in accounts if a["status"] == "active"), None)

    if not active_acc:
        if accounts:
            last_acc = accounts[0]
            return jsonify({
                "logged_in": False,
                "login_state": _login_state,
                "token_preview": last_acc.get("token_preview", ""),
                "save_time": last_acc.get("save_time", 0),
                "message": "暂无可用微信读书账号，请扫码登录或点击刷新",
                "account_info": {
                    "nickname": last_acc.get("nickname", ""),
                    "avatar": last_acc.get("avatar", ""),
                    "vid": last_acc.get("vid", ""),
                },
                "pool_summary": account_pool.get_summary(),
            })
        return jsonify({
            "logged_in": False,
            "login_state": _login_state,
            "message": "未登录，请点击右上角扫码登录微信读书账号",
            "pool_summary": account_pool.get_summary(),
        })

    return jsonify({
        "logged_in": True,
        "login_state": _login_state,
        "token_preview": active_acc.get("token_preview", ""),
        "save_time": active_acc.get("save_time", 0),
        "message": "登录有效",
        "account_info": {
            "nickname": active_acc.get("nickname", ""),
            "avatar": active_acc.get("avatar", ""),
            "vid": active_acc.get("vid", ""),
        },
        "pool_summary": account_pool.get_summary(),
    })


@auth_bp.route("/qrcode", methods=["GET", "POST"])
def get_qrcode():
    """获取微信读书扫码登录二维码与 UUID"""
    _poll_stop_event.set()  # 停止之前的轮询
    try:
        qr_data = get_qr_data()
        uuid_val = qr_data.get('uuid', '')
        if not uuid_val:
            return jsonify({"error": "生成二维码失败，未获取到 uuid"}), 500

        b64 = qr_data.get('qrcode', {}).get('qrcodebase64', '')
        qr_img = f"data:image/jpeg;base64,{b64}" if b64 else f"https://open.weixin.qq.com/connect/qrcode/{uuid_val}"
        device_profile = generate_random_device_profile()

        _set_login_state("scanning", "请使用微信扫描二维码授权登录", 20, qrcode=qr_img, uuid_val=uuid_val)

        # 启动后台监听线程（携带该次登录专属分配的设备指纹）
        t = threading.Thread(target=_background_poll_login, args=(uuid_val, device_profile), daemon=True)
        t.start()

        return jsonify({
            "errcode": 0,
            "uuid": uuid_val,
            "qrcode": qr_data.get("qrcode"),
            "qrcode_url": qr_img,
            "login_state": _login_state
        })
    except Exception as e:
        _set_login_state("failed", f"获取二维码失败: {e}", 0)
        return jsonify({"error": str(e)}), 500


@auth_bp.route("/login", methods=["POST"])
def start_login():
    """触发微信读书扫码登录"""
    return get_qrcode()


@auth_bp.route("/login-browser", methods=["POST"])
def start_browser_login():
    """兼容旧前端调用"""
    return get_qrcode()


@auth_bp.route("/cancel", methods=["POST"])
def cancel_login():
    """取消当前扫码登录"""
    _poll_stop_event.set()
    _set_login_state("idle", "已取消登录")
    return jsonify({"message": "已取消扫码登录"})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """退出登录（清除当前账号）"""
    _poll_stop_event.set()
    _set_login_state("idle", "已退出登录")
    return jsonify({"message": "已退出登录"})


@auth_bp.route("/check-credentials", methods=["GET"])
def check_credentials():
    """验证账号池凭据有效性"""
    try:
        acc_id, vid, token, _ = borrow_weread_session()
        return jsonify({
            "valid": True,
            "message": f"微信读书账号 (VID: {vid}) 正常可用",
            "account_id": acc_id,
            "vid": vid
        })
    except RuntimeError as e:
        return jsonify({"valid": False, "message": str(e)})


@auth_bp.route("/manual", methods=["POST"])
def manual_login():
    """支持手动填入微信读书 VID 和 Token / RefreshToken 添加账号"""
    data = request.get_json() or {}
    vid = str(data.get("vid", "")).strip()
    token = str(data.get("accessToken", "") or data.get("token", "")).strip()
    refresh_token = str(data.get("refreshToken", "")).strip()
    nickname = str(data.get("nickname", "")).strip() or f"微信读书_{vid}"

    if not vid or (not token and not refresh_token):
        return jsonify({"error": "必须提供 VID 与 accessToken 或 refreshToken"}), 400

    acc = account_pool.add_or_update({
        "vid": vid,
        "accessToken": token,
        "token": token,
        "refreshToken": refresh_token,
        "nickname": nickname,
    })

    return jsonify({"message": "账号已成功录入账号池", "account": acc})
