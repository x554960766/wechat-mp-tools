# -*- coding: utf-8 -*-
"""
微信视频号视频在线解析与下载模块
"""

import re
import sys
import time
import random
import subprocess
import urllib.parse
import requests
from pathlib import Path
from flask import Blueprint, jsonify, request

from backend.config import DATA_DIR, OUTPUT_DIR, get_settings

channels_bp = Blueprint("channels", __name__, url_prefix="/api/channels")


def sanitize_filename(desc: str, createtime: str) -> str:
    """清理并生成合法的文件名"""
    if desc:
        # 去除 Windows/Mac 系统不支持的字符
        clean = re.sub(r'[\\/:*?"<>|\r\n]', "", desc).strip()
        if clean:
            # 截取前 120 个字符防止文件名过长
            return clean[:120] + ".mp4"
    if createtime:
        try:
            from datetime import datetime
            d = datetime.fromtimestamp(int(createtime))
            return f"video_{d.strftime('%Y%m%d_%H%M%S')}.mp4"
        except Exception:
            pass
    return "video.mp4"


def generate_rid() -> str:
    """生成微信视频号 API 所需的 _rid 参数"""
    timestamp_hex = hex(int(time.time()))[2:]
    random_hex = "".join(random.choices("0123456789abcdef", k=8))
    return f"{timestamp_hex}-{random_hex}"


def local_parse_with_yuanbao(share_url: str, cookie: str) -> dict:
    """100% 软件内部本地解析（调用腾讯元宝 + 微信视频号协议接口）"""
    # ---- 步骤 1: 调用腾讯元宝解析分享链接以获取 exportId 和 generalToken ----
    parse_url = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
    parse_headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "content-type": "application/json",
        "origin": "https://yuanbao.tencent.com",
        "referer": "https://yuanbao.tencent.com/chat",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "cookie": cookie,
        "x-source": "web"
    }
    payload1 = {
        "type": "video_channel_url",
        "url": share_url,
        "scene": 1
    }
    
    r1 = requests.post(parse_url, json=payload1, headers=parse_headers, timeout=15)
    if r1.status_code != 200:
        raise RuntimeError(f"腾讯元宝接口返回异常 (HTTP {r1.status_code})")
        
    res1 = r1.json()
    
    # ── 写入深度诊断日志 ──
    import json
    from backend.config import DATA_DIR
    log_file = DATA_DIR / "parse_debug.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f_log:
            f_log.write(f"\n=== [{time.strftime('%Y-%m-%d %H:%M:%S')}] 元宝 API 请求与响应 ===\n")
            f_log.write(f"请求 URL: {share_url}\n")
            f_log.write(f"元宝响应 JSON: {json.dumps(res1, ensure_ascii=False)}\n")
    except Exception:
        pass
        
    data = res1.get("data")
    if not data or not data.get("playable_url"):
        err_msg = res1.get("msg") or res1.get("error") or "未知错误，可能是您的元宝 Cookie 已失效，请在设置中更新"
        raise RuntimeError(f"腾讯元宝解析失败: {err_msg}")
        
    playable_url = data.get("playable_url")
    
    # 从元宝返回的直链参数中提取 eid 和 token
    parsed_query = urllib.parse.parse_qs(urllib.parse.urlparse(playable_url).query)
    general_token = parsed_query.get("token", [""])[0]
    export_id = parsed_query.get("eid", [""])[0]
    
    if not general_token or not export_id:
        raise RuntimeError("未能从元宝响应中获取有效的 Token 或 ExportId")

    # ---- 步骤 2: 使用提取出的凭证直接向微信视频号预览服务器获取视频详细数据 ----
    feed_info_url = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
    rid = generate_rid()
    api_url = f"{feed_info_url}?_rid={rid}&_pageUrl=https:%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed"
    
    referer = (
        f"https://channels.weixin.qq.com/finder-preview/pages/feed"
        f"?entry_card_type=48&comment_scene=39&appid=0"
        f"&token={urllib.parse.quote(general_token)}"
        f"&entry_scene=0&eid={urllib.parse.quote(export_id)}"
    )
    
    feed_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://channels.weixin.qq.com",
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    payload2 = {
        "baseReq": {"generalToken": general_token},
        "exportId": export_id
    }
    
    r2 = requests.post(api_url, json=payload2, headers=feed_headers, timeout=15)
    if r2.status_code not in (200, 201):
        raise RuntimeError(f"微信视频号接口请求异常 (HTTP {r2.status_code})")
        
    return r2.json()


# ── 自动获取元宝 Cookie (Playwright 联动) ─────────────────────────
import threading

_cookie_task = {
    "status": "idle",
    "cookie": None,
    "error": None
}
_cookie_lock = threading.Lock()


def _do_cookie_acquisition():
    global _cookie_task
    from playwright.sync_api import sync_playwright
    import time
    from backend.config import save_settings
    
    with _cookie_lock:
        _cookie_task = {
            "status": "running",
            "cookie": None,
            "error": None
        }
        
    try:
        with sync_playwright() as p:
            # 优先尝试启动本地 Chrome 浏览器以提供最佳免安装体验；若失败则退回 Playwright 默认 Chromium
            try:
                browser = p.chromium.launch(headless=False, channel="chrome")
            except Exception:
                try:
                    browser = p.chromium.launch(headless=False)
                except Exception as e:
                    raise RuntimeError("未检测到本地 Chrome 浏览器，请先安装 Chrome 浏览器，或在终端运行 'playwright install chromium' 补全驱动。")
            
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.goto("https://yuanbao.tencent.com/")
            
            cookie_captured = None
            start_time = time.time()
            
            # 最大等待 5 分钟 (300秒)
            while time.time() - start_time < 300:
                if page.is_closed():
                    break
                    
                cookies = context.cookies()
                # 检查元宝核心登录凭证 Cookie 'hy_token'
                has_token = any(c['name'] == 'hy_token' for c in cookies)
                
                if has_token:
                    cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                    cookie_captured = cookie_str
                    break
                    
                time.sleep(1.5)
                
            # 冗余读取：如果窗口被关掉，做最后一次检查
            if not cookie_captured:
                cookies = context.cookies()
                has_token = any(c['name'] == 'hy_token' for c in cookies)
                if has_token or len(cookies) > 5:
                    cookie_captured = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                    
            browser.close()
            
            if cookie_captured:
                # 抓取成功，直接保存到配置文件中
                settings = get_settings()
                settings["yuanbao_cookie"] = cookie_captured
                save_settings(settings)
                
                with _cookie_lock:
                    _cookie_task = {
                        "status": "success",
                        "cookie": cookie_captured,
                        "error": None
                    }
            else:
                with _cookie_lock:
                    _cookie_task = {
                        "status": "failed",
                        "cookie": None,
                        "error": "未检测到微信扫码登录状态，请确保成功登录并进入腾讯元宝对话页"
                    }
                    
    except Exception as e:
        err_str = str(e)
        if "closed" in err_str.lower() or "target page" in err_str.lower():
            err_str = "浏览器窗口已被关闭，自动获取已取消。"
        with _cookie_lock:
            _cookie_task = {
                "status": "failed",
                "cookie": None,
                "error": err_str
            }


@channels_bp.route("/start_cookie_acquisition", methods=["POST"])
def start_cookie_acquisition():
    """启动本地 Playwright 浏览器，引导用户登录腾讯元宝并自动截获 Cookie"""
    with _cookie_lock:
        if _cookie_task["status"] == "running":
            return jsonify({"message": "抓取任务已在运行中"}), 200
            
    thread = threading.Thread(target=_do_cookie_acquisition, daemon=True)
    thread.start()
    return jsonify({"message": "已成功唤起本地浏览器，请在弹出的窗口中完成微信扫码登录..."})


@channels_bp.route("/cookie_acquisition_status", methods=["GET"])
def cookie_acquisition_status():
    """查询本地 Cookie 抓取任务的状态"""
    with _cookie_lock:
        return jsonify(_cookie_task)


@channels_bp.route("/fetch_video_profile", methods=["POST"])
def fetch_video_profile():
    """解析视频号分享链接，提取视频 CDN 地址和元数据（支持本地解析、私有Worker以及免翻墙智能穿透）"""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "请输入有效的视频号链接"}), 400

    # 提取纯分享链接
    match = re.search(r'(https?://weixin\.qq\.com/sph/[a-zA-Z0-9]+)', url)
    if not match:
        match = re.search(r'(https?://channels\.weixin\.qq\.com/mobile/sf/[a-zA-Z0-9_]+)', url)
    share_url = match.group(1) if match else url

    # 获取系统配置，检测是否配置了本地解析凭证或私有 Worker
    settings = get_settings()
    yuanbao_cookie = settings.get("yuanbao_cookie", "").strip()
    custom_worker = settings.get("custom_channels_worker", "").strip()

    # ================= 模式 1: 100% 软件内部本地解析（如用户配置了元宝 Cookie） =================
    if yuanbao_cookie:
        try:
            result = local_parse_with_yuanbao(share_url, yuanbao_cookie)
            return jsonify(result)
        except Exception as e:
            # 本地解析失败时，可优雅回退到云端代理模式
            import traceback
            from backend.config import DATA_DIR
            log_file = DATA_DIR / "parse_debug.log"
            try:
                with open(log_file, "a", encoding="utf-8") as f_log:
                    f_log.write(f"\n--- [{time.strftime('%Y-%m-%d %H:%M:%S')}] 本地元宝解析发生错误 ---\n")
                    f_log.write(f"异常消息: {str(e)}\n")
                    f_log.write("详细调用栈:\n")
                    traceback.print_exc(file=f_log)
            except Exception:
                pass
            print(f"本地元宝解析失败，正在尝试云端方案... 错误: {str(e)}")

    # ================= 模式 2: 云端代理解析模式 =================
    if custom_worker:
        target_api = f"{custom_worker.rstrip('/')}/api/fetch_video_profile"
    else:
        target_api = "https://sph.litao.workers.dev/api/fetch_video_profile"

    # 尝试直接访问或使用用户配置代理
    from backend.config import get_proxies_dict, report_proxy_status
    proxies = get_proxies_dict()
    proxy_url = proxies.get("http") if proxies else None

    try:
        resp = requests.post(
            target_api,
            json={"url": share_url},
            headers={"Content-Type": "application/json"},
            proxies=proxies,
            timeout=8
        )
        if resp.status_code == 200:
            if proxy_url:
                report_proxy_status(proxy_url, success=True)
            return jsonify(resp.json())
    except Exception:
        if proxy_url:
            report_proxy_status(proxy_url, success=False)

    # 兜底：自动使用免翻墙 CDN 穿透通道
    if not custom_worker:
        import random
        from backend.config import get_random_subdomain
        
        backup_hosts = [
            "worker-proxy.asia",
            "net-proxy.asia",
            "1235566.space",
            "worker-proxy.shop",
            "worker-proxys.cyou",
            "worker-proxy.cyou"
        ]
        random.shuffle(backup_hosts)

        for host in backup_hosts:
            actual_host = f"node-{get_random_subdomain()}.{host}"
            tunnel_proxy_url = f"https://{actual_host}:443"
            tunnel_proxies = {"http": tunnel_proxy_url, "https": tunnel_proxy_url}
            
            try:
                resp = requests.post(
                    target_api,
                    json={"url": share_url},
                    headers={"Content-Type": "application/json"},
                    proxies=tunnel_proxies,
                    timeout=12
                )
                if resp.status_code == 200:
                    return jsonify(resp.json())
            except Exception:
                continue

    return jsonify({"error": "解析服务暂时不可用（本地解析鉴权失效且云端中继不可达），请检查配置或开启代理后重试"}), 502


@channels_bp.route("/download", methods=["POST"])
def download_video():
    """在后端下载未加密的视频文件到本地下载目录，避免前端跨域(CORS)限制"""
    data = request.get_json() or {}
    video_url = data.get("url", "").strip()
    description = data.get("description", "").strip()
    createtime = data.get("createtime", "").strip()

    if not video_url:
        return jsonify({"error": "下载链接不能为空"}), 400

    settings = get_settings()
    # 存放在配置的下载目录下的 channels 文件夹中
    base_download_dir = Path(settings.get("download_dir", str(OUTPUT_DIR)))
    channels_dir = base_download_dir / "channels"
    
    try:
        channels_dir.mkdir(parents=True, exist_ok=True)
        
        # 生成干净的文件名
        filename = sanitize_filename(description, createtime)
        filepath = channels_dir / filename
        
        # 如果存在重名，则追加序号
        if filepath.exists():
            base_name = filepath.stem
            ext = filepath.suffix
            counter = 1
            while filepath.exists():
                filepath = channels_dir / f"{base_name}_{counter}{ext}"
                counter += 1
            filename = filepath.name

        # 后端流式下载
        resp = requests.get(
            video_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            stream=True,
            timeout=60
        )
        resp.raise_for_status()
        
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    
        # 写入全局下载历史记录数据库，使视频号下载立即出现在“下载历史”中，并能够快速导入转码
        try:
            from backend.config import load_json, save_json, DOWNLOAD_HISTORY_FILE
            history = load_json(DOWNLOAD_HISTORY_FILE, [])
            history.append({
                "title": description or filename,
                "url": video_url,
                "account": "微信视频号",
                "time": int(time.time()),
                "success": True,
                "path": str(filepath.resolve())
            })
            save_json(DOWNLOAD_HISTORY_FILE, history)
        except Exception as eh:
            print(f"写入视频号下载历史记录失败: {eh}")
                    
        return jsonify({
            "success": True,
            "message": "视频下载成功",
            "path": str(filepath),
            "filename": filename,
            "directory": str(channels_dir)
        })
        
    except Exception as e:
        return jsonify({"error": f"下载视频失败: {str(e)}"}), 500


@channels_bp.route("/open-folder", methods=["POST"])
def open_folder():
    """在文件管理器中打开视频号下载文件夹"""
    settings = get_settings()
    base_download_dir = Path(settings.get("download_dir", str(OUTPUT_DIR)))
    channels_dir = base_download_dir / "channels"
    
    try:
        channels_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.run(["open", str(channels_dir)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(channels_dir)])
        else:
            subprocess.run(["xdg-open", str(channels_dir)])
        return jsonify({"message": "文件夹已打开"})
    except Exception as e:
        return jsonify({"error": f"打开文件夹失败: {str(e)}"}), 500
