# -*- coding: utf-8 -*-
"""
WeChat Channels MITM Interception Proxy Server
Handles SSL interception, CA root trust, system proxy control, and custom script injection.
"""

import os
import re
import sys
import json
import time
import threading
import subprocess
from pathlib import Path
from backend.config import DATA_DIR

# Force unbuffered output for real-time background logging
import builtins
def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    builtins.print(*args, **kwargs)

CA_KEY_PATH = DATA_DIR / "ca.key"
CA_CERT_PATH = DATA_DIR / "ca.crt"
CERTS_DIR = DATA_DIR / "certs"
# mitmproxy 的配置目录:复用我们已生成并已被系统信任的 CA,
# 避免 mitmproxy 自己另生成一张未受信任的 CA(那会导致握手失败 → 页面疯狂重刷)。
MITM_CONFDIR = DATA_DIR / "mitm"

# 拦截目标主机(仅对这两个域名做 TLS 解密,其余全部透传)
TARGET_HOSTS = ("channels.weixin.qq.com", "mp.weixin.qq.com", "res.wx.qq.com")

# Create certs directory
CERTS_DIR.mkdir(parents=True, exist_ok=True)

PROXY_SESSION_ID = str(int(time.time()))

# ── 证书管理 (Certificate Management) ──────────────────────────

def ensure_ca_certificates():
    if CA_KEY_PATH.exists() and CA_CERT_PATH.exists():
        return CA_KEY_PATH, CA_CERT_PATH
        
    # Generate CA
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Guangdong"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Shenzhen"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WeChat Channels Downloader"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Channels Interceptor CA"),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    ).not_valid_after(
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
    ).add_extension(
        x509.BasicConstraints(ca=True, path_length=None), critical=True
    ).add_extension(
        x509.KeyUsage(
            digital_signature=True,
            content_commitment=False,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=True,
            crl_sign=True,
            encipher_only=False,
            decipher_only=False
        ), critical=True
    ).sign(private_key, hashes.SHA256())
    
    with open(CA_KEY_PATH, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    with open(CA_CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return CA_KEY_PATH, CA_CERT_PATH

def prepare_mitm_confdir():
    """把已生成、已受系统信任的 CA(ca.key + ca.crt)写入 mitmproxy 的
    配置目录,文件名为 mitmproxy-ca.pem(key 与 cert 拼接的 PEM)。
    这样 mitmproxy 用我们这张 CA 去签发各站点的叶子证书,沿用现有的
    安装/信任/卸载逻辑(CN=Channels Interceptor CA),不会另起一张新 CA。
    返回 confdir 路径。
    """
    ensure_ca_certificates()
    MITM_CONFDIR.mkdir(parents=True, exist_ok=True)
    ca_pem = MITM_CONFDIR / "mitmproxy-ca.pem"
    key_bytes = CA_KEY_PATH.read_bytes()
    cert_bytes = CA_CERT_PATH.read_bytes()
    combined = key_bytes
    if not combined.endswith(b"\n"):
        combined += b"\n"
    combined += cert_bytes
    # 仅在内容变化时重写,避免每次启动都触碰文件
    if not ca_pem.exists() or ca_pem.read_bytes() != combined:
        ca_pem.write_bytes(combined)
    # mitmproxy 还会用到 *-ca-cert.pem 等派生文件;删掉旧的让其按新 CA 重建
    for stale in ("mitmproxy-ca-cert.pem", "mitmproxy-ca-cert.cer",
                  "mitmproxy-ca-cert.p12", "mitmproxy-dhparam.pem"):
        p = MITM_CONFDIR / stale
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    return MITM_CONFDIR


def check_cert_trusted():
    if sys.platform == "darwin":
        try:
            def has_valid_trust_count(output_text):
                lines = output_text.splitlines()
                for i, line in enumerate(lines):
                    if "Channels Interceptor CA" in line:
                        for j in range(i + 1, min(i + 5, len(lines))):
                            if "Number of trust settings" in lines[j]:
                                match = re.search(r"Number of trust settings\s*:\s*(\d+)", lines[j])
                                if match and int(match.group(1)) > 0:
                                    return True
                return False

            # 1. Check System-wide domain trust settings
            out_d = subprocess.run(["security", "dump-trust-settings", "-d"], capture_output=True, text=True)
            if out_d.returncode == 0 and "Channels Interceptor CA" in out_d.stdout:
                if has_valid_trust_count(out_d.stdout):
                    return True
            
            # 2. Check User domain trust settings (installed via fallback to login keychain)
            out_s = subprocess.run(["security", "dump-trust-settings", "-s"], capture_output=True, text=True)
            if out_s.returncode == 0 and "Channels Interceptor CA" in out_s.stdout:
                if has_valid_trust_count(out_s.stdout):
                    return True
                    
            # 3. Check Default domain trust settings
            out = subprocess.run(["security", "dump-trust-settings"], capture_output=True, text=True)
            if out.returncode == 0 and "Channels Interceptor CA" in out.stdout:
                if has_valid_trust_count(out.stdout):
                    return True
                    
            return False
        except Exception:
            return False
    elif sys.platform == "win32":
        try:
            out = subprocess.run(["certutil", "-verifystore", "-user", "root", "Channels Interceptor CA"], capture_output=True)
            return out.returncode == 0
        except Exception:
            return False
    return False

def install_system_cert(ca_cert_path):
    if check_cert_trusted():
        return True
        
    if sys.platform == "darwin":
        # Try to delete from user keychains to prevent duplicates/conflicts
        try:
            subprocess.run(["security", "delete-certificate", "-c", "Channels Interceptor CA"], capture_output=True)
        except Exception:
            pass
            
        # 1. Attempt to install system-wide first (targets System.keychain)
        try:
            subprocess.run([
                "security", "add-trusted-cert",
                "-d",
                "-r", "trustRoot",
                "-k", "/Library/Keychains/System.keychain",
                str(ca_cert_path)
            ], check=True, capture_output=True)
            return True
        except Exception as system_err:
            print(f"System keychain installation failed: {system_err}. Falling back to Login keychain...")
            
            # 2. Fallback to user login keychain (no admin privileges / GUI prompt required)
            try:
                keychain_path = os.path.expanduser("~/Library/Keychains/login.keychain-db")
                subprocess.run([
                    "security", "add-trusted-cert",
                    "-r", "trustRoot",
                    "-p", "ssl",
                    "-k", keychain_path,
                    str(ca_cert_path)
                ], check=True)
                return True
            except Exception as e:
                print(f"Failed to install Mac cert to Login keychain: {e}")
                return False
    elif sys.platform == "win32":
        try:
            subprocess.run(["certutil", "-addstore", "-user", "root", str(ca_cert_path)], check=True)
            return True
        except Exception as e:
            print(f"Failed to install Win cert: {e}")
            return False
    return False

def uninstall_system_cert():
    if sys.platform == "darwin":
        cert_path = str(CA_CERT_PATH)
        # 1. 先删用户登录钥匙串里的(不需要管理员权限)
        try:
            subprocess.run(
                ["security", "delete-certificate", "-c", "Channels Interceptor CA"],
                capture_output=True
            )
        except Exception:
            pass

        # 2. System.keychain 的删除与去信任需要管理员权限。
        #    用 osascript 触发一次 GUI 授权(与安装时一致),
        #    在一个提权 shell 里同时去掉信任设置并删除证书。
        if CA_CERT_PATH.exists():
            inner = (
                f"/usr/bin/security remove-trusted-cert -d '{cert_path}'; "
                "/usr/bin/security delete-certificate -c 'Channels Interceptor CA' "
                "/Library/Keychains/System.keychain"
            )
            osa = (
                'do shell script "' + inner.replace('"', '\\"') + '" '
                'with administrator privileges'
            )
            try:
                subprocess.run(["osascript", "-e", osa], capture_output=True)
            except Exception as e:
                print(f"osascript uninstall failed: {e}")

        # 3. 以信任状态为准返回真实结果,不再无条件 True
        still_trusted = check_cert_trusted()
        if still_trusted:
            print("Cert still trusted after uninstall attempt")
        return not still_trusted
    elif sys.platform == "win32":
        try:
            subprocess.run(["certutil", "-delstore", "-user", "root", "Channels Interceptor CA"], check=True)
            return True
        except Exception as e:
            print(f"Failed to delete Win cert: {e}")
            return False
    return False


# ── 系统代理管理 (System Proxy Control) ─────────────────────────

def get_active_mac_service():
    try:
        nwi_out = subprocess.check_output(["scutil", "--nwi"], timeout=2).decode("utf-8")
        match = re.search(r"Network interfaces:\s*([^\s]+)", nwi_out)
        if match:
            iface = match.group(1)
            ports_out = subprocess.check_output(["networksetup", "-listallhardwareports"], timeout=2).decode("utf-8")
            blocks = ports_out.split("Hardware Port:")
            for block in blocks:
                block = block.strip()
                if not block:
                    continue
                lines = block.split("\n")
                service = lines[0].strip()
                if f"Device: {iface}" in block or any(f"Device: {iface}" in l for l in lines):
                    return service
    except Exception as e:
        print(f"Error getting active service: {e}")
    return "Wi-Fi"

def set_mac_proxy(enabled, host="127.0.0.1", port=5202):
    service = get_active_mac_service()
    try:
        if enabled:
            subprocess.run(["networksetup", "-setwebproxy", service, host, str(port)], check=True)
            subprocess.run(["networksetup", "-setsecurewebproxy", service, host, str(port)], check=True)
        else:
            subprocess.run(["networksetup", "-setwebproxystate", service, "off"], check=True)
            subprocess.run(["networksetup", "-setsecurewebproxystate", service, "off"], check=True)
    except Exception as e:
        print(f"Error setting Mac proxy state to {enabled}: {e}")

def set_windows_proxy(enabled, host="127.0.0.1", port=5202):
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_WRITE
        )
        if enabled:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"{host}:{port}")
        else:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        
        import ctypes
        internet_set_option = ctypes.windll.wininet.InternetSetOptionW
        internet_set_option(0, 39, 0, 0) # INTERNET_OPTION_SETTINGS_CHANGED
        internet_set_option(0, 37, 0, 0) # INTERNET_OPTION_REFRESH
    except Exception as e:
        print(f"Error setting Windows proxy state to {enabled}: {e}")

def set_system_proxy(enabled, port=5202):
    if sys.platform == "darwin":
        set_mac_proxy(enabled, port=port)
    elif sys.platform == "win32":
        set_windows_proxy(enabled, port=port)


# ── mitmproxy 拦截插件 (Interception Addon) ──────────────────────
# 用成熟的 mitmproxy 引擎替代手写 HTTP/1.1 解析器:
# HTTP/2、chunked、SSE/长轮询、WebSocket 全部由引擎正确处理,
# 不会再因解析错位或 read-until-EOF 阻塞导致视频号页面疯狂重刷。

class ChannelsAddon:
    """只对视频号 / 公众号两个域名做拦截与注入,其余流量透传。"""

    def _local_json(self, flow, status, payload_bytes):
        from mitmproxy import http
        flow.response = http.Response.make(
            status,
            payload_bytes,
            {"Content-Type": "application/json"},
        )

    def _forward_to_flask(self, flow, url):
        import urllib.request
        try:
            req = urllib.request.Request(
                url,
                data=flow.request.raw_content,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            self._local_json(flow, 200, body)
        except Exception as ex:
            print(f"Error forwarding to flask {url}: {ex}")
            self._local_json(
                flow, 500,
                json.dumps({"success": False, "error": str(ex)}).encode("utf-8"),
            )

    def http_connect(self, flow):
        # DIAG: 记录所有 CONNECT 的目标域名,用于发现 bundle 实际所在 CDN
        print(f"[DIAG CONNECT] {flow.request.host}:{flow.request.port}", flush=True)

    def request(self, flow):
        host = flow.request.pretty_host
        path = flow.request.path.split("?", 1)[0]

        if host == "channels.weixin.qq.com":
            if path == "/__wx_channels_api/sync-feed":
                try:
                    payload = json.loads(flow.request.get_text())
                    save_synced_feeds(payload.get("username"), payload.get("feeds", []))
                    self._local_json(
                        flow, 200,
                        b'{"code":0,"success":true,"message":"Feeds synced successfully"}',
                    )
                except Exception as ex:
                    print(f"Error handling sync-feed: {ex}")
                    self._local_json(
                        flow, 500,
                        json.dumps({"success": False, "error": str(ex)}).encode("utf-8"),
                    )
                return
            if path in ("/__wx_channels_api/error", "/__wx_channels_api/log-error"):
                try:
                    payload = json.loads(flow.request.get_text())
                    msg = payload.get('message') or payload.get('msg')
                    print(f"[FRONTEND ERROR] {msg}", flush=True)
                except Exception as ex:
                    print(f"[FRONTEND ERROR LOG FAILED] {ex}", flush=True)
                self._local_json(flow, 200, b'{"success":true}')
                return
            if path == "/__wx_channels_api/tip":
                try:
                    payload = json.loads(flow.request.get_text())
                    print(f"[FRONTEND TIP] {payload.get('msg')}", flush=True)
                except Exception:
                    pass
                self._local_json(flow, 200, b'{"success":true}')
                return
            if path == "/__wx_channels_api/profile":
                try:
                    payload = json.loads(flow.request.get_text())
                    print(f"[FRONTEND PROFILE] {payload.get('username')}", flush=True)
                except Exception:
                    pass
                self._local_json(flow, 200, b'{"success":true}')
                return
            if path == "/__wx_channels_api/synced-usernames":
                # 返回已同步过作品的作者 username 列表，供「一键采集全部关注」断点续采跳过。
                # 包成 {code:0,data:[...]} 以匹配前端 WXU.request 的约定。
                try:
                    from backend.config import load_json
                    from backend.channels import CHANNELS_FEEDS_FILE
                    feeds_db = load_json(CHANNELS_FEEDS_FILE, {})
                    body = json.dumps(
                        {"code": 0, "data": list(feeds_db.keys())}, ensure_ascii=False
                    ).encode("utf-8")
                    self._local_json(flow, 200, body)
                except Exception as ex:
                    print(f"Error handling synced-usernames: {ex}")
                    self._local_json(flow, 200, b'{"code":0,"data":[]}')
                return
            if path == "/__wx_channels_api/synced-feed-ids":
                # 返回 {username: [已同步的作品 id, ...]}，供「检查更新」客户端比对出新作品。
                # 包成 {code:0,data:{...}} 以匹配前端 WXU.request 的约定。
                try:
                    from backend.config import load_json
                    from backend.channels import CHANNELS_FEEDS_FILE
                    feeds_db = load_json(CHANNELS_FEEDS_FILE, {})
                    id_map = {
                        u: [it.get("id") for it in items if it.get("id")]
                        for u, items in feeds_db.items()
                    }
                    body = json.dumps(
                        {"code": 0, "data": id_map}, ensure_ascii=False
                    ).encode("utf-8")
                    self._local_json(flow, 200, body)
                except Exception as ex:
                    print(f"Error handling synced-feed-ids: {ex}")
                    self._local_json(flow, 200, b'{"code":0,"data":{}}')
                return
            if path == "/__wx_channels_api/call-log":
                # 采集脚本的调用埋点：每次 finder API 调用追加一行 jsonl，
                # 供「测风控概率」单变量扫描分析（errCode 非 0 占比、限流出现的累计请求数等）。
                try:
                    payload = json.loads(flow.request.get_text())
                    payload["ts"] = int(time.time() * 1000)
                    log_file = DATA_DIR / "channels_call_log.jsonl"
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                except Exception as ex:
                    print(f"Error handling call-log: {ex}")
                self._local_json(flow, 200, b'{"code":0,"data":true}')
                return
            if path == "/__wx_channels_api/download":
                self._forward_to_flask(
                    flow, "http://127.0.0.1:5200/api/channels/download"
                )
                return

        elif host == "mp.weixin.qq.com":
            if path == "/__wx_official_api/download":
                self._forward_to_flask(
                    flow, "http://127.0.0.1:5200/api/articles/download-url"
                )
                return

    def response(self, flow):
        host = flow.request.pretty_host
        if host not in TARGET_HOSTS:
            return
        content_type = flow.response.headers.get("content-type", "").lower()
        
        # 1. Check for res.wx.qq.com JS bundles
        if host == "res.wx.qq.com":
            pathname = flow.request.path.split("?", 1)[0]
            print(f"[DIAG res.wx] path={pathname} ct={content_type}", flush=True)
            if "javascript" not in content_type:
                return
            if "wasm_video_decode" in pathname:
                return
            try:
                js_content = flow.response.get_text(strict=False)
                if js_content:
                    modified = _hook_wx_bundle(pathname, js_content)
                    # DIAG: dump raw+hooked for virtual_svg bundle to locate syntax break
                    if "virtual_svg-icons-register.publish" in pathname:
                        try:
                            with open("/tmp/wx_bundle_raw.js", "w") as _f:
                                _f.write(js_content)
                            with open("/tmp/wx_bundle_hooked.js", "w") as _f:
                                _f.write(modified)
                            print("[DIAG] dumped virtual_svg bundle raw+hooked", flush=True)
                        except Exception as _e:
                            print(f"[DIAG] dump failed: {_e}", flush=True)
                    flow.response.text = modified
            except Exception as ex:
                print(f"[Proxy] Error hooking JS bundle: {ex}", flush=True)
            return

        # 2. Check for channels.weixin.qq.com / mp.weixin.qq.com HTML
        if "text/html" not in content_type:
            return

        try:
            html = flow.response.get_text(strict=False)
        except Exception:
            return
        if not html:
            return

        if host == "channels.weixin.qq.com":
            # Cache-busting for JS/CSS files loaded in the HTML
            html = re.sub(r'src="([^"]+)\.js"', r'src="\1.js?t=local"', html)
            html = re.sub(r'href="([^"]+)\.js"', r'href="\1.js?t=local"', html)
            
            try:
                libs = [
                    ("lib/mitt.umd.js", "script"),
                    ("lib/timeless.reactive.umd.min.js", "script"),
                    ("lib/timeless.utils.umd.min.js", "script"),
                    ("lib/timeless.ui.umd.min.js", "script"),
                    ("lib/timeless.kit.umd.min.js", "script"),
                    ("lib/timeless.headless.umd.min.js", "script"),
                    ("lib/timeless.icons.umd.min.js", "script"),
                    ("lib/timeless.web.umd.min.js", "script"),
                    ("lib/floating-ui.core.1.7.4.min.js", "script"),
                    ("lib/floating-ui.dom.1.7.4.min.js", "script"),
                    ("lib/weui.min.css", "style"),
                    ("lib/weui.min.js", "script"),
                    ("lib/wui.umd.js", "script"),
                ]
                
                parts = []
                for relpath, tag in libs:
                    content = _read_injection(relpath)
                    parts.append(f"<{tag}>{content}</{tag}>")
                
                # Inject inline config & variables
                inline_config = (
                    "var __wx_channels_config__ = { downloadInFrontend: false, downloadForceCheckAllFeeds: true, defaultHighest: true };\n"
                    "var __wx_channels_version__ = 'local';\n"
                    "window.addEventListener('error', (event) => {\n"
                    "    const errorMsg = `[JS Error] ${event.message}\\nFile: ${event.lineno ? (event.filename + ':' + event.lineno) : event.filename}\\nStack: ${event.error ? event.error.stack : ''}`;\n"
                    "    console.error(errorMsg);\n"
                    "    fetch('/__wx_channels_api/error', {\n"
                    "        method: 'POST',\n"
                    "        headers: { 'Content-Type': 'application/json' },\n"
                    "        body: JSON.stringify({ msg: errorMsg })\n"
                    "    }).catch(e => {});\n"
                    "});"
                )
                parts.append(f"<script>{inline_config}</script>")
                
                inline_variables = "var WXVariable = {};"
                parts.append(f"<script>{inline_variables}</script>")
                
                # Inject eventbus, utils, components
                src_files = ["src/eventbus.js", "src/utils.js", "src/components.js"]
                for relpath in src_files:
                    content = _read_injection(relpath)
                    parts.append(f"<script>{content}</script>")
                
                # Inject page specific script
                path_only = flow.request.path.split("?", 1)[0]
                if path_only == "/web/pages/home":
                    parts.append(f"<script>{_read_injection('src/home.js')}</script>")
                    parts.append(f"<script>{_read_injection('src/automation.js')}</script>")
                elif path_only == "/web/pages/feed":
                    parts.append(f"<script>{_read_injection('src/feed.js')}</script>")
                elif path_only == "/web/pages/profile":
                    parts.append(f"<script>{_read_injection('src/profile.js')}</script>")
                    parts.append(f"<script>{_read_injection('src/automation.js')}</script>")
                
                inject_script = "\n".join(parts)
            except Exception as ex:
                print(f"[Proxy] Error reading injection scripts: {ex}", flush=True)
                inject_script = ""
        else:
            inject_script = f"<script>{get_injected_official_js()}</script>"

        m = re.search(r"<head\b[^>]*>", html, flags=re.IGNORECASE)
        if m:
            pos = m.end()
            html = html[:pos] + "\n" + inject_script + html[pos:]
        else:
            html = inject_script + html
        # 设置 .text 会按 content-encoding/charset 重新编码并自动更新 Content-Length
        flow.response.text = html

        # 我们改过 HTML,关闭缓存,避免 webview 拿旧缓存导致注入丢失
        for h in ("etag", "last-modified", "expires"):
            if h in flow.response.headers:
                del flow.response.headers[h]
        flow.response.headers["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
        flow.response.headers["pragma"] = "no-cache"


# ── Synced Data Saving (同步数据持久化) ──────────────────────────

def save_synced_feeds(username, feeds):
    import urllib.parse
    from backend.config import load_json, save_json
    from backend.channels import CHANNELS_FEEDS_FILE, CHANNELS_FAVORITES_FILE
    
    if not username or not feeds:
        return
        
    username = urllib.parse.unquote(username)
        
    first_feed = feeds[0]
    contact = first_feed.get("contact", {})
    nickname = contact.get("nickname") or "已同步作者"
    head_img_url = contact.get("headUrl") or contact.get("avatar_url") or ""
    
    # Extract first video's decrypted CDN URL
    first_video_url = ""
    for feed in feeds:
        if feed.get("url"):
            first_video_url = feed.get("url")
            break
        media_list = feed.get("objectDesc", {}).get("media", [])
        if media_list and media_list[0].get("url"):
            media = media_list[0]
            first_video_url = media.get("url", "") + media.get("urlToken", "")
            break
    
    # 1. Update/Merge Favorites list
    favs = load_json(CHANNELS_FAVORITES_FILE, [])
    found_fav = False
    
    # Check if there is an existing favorite item with the real username (e.g. v2_xxx@finder)
    for fav in favs:
        if fav.get("username") == username:
            if head_img_url:
                fav["head_img_url"] = head_img_url
            if nickname and nickname != "已同步作者":
                fav["nickname"] = nickname
            if first_video_url:
                fav["video_url"] = first_video_url
            found_fav = True
            break
            
    # If not found by real username, check if there is a placeholder favorite with the nickname as username
    if not found_fav:
        for fav in favs:
            if fav.get("username") == nickname or fav.get("nickname") == nickname:
                # Upgrade this placeholder favorite to the real username!
                fav["username"] = username
                if head_img_url:
                    fav["head_img_url"] = head_img_url
                if first_video_url:
                    fav["video_url"] = first_video_url
                found_fav = True
                break
                
    # If still not found, append a new favorite
    if not found_fav:
        favs.append({
            "username": username,
            "nickname": nickname,
            "head_img_url": head_img_url,
            "video_url": first_video_url,
            "added_time": int(time.time())
        })
    save_json(CHANNELS_FAVORITES_FILE, favs)
    
    # 2. Update/Merge Feeds DB
    feeds_db = load_json(CHANNELS_FEEDS_FILE, {})
    
    # If there are old feeds saved under the nickname (placeholder), move/merge them
    old_feeds = []
    if nickname in feeds_db:
        old_feeds = feeds_db.pop(nickname) # Extract and delete old key
    if username in feeds_db and nickname != username:
        pass
        
    if username not in feeds_db:
        feeds_db[username] = []
        
    # Append old feeds if they are not already in the real list
    for of in old_feeds:
        of_id = of.get("id")
        if not of_id:
            continue
        if not any(item.get("id") == of_id for item in feeds_db[username]):
            feeds_db[username].append(of)
        
    for feed in feeds:
        is_media = False
        if feed.get("type") == "media":
            is_media = True
        elif feed.get("objectDesc", {}).get("mediaType") == 4:
            is_media = True
            
        if not is_media:
            continue # Sync videos only
            
        feed_id = feed.get("id")
        if not feed_id:
            continue
            
        # Try to parse as raw first, fallback to flat
        object_desc = feed.get("objectDesc", {})
        media_list = object_desc.get("media", [])
        
        description = object_desc.get("description") or feed.get("title") or feed.get("description") or ""
        createtime = str(feed.get("createtime", 0))
        
        if media_list:
            media = media_list[0]
            video_url = media.get("url", "") + media.get("urlToken", "")
            cover_url = media.get("coverUrl", "")
            decode_key = media.get("decodeKey") or feed.get("key") or ""
            spec_list = media.get("spec", [])
        else:
            # Flat structure fallback
            video_url = feed.get("url", "")
            cover_url = feed.get("cover_url", "")
            decode_key = feed.get("key") or ""
            spec_list = feed.get("spec", [])
            
        # Extract specs
        video_url_h264 = ""
        video_url_h265 = ""
        if spec_list:
            for s in spec_list:
                ff = s.get("fileFormat")
                if not ff:
                    continue
                url_with_flag = video_url + f"&X-snsvideoflag={ff}"
                coding = s.get("codingFormat", 0)
                if coding == 2:
                    video_url_h265 = url_with_flag
                elif coding == 1:
                    video_url_h264 = url_with_flag
                    
            if not video_url_h265 and spec_list:
                video_url_h265 = video_url + f"&X-snsvideoflag={spec_list[0].get('fileFormat')}"
            if not video_url_h264 and len(spec_list) > 1:
                video_url_h264 = video_url + f"&X-snsvideoflag={spec_list[1].get('fileFormat')}"
        else:
            video_url_h264 = video_url
            video_url_h265 = video_url
            
        item = {
            "id": feed_id,
            "description": description,
            "cover_url": cover_url,
            "video_url": video_url,
            "video_url_h264": video_url_h264,
            "video_url_h265": video_url_h265,
            "createtime": createtime,
            "decode_key": decode_key
        }
        
        found = False
        for ex_item in feeds_db[username]:
            if ex_item.get("id") == feed_id:
                ex_item.update(item)
                found = True
                break
        if not found:
            feeds_db[username].append(item)
            
    save_json(CHANNELS_FEEDS_FILE, feeds_db)


# ── Custom Injected Script Content (注入 JS 模板) ───────────────

def _injection_base():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "injection_scripts")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injection_scripts")

def _read_injection(relpath):
    with open(os.path.join(_injection_base(), relpath), "r", encoding="utf-8") as f:
        return f.read()

def _hook_wx_bundle(pathname, js_script):
    v = "?t=local"
    
    # Cache-busting imports in JS
    js_script = re.sub(r'from\s*"([^"]+)\.js"', r'from "\1.js' + v + '"', js_script)
    js_script = re.sub(r'"js/([^"]+)\.js"', r'"js/\1.js' + v + '"', js_script)
    js_script = re.sub(r'import\("([^"]+)\.js"\)', r'import("\1.js' + v + '")', js_script)
    js_script = re.sub(r'import\s*"([^"]+)\.js"', r'import "\1.js' + v + '"', js_script)

    if "virtual_svg-icons-register.publish" in pathname:
        print("[Bundle Hook] Hooking virtual_svg-icons-register.publish", flush=True)
        # 1. finderInit
        js_script = re.sub(
            r'async finderInit\(\)\{(.*?)\}async',
            r'async finderInit(){var result=await(async()=>{\1})();var data=result.data;WXU.emit(WXU.Events.Init,data);return result;}async',
            js_script
        )
        
        # 2. finderPcFlow
        js_script = re.sub(
            r'async finderPcFlow\((\w+)\)\{(.*?)\}async',
            r'async finderPcFlow(\1){var result=await(async()=>{\2})();var feeds=result.data.object;WXU.emit(WXU.Events.PCFlowLoaded,feeds);return result;}async',
            js_script
        )
        
        # 3. finderGetRecommend
        js_script = re.sub(
            r'async finderGetRecommend\((\w+)\)\{(.*?)\}async',
            r'async finderGetRecommend(\1){var result=await(async()=>{\2})();var feeds=result.data.object;WXU.emit(WXU.Events.RecommendFeedsLoaded,feeds);return result;}async',
            js_script
        )
        
        # 4. finderGetCommentDetail
        js_script = re.sub(
            r'async finderGetCommentDetail\((\w+)\)\{(.*?)\}async',
            r'async finderGetCommentDetail(\1){var result=await(async()=>{\2})();var feed=result.data.object;WXU.emit(WXU.Events.FeedProfileLoaded,feed);return result;}async',
            js_script
        )
        
        # 5. finderGetCommentList
        js_script = re.sub(
            r'async finderGetCommentList\((\w+)\)\{(.*?)\}async',
            r'async finderGetCommentList(\1){var result=await(async()=>{\2})();WXU.emit(WXU.Events.FeedCommentListLoaded,result.data);return result;}async',
            js_script
        )
        
        # 6. finderPCSearch
        js_script = re.sub(
            r'async finderPCSearch\((\w+)\)\{(.*?)\}async',
            r'async finderPCSearch(\1){var result=await(async()=>{\2})();return result;}async',
            js_script
        )
        
        # 7. finderSearch
        js_script = re.sub(
            r'async finderSearch\((\w+)\)\{(.*?)\}async',
            r'async finderSearch(\1){var result=await(async()=>{\2})();return result;}async',
            js_script
        )
        
        # 8. finderGetInteractionedFeedList
        js_script = re.sub(
            r'async finderGetInteractionedFeedList\((\w+)\)\{(.*?)\}\}const',
            r'async finderGetInteractionedFeedList(\1){var result=await(async()=>{\2})();var feeds=result.data.object;WXU.emit(WXU.Events.InteractionedFeedsLoaded,feeds);return result;}}const',
            js_script
        )
        
        # 9. finderUserPage
        js_script = re.sub(
            r'async finderUserPage\((\w+)\)\{(.*?)\}async',
            r'async finderUserPage(\1){var result=await(async()=>{\2})();var feeds=result.data.object;WXU.emit(WXU.Events.UserFeedsLoaded,{feeds:feeds,lastBuffer:result.data.lastBuffer,raw:result.data});return result;}async',
            js_script
        )
        
        # 10. finderLiveUserPage
        js_script = re.sub(
            r'async finderLiveUserPage\((\w+)\)\{(.*?)\}async',
            r'async finderLiveUserPage(\1){var result=await(async()=>{\2})();var feeds=result.data.object;WXU.emit(WXU.Events.LiveUserFeedsLoaded,feeds);return result;}async',
            js_script
        )
        
        # 11. finderGetLiveInfo
        js_script = re.sub(
            r'async finderGetLiveInfo\((\w+)\)\{(.*?)\}async',
            r'async finderGetLiveInfo(\1){var result=await(async()=>{\2})();var live=result.data;WXU.emit(WXU.Events.LiveProfileLoaded,live);return result;}async',
            js_script
        )
        
        # 12. joinLive
        js_script = re.sub(
            r'async joinLive\((\w+)\)\{(.*?)\}async',
            r'async joinLive(\1){var result=await(async()=>{\2})();var data=result.data;WXU.emit(WXU.Events.JoinLive,data);return result;}async',
            js_script
        )
        
        # 13. Export hooks (APILoaded)
        export_match = re.search(r'exports?\s*\{([^}]+)\}', js_script)
        api_methods = "{}"
        if export_match:
            items = export_match.group(1).split(",")
            locals_list = []
            for item in items:
                p = item.strip()
                if not p:
                    continue
                if " as " in p:
                    local = p.split(" as ")[0].strip()
                else:
                    local = p
                if local and not local.isspace():
                    locals_list.append(local)
            if locals_list:
                api_methods = "{" + ",".join(locals_list) + "}"
        
        api_methods_escaped = api_methods
        js_wxapi = f";WXU.emit(WXU.Events.APILoaded,{api_methods_escaped});export{{"
        js_script = re.sub(r'exports?\s*\{', js_wxapi, js_script, count=1)

    elif "connect.publish" in pathname or "applyMic.publish" in pathname:
        print(f"[Bundle Hook] Hooking {pathname}", flush=True)
        m_flow = re.search(r'flowTab:([a-zA-Z_$][\w$]*)', js_script)
        flow_var = m_flow.group(1) if m_flow else "yt"
        
        js_go_next = (
            f"goToNextFlowFeed:async function(v){{"
            f"await \\1(v);"
            f"if(!{flow_var}||!{flow_var}.value.feeds){{return;}}"
            f"var feed={flow_var}.value.feeds[{flow_var}.value.currentFeedIndex];"
            f"WXU.emit(WXU.Events.GotoNextFeed,feed);"
            f"}}"
        )
        js_script = re.sub(r'\bgoToNextFlowFeed:([a-zA-Z_$][\w$]*)', js_go_next, js_script)
        
        js_go_prev = (
            f"goToPrevFlowFeed:async function(v){{"
            f"await \\1(v);"
            f"if(!{flow_var}||!{flow_var}.value.feeds){{return;}}"
            f"var feed={flow_var}.value.feeds[{flow_var}.value.currentFeedIndex];"
            f"WXU.emit(WXU.Events.GotoPrevFeed,feed);"
            f"}}"
        )
        js_script = re.sub(r'\bgoToPrevFlowFeed:([a-zA-Z_$][\w$]*)', js_go_prev, js_script)
        
        js_wxutil = ";WXU.emit(WXU.Events.UtilsLoaded,{decodeBase64ToUint64String:decodeBase64ToUint64String,createAdapterFromGlobalMapper:createAdapterFromGlobalMapper,finderJoinLiveMapper:finderJoinLiveMapper});export{"
        js_script = re.sub(r'exports?\s*\{', js_wxutil, js_script, count=1)
        
        m_local = re.search(r'localFlowTab:([a-zA-Z_$][\w$]*)', js_script)
        local_var = m_local.group(1) if m_local else "vn"
        
        js_load_local = (
            f"loadLocalPlaylist:async function(...args){{"
            f"await \\1(...args);"
            f"if(!{local_var}||!{local_var}.value||!{local_var}.value.feeds){{return;}}"
            f"var feed={local_var}.value.feeds[{local_var}.value.currentFeedIndex];"
            f"WXU.emit(WXU.Events.HomeFeedChanged,feed);"
            f"}}"
        )
        js_script = re.sub(r'\bloadLocalPlaylist:([a-zA-Z_$][\w$]*)', js_load_local, js_script)
        
    return js_script


def get_injected_official_js():
    return r"""
    (() => {
        // Global Error Catcher to diagnose white-screens in WeChat client
        window.addEventListener('error', (event) => {
            const errorMsg = `[JS Error] ${event.message}\nFile: ${event.filename}:${event.lineno}\nStack: ${event.error ? event.error.stack : ''}`;
            console.error(errorMsg);
            fetch('/__wx_official_api/download', { // We can redirect to download or safe URL since mp has no error route, but posting to log-error is better:
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: errorMsg })
            }).catch(e => {});
        });

        // 1. Inject Inline Download Buttons in History List / Account profile pages
        function injectListDownloadButtons() {
            const selectors = [
                'a[href*="/s?__biz="]',
                'a[href*="/s/"]',
                '.weui_media_title',
                '.msg_title',
                '.appmsg_title',
                '.appmsg_title_link'
            ];
            
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    let url = '';
                    if (el.tagName === 'A') {
                        url = el.href;
                    } else {
                        const linkEl = el.querySelector('a');
                        if (linkEl) {
                            url = linkEl.href;
                        } else if (el.getAttribute('data-link')) {
                            url = el.getAttribute('data-link');
                        }
                    }
                    
                    if (!url || (!url.includes('/s?') && !url.includes('/s/'))) return;
                    
                    if (el.classList.contains('has-download-btn') || el.querySelector('.list-download-btn')) return;
                    el.classList.add('has-download-btn');
                    
                    const btn = document.createElement('span');
                    btn.className = 'list-download-btn';
                    btn.innerHTML = ' 📥下载';
                    btn.style.cssText = `
                        display: inline-block;
                        margin-left: 8px;
                        padding: 2px 6px;
                        background: #07c160;
                        color: white !important;
                        font-size: 11px;
                        border-radius: 4px;
                        cursor: pointer;
                        font-weight: bold;
                        text-decoration: none !important;
                        user-select: none;
                        vertical-align: middle;
                        transition: background 0.2s;
                    `;
                    
                    btn.onmouseover = () => { btn.style.background = '#06ad53'; };
                    btn.onmouseout = () => { btn.style.background = '#07c160'; };
                    
                    btn.onclick = async (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        
                        btn.style.background = '#888';
                        btn.innerHTML = ' ⏳提交中...';
                        
                        try {
                            const response = await fetch('/__wx_official_api/download', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ urls: [url] })
                            });
                            const data = await response.json();
                            if (response.ok) {
                                btn.innerHTML = ' ✅已提交';
                                btn.style.background = '#07c160';
                            } else {
                                alert('下载失败: ' + (data.error || '未知错误'));
                                btn.innerHTML = ' 📥下载';
                                btn.style.background = '#07c160';
                            }
                        } catch (err) {
                            alert('提交失败: ' + err.message);
                            btn.innerHTML = ' 📥下载';
                            btn.style.background = '#07c160';
                        }
                    };
                    
                    if (el.tagName === 'A') {
                        el.parentNode.insertBefore(btn, el.nextSibling);
                    } else {
                        el.appendChild(btn);
                    }
                });
            });
        }

        // 2. Inject Floating Button in Article Details Page
        function injectDetailFloatingButton() {
            if (document.getElementById('official-download-btn')) return;
            const style = document.createElement('style');
            style.innerHTML = `
                #official-download-btn {
                    position: fixed;
                    bottom: 30px;
                    right: 30px;
                    background: #07c160;
                    color: white;
                    padding: 12px 24px;
                    border-radius: 24px;
                    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
                    cursor: pointer;
                    z-index: 999999;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    font-size: 14px;
                    font-weight: bold;
                    transition: all 0.3s;
                    user-select: none;
                }
                #official-download-btn:hover {
                    transform: scale(1.05);
                    background: #06ad53;
                }
            `;
            document.head.appendChild(style);

            const btn = document.createElement('div');
            btn.id = 'official-download-btn';
            btn.innerHTML = '📥 下载当前公众号文章';
            btn.onclick = async () => {
                if (btn.classList.contains('downloading')) return;
                btn.classList.add('downloading');
                btn.style.background = '#888';
                btn.innerHTML = '⏳ 正在提交下载...';
                try {
                    const response = await fetch('/__wx_official_api/download', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ urls: [window.location.href] })
                    });
                    const data = await response.json();
                    if (response.ok) {
                        btn.innerHTML = '✅ 已提交下载任务';
                        btn.style.background = '#07c160';
                        setTimeout(() => {
                            btn.innerHTML = '📥 下载当前公众号文章';
                            btn.classList.remove('downloading');
                        }, 3000);
                    } else {
                        alert('下载失败: ' + (data.error || '未知错误'));
                        btn.innerHTML = '📥 下载当前公众号文章';
                        btn.style.background = '#07c160';
                        btn.classList.remove('downloading');
                    }
                } catch (e) {
                    alert('提交下载失败: ' + e.message);
                    btn.innerHTML = '📥 下载当前公众号文章';
                    btn.style.background = '#07c160';
                    btn.classList.remove('downloading');
                }
            };
            
            document.body.appendChild(btn);
        }
        
        const interval = setInterval(() => {
            if (document.body) {
                clearInterval(interval);
                // Only show detail floating button on article details pages
                if (window.location.pathname.includes('/s')) {
                    injectDetailFloatingButton();
                }
                // Always try to scan and inject buttons next to article links (e.g. list pages)
                setInterval(injectListDownloadButtons, 1000);
            }
        }, 100);
    })();
    """


def cleanup_mitmproxy_logging_handlers():
    import logging
    
    def is_mitm_handler(h):
        cls = h.__class__
        if "mitm" in getattr(cls, "__module__", "").lower():
            return True
        if "mitm" in cls.__name__.lower():
            return True
        for base in getattr(cls, "__mro__", []):
            if "mitm" in base.__name__.lower() or "mitm" in getattr(base, "__module__", "").lower():
                return True
        return False

    # 1. Clean root logger
    root = logging.getLogger()
    for h in list(root.handlers):
        if is_mitm_handler(h):
            try:
                root.removeHandler(h)
                h.close()
            except Exception:
                pass
                
    # 2. Clean all other loggers
    for name in list(logging.Logger.manager.loggerDict.keys()):
        logger = logging.getLogger(name)
        for h in list(logger.handlers):
            if is_mitm_handler(h):
                try:
                    logger.removeHandler(h)
                    h.close()
                except Exception:
                    pass


# ── Proxy Service Manager (代理服务单例管理器) ─────────────────────

class ProxyManager:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        self.running = False
        self.thread = None
        self.loop = None
        self.master = None
        self.port = 5202

    def start(self):
        if self.running:
            return True

        cleanup_mitmproxy_logging_handlers()
        try:
            from mitmproxy.tools.dump import DumpMaster
            from mitmproxy import options
        except ImportError as e:
            print(f"Error starting ProxyManager: mitmproxy is not installed in this Python environment. {e}")
            raise RuntimeError("未检测到 mitmproxy 依赖，请确保您是在虚拟环境 venv312 下运行项目（当前 Python 缺少 mitmproxy 库）。")

        ensure_ca_certificates()

        # 1. 安装并信任证书
        install_system_cert(CA_CERT_PATH)

        # 2. 把我们的 CA 喂给 mitmproxy
        confdir = prepare_mitm_confdir()

        # 3. 在后台线程里跑 mitmproxy 的 asyncio 事件循环
        self.running = True
        self.thread = threading.Thread(
            target=self._run_server, args=(str(confdir),), daemon=True
        )
        self.thread.start()

        # 4. 开启系统代理
        set_system_proxy(True, port=self.port)
        print(f"Channels MITM proxy started on 127.0.0.1:{self.port} and system proxy enabled.")
        return True

    def stop(self):
        if not self.running:
            return True

        self.running = False

        # 还原系统代理
        set_system_proxy(False, port=self.port)

        # 关闭 mitmproxy
        if self.master and self.loop:
            try:
                self.loop.call_soon_threadsafe(self.master.shutdown)
            except Exception as e:
                print(f"Error shutting down mitmproxy: {e}")

        # 等待后台线程完全退出以确保关闭完成
        if self.thread and self.thread.is_alive():
            try:
                self.thread.join(timeout=5)
            except Exception:
                pass

        self.master = None
        self.loop = None
        self.thread = None

        cleanup_mitmproxy_logging_handlers()

        print("Channels MITM proxy stopped and system proxy disabled.")
        return True

    def _run_server(self, confdir):
        import asyncio
        from mitmproxy.tools.dump import DumpMaster
        from mitmproxy import options

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop

        async def _serve():
            opts = options.Options(
                listen_host="127.0.0.1",
                listen_port=self.port,
                confdir=confdir,
                # 只解密这两个域名,其余 CONNECT 直接透传(不碰证书、不碰内容)
                allow_hosts=[
                    r"channels\.weixin\.qq\.com",
                    r"mp\.weixin\.qq\.com",
                    r"res\.wx\.qq\.com",
                ],
            )
            master = DumpMaster(opts, with_termlog=False, with_dumper=False)
            master.addons.add(ChannelsAddon())
            self.master = master
            try:
                await master.run()
            except Exception as e:
                print(f"[Proxy] mitmproxy run error: {e}")

        try:
            loop.run_until_complete(_serve())
        except Exception as e:
            print(f"[Proxy] event loop error: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass
