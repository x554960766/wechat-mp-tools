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
import struct
from pathlib import Path
from flask import Blueprint, jsonify, request

from backend.config import DATA_DIR, OUTPUT_DIR, get_settings, load_json, save_json

channels_bp = Blueprint("channels", __name__, url_prefix="/api/channels")
CHANNELS_HISTORY_FILE = DATA_DIR / "channels_history.json"
CHANNELS_FAVORITES_FILE = DATA_DIR / "channels_favorites.json"
CHANNELS_FEEDS_FILE = DATA_DIR / "channels_parsed_feeds.json"


class ISAAC64:
    def __init__(self, key_uint64: int):
        self.rand_cnt = 255
        self.aa = 0
        self.bb = 0
        self.cc = 0
        self.seed = [0] * 256
        self.mm = [0] * 256
        self.seed[0] = key_uint64
        self.rand64_init()

    def mix(self, a, b, c, d, e, f, g, h):
        mask = 0xFFFFFFFFFFFFFFFF
        a = (a - e) & mask
        f = (f ^ (h >> 9)) & mask
        h = (h + a) & mask
        b = (b - f) & mask
        g = (g ^ ((a << 9) & mask)) & mask
        a = (a + b) & mask
        c = (c - g) & mask
        h = (h ^ (b >> 23)) & mask
        b = (b + c) & mask
        d = (d - h) & mask
        a = (a ^ ((c << 15) & mask)) & mask
        c = (c + d) & mask
        e = (e - a) & mask
        b = (b ^ (d >> 14)) & mask
        d = (d + e) & mask
        f = (f - b) & mask
        c = (c ^ ((e << 20) & mask)) & mask
        e = (e + f) & mask
        g = (g - c) & mask
        d = (d ^ (f >> 17)) & mask
        f = (f + g) & mask
        h = (h - d) & mask
        e = (e ^ ((g << 14) & mask)) & mask
        g = (g + h) & mask
        return a, b, c, d, e, f, g, h

    def rand64_init(self):
        golden = 0x9e3779b97f4a7c13
        a = b = c = d = e = f = g = h = golden
        for _ in range(4):
            a, b, c, d, e, f, g, h = self.mix(a, b, c, d, e, f, g, h)
        for i in range(0, 256, 8):
            a = (a + self.seed[i]) & 0xFFFFFFFFFFFFFFFF
            b = (b + self.seed[i+1]) & 0xFFFFFFFFFFFFFFFF
            c = (c + self.seed[i+2]) & 0xFFFFFFFFFFFFFFFF
            d = (d + self.seed[i+3]) & 0xFFFFFFFFFFFFFFFF
            e = (e + self.seed[i+4]) & 0xFFFFFFFFFFFFFFFF
            f = (f + self.seed[i+5]) & 0xFFFFFFFFFFFFFFFF
            g = (g + self.seed[i+6]) & 0xFFFFFFFFFFFFFFFF
            h = (h + self.seed[i+7]) & 0xFFFFFFFFFFFFFFFF
            a, b, c, d, e, f, g, h = self.mix(a, b, c, d, e, f, g, h)
            self.mm[i] = a; self.mm[i+1] = b; self.mm[i+2] = c; self.mm[i+3] = d
            self.mm[i+4] = e; self.mm[i+5] = f; self.mm[i+6] = g; self.mm[i+7] = h
        for i in range(0, 256, 8):
            a = (a + self.mm[i]) & 0xFFFFFFFFFFFFFFFF
            b = (b + self.mm[i+1]) & 0xFFFFFFFFFFFFFFFF
            c = (c + self.mm[i+2]) & 0xFFFFFFFFFFFFFFFF
            d = (d + self.mm[i+3]) & 0xFFFFFFFFFFFFFFFF
            e = (e + self.mm[i+4]) & 0xFFFFFFFFFFFFFFFF
            f = (f + self.mm[i+5]) & 0xFFFFFFFFFFFFFFFF
            g = (g + self.mm[i+6]) & 0xFFFFFFFFFFFFFFFF
            h = (h + self.mm[i+7]) & 0xFFFFFFFFFFFFFFFF
            a, b, c, d, e, f, g, h = self.mix(a, b, c, d, e, f, g, h)
            self.mm[i] = a; self.mm[i+1] = b; self.mm[i+2] = c; self.mm[i+3] = d
            self.mm[i+4] = e; self.mm[i+5] = f; self.mm[i+6] = g; self.mm[i+7] = h
        self.isaac64()

    def isaac64(self):
        self.cc = (self.cc + 1) & 0xFFFFFFFFFFFFFFFF
        self.bb = (self.bb + self.cc) & 0xFFFFFFFFFFFFFFFF
        for i in range(256):
            rem = i % 4
            if rem == 0:
                self.aa = (~(self.aa ^ (self.aa << 21))) & 0xFFFFFFFFFFFFFFFF
            elif rem == 1:
                self.aa = (self.aa ^ (self.aa >> 5)) & 0xFFFFFFFFFFFFFFFF
            elif rem == 2:
                self.aa = (self.aa ^ (self.aa << 12)) & 0xFFFFFFFFFFFFFFFF
            elif rem == 3:
                self.aa = (self.aa ^ (self.aa >> 33)) & 0xFFFFFFFFFFFFFFFF
            self.aa = (self.aa + self.mm[(i + 128) % 256]) & 0xFFFFFFFFFFFFFFFF
            x = self.mm[i]
            y = (self.mm[(x >> 3) % 256] + self.aa + self.bb) & 0xFFFFFFFFFFFFFFFF
            self.mm[i] = y
            self.bb = (self.mm[(y >> 11) % 256] + x) & 0xFFFFFFFFFFFFFFFF
            self.seed[i] = self.bb

    def isaac_random(self) -> int:
        result = self.seed[self.rand_cnt]
        if self.rand_cnt == 0:
            self.isaac64()
            self.rand_cnt = 255
        else:
            self.rand_cnt -= 1
        return result


def decrypt_channels_data(data: bytearray, key: int, enc_len: int = 131072):
    if len(data) == 0:
        return
    actual_enc_len = min(len(data), enc_len)
    aa_inst = ISAAC64(key)
    for i in range(0, actual_enc_len, 8):
        rand_num = aa_inst.isaac_random()
        temp_bytes = struct.pack(">Q", rand_num)
        for j in range(8):
            idx = i + j
            if idx >= actual_enc_len:
                return
            data[idx] ^= temp_bytes[j]



def add_channels_history_item(title: str, item_type: str, file_path: str, size_bytes: int):
    """保存下载记录到视频号历史记录文件"""
    from backend.config import load_json, save_json
    history = load_json(CHANNELS_HISTORY_FILE, [])
    history.insert(0, {
        "title": title,
        "type": item_type,
        "path": str(file_path),
        "size": f"{size_bytes / (1024 * 1024):.2f} MB" if size_bytes else "未知",
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    save_json(CHANNELS_HISTORY_FILE, history[:150])


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
    """在后端下载并自动解密的视频文件到本地下载目录，避免前端跨域(CORS)限制"""
    data = request.get_json() or {}
    video_url = data.get("url", "").strip()
    description = data.get("description", "").strip()
    createtime = data.get("createtime", "").strip()
    decrypt_key = data.get("decrypt_key") or data.get("key")

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

        # 如果有解密 Key 则对文件前 128KB 进行 ISAAC-64 解密
        if decrypt_key:
            try:
                key_val = int(decrypt_key)
                if key_val > 0:
                    with open(filepath, "r+b") as f:
                        file_data = bytearray(f.read(131072))
                        decrypt_channels_data(file_data, key_val)
                        f.seek(0)
                        f.write(file_data)
            except Exception as ed:
                print(f"解密视频文件失败: {ed}")
                    
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

        # 写入视频号专属下载历史
        try:
            size_bytes = filepath.stat().st_size
            add_channels_history_item(description or filename, "视频", filepath, size_bytes)
        except Exception as eh:
            print(f"写入视频号专属下载历史记录失败: {eh}")
                    
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


@channels_bp.route("/history", methods=["GET"])
def get_history():
    """获取视频号下载历史记录"""
    from backend.config import load_json
    history = load_json(CHANNELS_HISTORY_FILE, [])
    return jsonify(history)


@channels_bp.route("/history", methods=["DELETE"])
def clear_history():
    """清除视频号下载历史记录"""
    from backend.config import save_json
    save_json(CHANNELS_HISTORY_FILE, [])
    return jsonify({"message": "历史记录已清空"})


@channels_bp.route("/open-file", methods=["POST"])
def open_file():
    """在系统默认程序中打开特定的视频文件"""
    import subprocess
    import sys
    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400
    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件不存在"}), 404
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@channels_bp.route("/open-parent", methods=["POST"])
def open_parent():
    """打开文件所在的父目录"""
    import subprocess
    import sys
    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400
    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件或文件夹不存在"}), 404
        parent_path = path.parent if path.is_file() else path
        if sys.platform == "darwin":
            subprocess.run(["open", str(parent_path)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(parent_path)])
        else:
            subprocess.run(["xdg-open", str(parent_path)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@channels_bp.route("/favorites", methods=["GET"])
def get_favorites():
    """获取收藏的视频号作者列表"""
    favorites = load_json(CHANNELS_FAVORITES_FILE, [])
    return jsonify(favorites)


@channels_bp.route("/favorites", methods=["POST"])
def add_favorite():
    """添加视频号作者到收藏列表"""
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    nickname = data.get("nickname", "").strip()
    head_img_url = data.get("head_img_url", "").strip()

    if not username:
        return jsonify({"error": "作者 ID 不能为空"}), 400

    favorites = load_json(CHANNELS_FAVORITES_FILE, [])
    # 检查是否已存在
    for fav in favorites:
        if fav.get("username") == username:
            return jsonify({"message": "作者已在收藏列表中", "favorites": favorites})

    favorites.append({
        "username": username,
        "nickname": nickname or "未命名",
        "head_img_url": head_img_url,
        "added_time": int(time.time())
    })
    save_json(CHANNELS_FAVORITES_FILE, favorites)
    return jsonify({"message": "收藏成功", "favorites": favorites})


@channels_bp.route("/favorites/<username>", methods=["DELETE"])
def remove_favorite(username):
    """从收藏列表删除视频号作者"""
    if not username:
        return jsonify({"error": "作者 ID 不能为空"}), 400

    favorites = load_json(CHANNELS_FAVORITES_FILE, [])
    new_favorites = [fav for fav in favorites if fav.get("username") != username]
    save_json(CHANNELS_FAVORITES_FILE, new_favorites)
    return jsonify({"message": "已取消收藏", "favorites": new_favorites})


@channels_bp.route("/author-videos/<username>", methods=["GET"])
def get_author_videos(username):
    """获取指定作者已解析的视频列表"""
    if not username:
        return jsonify([])
    feeds_db = load_json(CHANNELS_FEEDS_FILE, {})
    author_videos = feeds_db.get(username, [])
    return jsonify(author_videos)


@channels_bp.route("/author-videos/<username>", methods=["POST"])
def add_author_video(username):
    """为指定作者添加/保存一条解析成功的视频信息"""
    if not username:
        return jsonify({"error": "作者 ID 不能为空"}), 400
    feed = request.get_json() or {}
    feed_id = feed.get("id")
    if not feed_id:
        return jsonify({"error": "视频 ID 不能为空"}), 400

    feeds_db = load_json(CHANNELS_FEEDS_FILE, {})
    if username not in feeds_db:
        feeds_db[username] = []

    # 检查是否已存在该视频
    exists = False
    for item in feeds_db[username]:
        if item.get("id") == feed_id:
            # 存在则更新数据（如更新可能失效 of URL）
            item.update(feed)
            exists = True
            break

    if not exists:
        feeds_db[username].append(feed)

    save_json(CHANNELS_FEEDS_FILE, feeds_db)
    return jsonify({"message": "视频已保存到作者作品列表", "videos": feeds_db[username]})


# ── Interceptor Proxy Admin Endpoints ──────────────────────────

@channels_bp.route("/proxy/status", methods=["GET"])
def get_proxy_status():
    """获取代理服务运行状态与证书信任状态"""
    from backend.mitm_proxy import ProxyManager, check_cert_trusted
    manager = ProxyManager.get_instance()
    return jsonify({
        "proxy_running": manager.running,
        "cert_installed": check_cert_trusted(),
        "proxy_port": manager.port
    })


@channels_bp.route("/proxy/start", methods=["POST"])
def start_proxy_server():
    """启动本地拦截代理服务"""
    from backend.mitm_proxy import ProxyManager
    manager = ProxyManager.get_instance()
    success = manager.start()
    if success:
        return jsonify({"message": "同步代理服务已成功启动，系统代理已设置", "running": True})
    else:
        return jsonify({"error": "启动同步代理服务失败，端口可能被占用"}), 500


@channels_bp.route("/proxy/stop", methods=["POST"])
def stop_proxy_server():
    """停止本地拦截代理服务"""
    from backend.mitm_proxy import ProxyManager
    manager = ProxyManager.get_instance()
    manager.stop()
    return jsonify({"message": "同步代理服务已关闭，系统代理已还原", "running": False})


@channels_bp.route("/proxy/install-cert", methods=["POST"])
def force_install_cert():
    """安装/信任 CA 证书"""
    from backend.mitm_proxy import install_system_cert, CA_CERT_PATH
    success = install_system_cert(CA_CERT_PATH)
    if success:
        return jsonify({"message": "证书安装成功"})
    else:
        return jsonify({"error": "证书安装失败，请手动下载证书进行安装"}), 500


@channels_bp.route("/proxy/download-cert", methods=["GET"])
def download_proxy_cert():
    """下载 CA 根证书文件以供手动安装"""
    from backend.mitm_proxy import CA_CERT_PATH, ensure_ca_certificates
    from flask import send_file
    ensure_ca_certificates()
    if not CA_CERT_PATH.exists():
        return jsonify({"error": "CA 证书生成失败"}), 500
    return send_file(
        str(CA_CERT_PATH),
        mimetype="application/x-x509-ca-cert",
        as_attachment=True,
        download_name="Channels_CA.crt"
    )


@channels_bp.route("/clear-cache", methods=["POST"])
def clear_wechat_cache():
    """清除微信视频号内置浏览器缓存"""
    import shutil
    import os
    
    paths_to_delete = [
        # macOS paths
        os.path.expanduser("~/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/radium/web/profiles"),
        os.path.expanduser("~/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/radium/web/profiles_to_delete"),
        os.path.expanduser("~/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/radium/cache"),
        os.path.expanduser("~/Library/Containers/com.tencent.xinWeChat/Data/Library/Caches/profiles"),
        # Windows paths (expanded)
        os.path.expandvars(r"%APPDATA%\Tencent\WeChat\radium\web\profiles")
    ]
    
    deleted_count = 0
    errors = []
    
    for path in paths_to_delete:
        if os.path.exists(path):
            try:
                # Try to remove tree
                shutil.rmtree(path, ignore_errors=True)
                # If still exists, try manual walking/removing
                if os.path.exists(path):
                    for root, dirs, files in os.walk(path, topdown=False):
                        for name in files:
                            try: os.remove(os.path.join(root, name))
                            except: pass
                        for name in dirs:
                            try: os.rmdir(os.path.join(root, name))
                            except: pass
                    try: os.rmdir(path)
                    except: pass
                
                if not os.path.exists(path):
                    deleted_count += 1
                else:
                    errors.append(f"部分文件可能被微信占用，无法完全删除: {path}")
            except Exception as e:
                errors.append(f"删除 {path} 失败: {str(e)}")
                
    if errors:
        return jsonify({
            "error": "，".join(errors) + "。请先在电脑上【彻底退出微信】，然后再试一次！"
        }), 400
        
    return jsonify({
        "success": True,
        "message": "已成功清除微信视频号浏览器缓存！请重启微信后再打开视频号页面。"
    })


