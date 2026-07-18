import urllib.parse
import requests
import time
import threading
from flask import Blueprint, jsonify, request

from backend.config import get_settings, save_settings, get_proxies_dict

bilibili_login_bp = Blueprint("bilibili_login", __name__, url_prefix="/api/bilibili-auth")

# Cache login checks for 5 minutes
_bili_cache = {
    "cookie": "",
    "valid": False,
    "account_info": None,
    "last_check": 0.0
}
_bili_cache_lock = threading.Lock()

def validate_bilibili_cached(cookie_str: str) -> dict | None:
    """Validate Bilibili cookie with 5-minute memory cache"""
    global _bili_cache
    if not cookie_str or "SESSDATA" not in cookie_str:
        return None
        
    now = time.time()
    with _bili_cache_lock:
        if _bili_cache["cookie"] == cookie_str and (now - _bili_cache["last_check"]) < 300.0:
            return _bili_cache["account_info"] if _bili_cache["valid"] else None
            
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com"
        })
        proxies = get_proxies_dict()
        if proxies:
            session.proxies.update(proxies)
            
        resp = session.get("https://api.bilibili.com/x/web-interface/nav", headers={"Cookie": cookie_str}, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0 and data.get("data", {}).get("isLogin"):
                user_data = data["data"]
                account_info = {
                    "nickname": user_data.get("uname", "未知用户"),
                    "avatar": user_data.get("face", ""),
                    "vip_status": user_data.get("vipStatus") == 1,
                    "mid": str(user_data.get("mid", ""))
                }
                with _bili_cache_lock:
                    _bili_cache["cookie"] = cookie_str
                    _bili_cache["valid"] = True
                    _bili_cache["account_info"] = account_info
                    _bili_cache["last_check"] = now
                return account_info
                
        # If not successful or code is not 0
        raise ValueError("Invalid credentials or Bilibili server error")
        
    except Exception as e:
        print(f"Error validating Bilibili cookie: {e}")
        with _bili_cache_lock:
            _bili_cache["cookie"] = cookie_str
            _bili_cache["valid"] = False
            _bili_cache["account_info"] = None
            _bili_cache["last_check"] = now
        return None


@bilibili_login_bp.route("/status", methods=["GET"])
def get_status():
    """Get the current Bilibili login status"""
    settings = get_settings()
    cookie = settings.get("bilibili_cookie", "")
    
    if not cookie:
        return jsonify({
            "logged_in": False,
            "message": "未登录，请先登录B站"
        })
        
    account_info = validate_bilibili_cached(cookie)
    if account_info:
        return jsonify({
            "logged_in": True,
            "message": "登录有效",
            "account_info": account_info
        })
    else:
        # Clear setting if Cookie was invalid
        settings["bilibili_cookie"] = ""
        save_settings(settings)
        return jsonify({
            "logged_in": False,
            "message": "凭证已失效或过期，请重新登录"
        })


@bilibili_login_bp.route("/qrcode/generate", methods=["GET"])
def qrcode_generate():
    """Generate login QR Code parameters"""
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com"
        })
        proxies = get_proxies_dict()
        if proxies:
            session.proxies.update(proxies)
            
        resp = session.get(url, params={"source": "main-fe-header"}, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            qr_data = data.get("data", {})
            return jsonify({
                "url": qr_data.get("url"),
                "qrcode_key": qr_data.get("qrcode_key")
            })
        return jsonify({"error": "获取二维码失败"}), 500
    except Exception as e:
        return jsonify({"error": f"请求失败: {str(e)}"}), 500


@bilibili_login_bp.route("/qrcode/poll", methods=["POST"])
def qrcode_poll():
    """Poll the Bilibili passport login state"""
    req_data = request.get_json() or {}
    qrcode_key = req_data.get("qrcode_key")
    if not qrcode_key:
        return jsonify({"error": "qrcode_key 不能为空"}), 400
        
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com"
        })
        proxies = get_proxies_dict()
        if proxies:
            session.proxies.update(proxies)
            
        resp = session.get(url, params={"qrcode_key": qrcode_key, "source": "main-fe-header"}, timeout=10)
        res_json = resp.json()
        
        if res_json.get("code") == 0:
            poll_data = res_json.get("data", {})
            status_code = poll_data.get("code")
            
            # QR Code Scanned Confirmed
            if status_code == 0:
                redirect_url = poll_data.get("url")
                
                # Fetch cookies from redirect
                resp_redir = session.get(redirect_url, allow_redirects=False, timeout=10)
                cookie_dict = requests.utils.dict_from_cookiejar(session.cookies)
                
                # Extract SESSDATA and bili_jct
                sessdata = cookie_dict.get("SESSDATA")
                bili_jct = cookie_dict.get("bili_jct")
                
                # If they are not found in session jar, try query parameters
                if not sessdata or not bili_jct:
                    parsed_query = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
                    if "SESSDATA" in parsed_query:
                        sessdata = parsed_query["SESSDATA"][0]
                    if "bili_jct" in parsed_query:
                        bili_jct = parsed_query["bili_jct"][0]
                        
                if not sessdata:
                    return jsonify({"error": "登录成功但未提取到 SESSDATA"}), 500
                    
                # Format to a standard cookie string
                cookie_items = [f"{k}={v}" for k, v in cookie_dict.items()]
                if "SESSDATA" not in cookie_dict:
                    cookie_items.append(f"SESSDATA={sessdata}")
                if "bili_jct" not in cookie_dict and bili_jct:
                    cookie_items.append(f"bili_jct={bili_jct}")
                    
                cookie_str = "; ".join(cookie_items)
                
                # Save to settings
                settings = get_settings()
                settings["bilibili_cookie"] = cookie_str
                save_settings(settings)
                
                # Clear check cache to force reload
                global _bili_cache
                with _bili_cache_lock:
                    _bili_cache["cookie"] = ""
                    
                return jsonify({
                    "status": "success",
                    "cookie": cookie_str
                })
            elif status_code == 86101:
                return jsonify({"status": "not_scanned"})
            elif status_code == 86090:
                return jsonify({"status": "scanned"})
            elif status_code == 86038:
                return jsonify({"status": "expired"})
            else:
                return jsonify({"status": "failed", "message": poll_data.get("message")})
                
        return jsonify({"error": "查询登录状态失败"}), 500
    except Exception as e:
        return jsonify({"error": f"请求失败: {str(e)}"}), 500


@bilibili_login_bp.route("/save-cookie", methods=["POST"])
def save_cookie():
    """Manually input and save Cookie"""
    req_data = request.get_json() or {}
    cookie = req_data.get("cookie", "").strip()
    if not cookie:
        return jsonify({"error": "Cookie不能为空"}), 400
        
    # Validate the cookie
    account_info = validate_bilibili_cached(cookie)
    if account_info:
        settings = get_settings()
        settings["bilibili_cookie"] = cookie
        save_settings(settings)
        return jsonify({
            "message": "保存成功",
            "account_info": account_info
        })
    else:
        return jsonify({"error": "Cookie校验失败，请检查是否填写正确或已过期"}), 400


@bilibili_login_bp.route("/logout", methods=["POST"])
def logout():
    """Log out and remove Cookie"""
    settings = get_settings()
    settings["bilibili_cookie"] = ""
    save_settings(settings)
    
    # Reset validation cache
    global _bili_cache
    with _bili_cache_lock:
        _bili_cache["cookie"] = ""
        _bili_cache["valid"] = False
        _bili_cache["account_info"] = None
        _bili_cache["last_check"] = 0.0
        
    return jsonify({"message": "退出成功"})
