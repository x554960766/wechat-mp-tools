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
import socket
import ssl
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

def get_host_certificate(host):
    ensure_ca_certificates()
    
    host_key_path = CERTS_DIR / f"{host}.key"
    host_cert_path = CERTS_DIR / f"{host}.crt"
    
    if host_key_path.exists() and host_cert_path.exists():
        return host_cert_path, host_key_path
        
    # Generate host cert signed by CA
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    with open(CA_KEY_PATH, "rb") as f:
        ca_key = serialization.load_pem_private_key(f.read(), password=None)
    with open(CA_CERT_PATH, "rb") as f:
        ca_cert = x509.load_pem_x509_certificate(f.read())
        
    host_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, host),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        ca_cert.subject
    ).public_key(
        host_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    ).not_valid_after(
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False
    ).add_extension(
        x509.BasicConstraints(ca=False, path_length=None), critical=True
    ).add_extension(
        x509.KeyUsage(
            digital_signature=True,
            content_commitment=False,
            key_encipherment=True,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False
        ), critical=True
    ).add_extension(
        x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
    ).sign(ca_key, hashes.SHA256())
    
    with open(host_key_path, "wb") as f:
        f.write(host_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    with open(host_cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        
    return host_cert_path, host_key_path

def check_cert_trusted():
    if sys.platform == "darwin":
        try:
            # Check if cert is present in keychain and trusted for SSL in any domain
            out = subprocess.run(["security", "dump-trust-settings"], capture_output=True, text=True)
            if out.returncode == 0 and "Channels Interceptor CA" in out.stdout:
                return True
            out_d = subprocess.run(["security", "dump-trust-settings", "-d"], capture_output=True, text=True)
            if out_d.returncode == 0 and "Channels Interceptor CA" in out_d.stdout:
                return True
            out_s = subprocess.run(["security", "dump-trust-settings", "-s"], capture_output=True, text=True)
            if out_s.returncode == 0 and "Channels Interceptor CA" in out_s.stdout:
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
        try:
            # Install to user login keychain and trust it for SSL without admin/sudo prompts
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
            print(f"Failed to install Mac cert: {e}")
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
        try:
            subprocess.run(["security", "delete-certificate", "-c", "Channels Interceptor CA"], check=True)
            return True
        except Exception as e:
            print(f"Failed to delete Mac cert: {e}")
            return False
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


# ── HTTP 解析与代理逻辑 (HTTP Parsing & Proxy Core) ───────────────

def read_http_request(r_file):
    req_line = r_file.readline()
    if not req_line:
        return None
    headers = {}
    while True:
        line = r_file.readline()
        if not line or line == b"\r\n" or line == b"\n":
            break
        parts = line.decode("utf-8", errors="ignore").split(":", 1)
        if len(parts) == 2:
            headers[parts[0].strip().lower()] = parts[1].strip()
            
    body = b""
    if "content-length" in headers:
        content_length = int(headers["content-length"])
        body = r_file.read(content_length)
        
    return {
        "line": req_line,
        "headers": headers,
        "body": body
    }

def read_http_response(r_file):
    resp_line = r_file.readline()
    if not resp_line:
        return None
    # Parse status code
    status_code = 200
    try:
        parts = resp_line.decode("utf-8", errors="ignore").split()
        if len(parts) >= 2:
            status_code = int(parts[1])
    except:
        pass

    headers = {}
    while True:
        line = r_file.readline()
        if not line or line == b"\r\n" or line == b"\n":
            break
        parts = line.decode("utf-8", errors="ignore").split(":", 1)
        if len(parts) == 2:
            headers[parts[0].strip().lower()] = parts[1].strip()
            
    body = b""
    if status_code in (204, 304) or (100 <= status_code < 200):
        pass
    elif "content-length" in headers:
        content_length = int(headers["content-length"])
        body = r_file.read(content_length)
    elif headers.get("transfer-encoding") == "chunked":
        while True:
            chunk_header = r_file.readline()
            if not chunk_header:
                break
            chunk_size_str = chunk_header.split(b";")[0].strip()
            if not chunk_size_str:
                break
            try:
                chunk_size = int(chunk_size_str, 16)
            except ValueError:
                break
            if chunk_size == 0:
                while True:
                    line = r_file.readline()
                    if not line or line == b"\r\n" or line == b"\n":
                        break
                break
            data = r_file.read(chunk_size)
            r_file.read(2) # CRLF
            body += data
        if "transfer-encoding" in headers:
            del headers["transfer-encoding"]
    else:
        # Read until EOF
        body = r_file.read()
            
    return {
        "line": resp_line,
        "status_code": status_code,
        "headers": headers,
        "body": body
    }

def tcp_tunnel(sock1, sock2):
    sock1.settimeout(15.0)
    sock2.settimeout(15.0)
    def forward(src, dst):
        try:
            while True:
                data = src.recv(16384)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try: src.close()
            except: pass
            try: dst.close()
            except: pass

    t = threading.Thread(target=forward, args=(sock1, sock2), daemon=True)
    t.start()
    forward(sock2, sock1)
    t.join()

def process_http_session(client_ssl, server_ssl, host):
    client_ssl.settimeout(30.0)
    server_ssl.settimeout(30.0)
    try:
        client_r = client_ssl.makefile("rb", buffering=4096)
        server_r = server_ssl.makefile("rb", buffering=4096)
        
        while True:
            req = read_http_request(client_r)
            if not req:
                break
                
            req_line_str = req["line"].decode("utf-8", errors="ignore")
            req_parts = req_line_str.split()
            if len(req_parts) < 2:
                break
            path = req_parts[1]
            print(f"[Proxy Request] {host} {req_parts[0]} {path}", flush=True)
            
            # Intercept fake sync feed path
            if host == "channels.weixin.qq.com" and path == "/__wx_channels_api/sync-feed":
                try:
                    payload = json.loads(req["body"].decode("utf-8"))
                    username = payload.get("username")
                    feeds = payload.get("feeds", [])
                    save_synced_feeds(username, feeds)
                    
                    resp_body = b'{"success":true,"message":"Feeds synced successfully"}'
                    resp = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + resp_body
                    )
                    client_ssl.sendall(resp)
                    continue
                except Exception as ex:
                    print(f"Error handling sync-feed in proxy: {ex}")
                    resp_body = f'{{"success":false,"error":"{str(ex)}"}}'.encode()
                    resp = (
                        b"HTTP/1.1 500 Internal Server Error\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + resp_body
                    )
                    client_ssl.sendall(resp)
                    continue

            # Intercept frontend error logging
            elif host == "channels.weixin.qq.com" and path == "/__wx_channels_api/log-error":
                try:
                    payload = json.loads(req["body"].decode("utf-8"))
                    print(f"[FRONTEND ERROR] {payload.get('message')}", flush=True)
                    resp_body = b'{"success":true}'
                    resp = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + resp_body
                    )
                    client_ssl.sendall(resp)
                    continue
                except Exception as ex:
                    print(f"Error handling log-error: {ex}")
                    resp_body = f'{{"success":false,"error":"{str(ex)}"}}'.encode()
                    resp = (
                        b"HTTP/1.1 500 Internal Server Error\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + resp_body
                    )
                    client_ssl.sendall(resp)
                    continue

            # Intercept video download requests to forward to Flask backend
            elif host == "channels.weixin.qq.com" and path == "/__wx_channels_api/download":
                try:
                    import urllib.request
                    req_data = req["body"]
                    headers = {"Content-Type": "application/json"}
                    flask_req = urllib.request.Request(
                        "http://127.0.0.1:5200/api/channels/download",
                        data=req_data,
                        headers=headers,
                        method="POST"
                    )
                    with urllib.request.urlopen(flask_req, timeout=60) as flask_resp:
                        resp_body = flask_resp.read()
                        
                    resp = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + resp_body
                    )
                    client_ssl.sendall(resp)
                    continue
                except Exception as ex:
                    print(f"Error handling video download in proxy: {ex}")
                    resp_body = f'{{"success":false,"error":"{str(ex)}"}}'.encode()
                    resp = (
                        b"HTTP/1.1 500 Internal Server Error\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + resp_body
                    )
                    client_ssl.sendall(resp)
                    continue

            # Intercept official account article download requests to forward to Flask backend
            elif host == "mp.weixin.qq.com" and path == "/__wx_official_api/download":
                try:
                    import urllib.request
                    req_data = req["body"]
                    headers = {"Content-Type": "application/json"}
                    flask_req = urllib.request.Request(
                        "http://127.0.0.1:5200/api/articles/download-url",
                        data=req_data,
                        headers=headers,
                        method="POST"
                    )
                    with urllib.request.urlopen(flask_req, timeout=60) as flask_resp:
                        resp_body = flask_resp.read()
                        
                    resp = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + resp_body
                    )
                    client_ssl.sendall(resp)
                    continue
                except Exception as ex:
                    print(f"Error handling official-download in proxy: {ex}")
                    resp_body = f'{{"success":false,"error":"{str(ex)}"}}'.encode()
                    resp = (
                        b"HTTP/1.1 500 Internal Server Error\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
                        b"Connection: keep-alive\r\n\r\n" + resp_body
                    )
                    client_ssl.sendall(resp)
                    continue
            
            # Only strip Accept-Encoding for the specific JS file we need to modify
            # (matching the third-party library's approach of minimal interference)
            is_target_js = host == "res.wx.qq.com" and (
                "virtual_svg-icons-register.publish" in path
                or "connect.publish" in path
                or "applyMic.publish" in path
            )
            is_target_html = host in ("channels.weixin.qq.com", "mp.weixin.qq.com")
            if is_target_js or is_target_html:
                req["headers"]["accept-encoding"] = "identity"
                if "if-none-match" in req["headers"]:
                    del req["headers"]["if-none-match"]
                if "if-modified-since" in req["headers"]:
                    del req["headers"]["if-modified-since"]
                
            # Forward request to real server
            req_data = req["line"]
            for k, v in req["headers"].items():
                req_data += f"{k}: {v}\r\n".encode("utf-8")
            req_data += b"\r\n" + req["body"]
            server_ssl.sendall(req_data)
            
            # Read response from real server
            resp = read_http_response(server_r)
            if not resp:
                break
                
            content_type = resp["headers"].get("content-type", "").lower()
            content_encoding = resp["headers"].get("content-encoding", "").lower()
            
            # Decompress response body if compressed (gzip, deflate, or brotli)
            body_bytes = resp["body"]
            decompressed = False
            
            if "gzip" in content_encoding:
                try:
                    import gzip
                    body_bytes = gzip.decompress(body_bytes)
                    decompressed = True
                except Exception as e:
                    print(f"Proxy failed to decompress gzip: {e}")
            elif "deflate" in content_encoding:
                try:
                    import zlib
                    body_bytes = zlib.decompress(body_bytes)
                    decompressed = True
                except Exception as e:
                    print(f"Proxy failed to decompress deflate: {e}")
            elif "br" in content_encoding:
                try:
                    import brotli
                    body_bytes = brotli.decompress(body_bytes)
                    decompressed = True
                except Exception as e:
                    print(f"Proxy failed to decompress brotli: {e}")
                    
            if decompressed:
                if "content-encoding" in resp["headers"]:
                    del resp["headers"]["content-encoding"]
            
            is_compressed = bool(resp["headers"].get("content-encoding"))
            
            # Modify only if NOT compressed (meaning it's plain text now)
            if not is_compressed:
                # HTML modification for channels script injection
                if host == "channels.weixin.qq.com" and "text/html" in content_type:
                    html = body_bytes.decode("utf-8", errors="ignore")
                    inject_script = f"<script>{get_injected_js_content()}</script>"
                    if "<head>" in html:
                        html = html.replace("<head>", f"<head>\n{inject_script}", 1)
                    else:
                        html = inject_script + html
                    # Cache-busting: append dynamic version parameter to scripts & stylesheets in HTML
                    html = re.sub(r'src="([^"]+?\.js)"', rf'src="\1?t={PROXY_SESSION_ID}"', html)
                    html = re.sub(r'href="([^"]+?\.js)"', rf'href="\1?t={PROXY_SESSION_ID}"', html)
                    body_bytes = html.encode("utf-8")
                    
                # HTML modification for official account script injection
                elif host == "mp.weixin.qq.com" and "text/html" in content_type:
                    html = body_bytes.decode("utf-8", errors="ignore")
                    inject_script = f"<script>{get_injected_official_js()}</script>"
                    if "<head>" in html:
                        html = html.replace("<head>", f"<head>\n{inject_script}", 1)
                    else:
                        html = inject_script + html
                    body_bytes = html.encode("utf-8")
                    
                # JS API hooking - ONLY modify virtual_svg-icons-register.publish
                elif host == "res.wx.qq.com" and "javascript" in content_type:
                    modified = False
                    js_content = body_bytes.decode("utf-8", errors="ignore")

                    # Cache-busting: rewrite ES6 dynamic imports inside res.wx.qq.com Javascript files
                    # e.g., from"chunk.js" -> from"chunk.js?t=PROXY_SESSION_ID"
                    original_js = js_content
                    js_content = re.sub(r'from\s*\"([^\"]+?)\.js\"', rf'from"\1.js?t={PROXY_SESSION_ID}"', js_content)
                    js_content = re.sub(r'\"js/([^\"]+?)\.js\"', rf'"js/\1.js?t={PROXY_SESSION_ID}"', js_content)
                    js_content = re.sub(r'import\(\"([^\"]+?)\.js\"\)', rf'import("\1.js?t={PROXY_SESSION_ID}")', js_content)
                    js_content = re.sub(r'import\s*\"([^\"]+?)\.js\"', rf'import"\1.js?t={PROXY_SESSION_ID}"', js_content)
                    if js_content != original_js:
                        modified = True

                    if "virtual_svg-icons-register.publish" in path:
                        # (A) 注入 feed 捕获:把 finderXxx 方法体包一层,emit 已解析的 feed(含 decodeKey)
                        js_content, feed_hooked = _inject_feed_capture_hooks(js_content)
                        if feed_hooked:
                            modified = True
                            print("[Proxy Hook] Injected feed-capture hooks (finderPcFlow/finderGetCommentDetail/...)", flush=True)
                        # (B) 注入 APILoaded(供"同步作者作品"调用 finderUserPage)
                        if "exports" in js_content or "export" in js_content:
                            match = re.search(r'export\s*\{([^}]*)\}', js_content)
                            if match:
                                export_content = match.group(1)
                                items = export_content.split(',')
                                locals_set = set()
                                JS_RESERVED_WORDS = {
                                    "default", "class", "const", "let", "var", "function", "import", "export",
                                    "extends", "super", "this", "arguments", "case", "catch", "continue",
                                    "debugger", "delete", "do", "else", "finally", "for", "if", "in",
                                    "instanceof", "new", "return", "switch", "throw", "try", "typeof", "void", "while", "with"
                                }
                                for item in items:
                                    p = item.strip()
                                    if not p:
                                        continue
                                    if " as " in p:
                                        parts = p.split(" as ")
                                        local = parts[0].strip()
                                    else:
                                        local = p
                                    if local and local not in JS_RESERVED_WORDS and re.match(r'^[a-zA-Z_$][a-zA-Z0-9_$]*$', local):
                                        locals_set.add(local)
                                if locals_set:
                                    api_methods = "{" + ",".join(sorted(list(locals_set))) + "}"
                                    js_wxapi = f";if(window.WXU&&window.WXU.emit){{window.WXU.emit('APILoaded',{api_methods})}};export{{"
                                    js_content = re.sub(r'export\s*\{', js_wxapi, js_content, count=1)
                                    print(f"[Proxy Hook] Successfully injected API hook into virtual_svg-icons-register.publish (methods: {len(locals_set)})", flush=True)
                                    modified = True
                                else:
                                    print("[Proxy Hook] No valid export methods found in export block!", flush=True)
                            else:
                                print("[Proxy Hook] export block match failed in virtual_svg-icons-register.publish!", flush=True)

                    if "connect.publish" in path or "applyMic.publish" in path:
                        js_content, flow_hooked = _inject_flow_scroll_hooks(js_content)
                        if flow_hooked:
                            modified = True
                            print("[Proxy Hook] Injected flow-scroll hooks (goToNextFlowFeed/goToPrevFlowFeed)", flush=True)

                    # 诊断:finder API 方法存在于某个 chunk 却没被 hook 到
                    # (modified 仍为 False)→ 说明要么正则没匹配上,要么微信把方法挪到了别的 chunk。
                    if not modified and ("finderUserPage" in js_content or "finderGetCommentDetail" in js_content):
                        print(f"[Proxy Hook][DIAG] finder API methods present but NOT hooked, chunk: {path}", flush=True)

                    if modified:
                        body_bytes = js_content.encode("utf-8")
            
            # Only disable cache headers for files we actually modified
            if is_target_js or is_target_html:
                for cache_hdr in ["etag", "last-modified", "expires"]:
                    if cache_hdr in resp["headers"]:
                        del resp["headers"][cache_hdr]
                resp["headers"]["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
                resp["headers"]["pragma"] = "no-cache"
            
            resp["body"] = body_bytes
            resp["headers"]["content-length"] = str(len(body_bytes))
            
            print(f"[Proxy Response] {host} {path} -> {resp['line'].decode('utf-8', errors='ignore').strip()} (len: {len(body_bytes)}, type: {content_type})", flush=True)
            
            # Send response back to client
            resp_data = resp["line"]
            for k, v in resp["headers"].items():
                resp_data += f"{k}: {v}\r\n".encode("utf-8")
            resp_data += b"\r\n" + resp["body"]
            client_ssl.sendall(resp_data)
            
            # Handle close connection headers
            connection_header = resp["headers"].get("connection", "").lower()
            if connection_header == "close" or req["headers"].get("connection", "").lower() == "close":
                break
            
    except Exception as e:
        import traceback
        print(f"[Proxy] Session error: {e}")
        traceback.print_exc()
    finally:
        try: client_ssl.close()
        except: pass
        try: server_ssl.close()
        except: pass

def handle_client_connection(client_sock):
    host = "unknown"
    try:
        client_r = client_sock.makefile("rb", buffering=4096)
        first_line_bytes = client_r.readline()
        if not first_line_bytes:
            client_sock.close()
            return
            
        first_line = first_line_bytes.decode("utf-8", errors="ignore")
        parts = first_line.split()
        if len(parts) < 3:
            client_sock.close()
            return
            
        # Support plain HTTP requests (method is GET, POST, etc. instead of CONNECT)
        if parts[0] != "CONNECT":
            url_str = parts[1]
            from urllib.parse import urlparse
            try:
                parsed_url = urlparse(url_str)
                host = parsed_url.hostname
                port = parsed_url.port or 80
                path = parsed_url.path or "/"
                if parsed_url.query:
                    path += "?" + parsed_url.query
            except Exception as e:
                host = None
                port = 80
                path = url_str

            # Read remaining headers
            headers = {}
            while True:
                line = client_r.readline()
                if not line or line == b"\r\n" or line == b"\n":
                    break
                h_parts = line.decode("utf-8", errors="ignore").split(":", 1)
                if len(h_parts) == 2:
                    headers[h_parts[0].strip().lower()] = h_parts[1].strip()

            if not host and "host" in headers:
                host_val = headers["host"]
                if ":" in host_val:
                    host, port_str = host_val.split(":", 1)
                    port = int(port_str)
                else:
                    host = host_val
                    port = 80

            if not host:
                client_sock.close()
                return

            # Read body if Content-Length is present
            body = b""
            if "content-length" in headers:
                content_length = int(headers["content-length"])
                body = client_r.read(content_length)

            # Establish connection to the plain HTTP server
            server_sock = socket.create_connection((host, port))
            # Reconstruct plain HTTP request line and headers, then forward
            new_first_line = f"{parts[0]} {path} {parts[2]}\r\n".encode("utf-8")
            server_sock.sendall(new_first_line)
            for k, v in headers.items():
                server_sock.sendall(f"{k}: {v}\r\n".encode("utf-8"))
            server_sock.sendall(b"\r\n" + body)

            # Forward response back to client
            tcp_tunnel(client_sock, server_sock)
            return

        # Handle CONNECT requests (HTTPS)
        target = parts[1]
        if ":" in target:
            host, port_str = target.split(":", 1)
            port = int(port_str)
        else:
            host = target
            port = 443
            
        while True:
            line = client_r.readline()
            if not line or line == b"\r\n" or line == b"\n":
                break
                
        client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        
        if host in ["channels.weixin.qq.com", "res.wx.qq.com", "mp.weixin.qq.com"]:
            cert_file, key_file = get_host_certificate(host)
            
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
            try:
                client_ssl = context.wrap_socket(client_sock, server_side=True)
            except Exception as e:
                print(f"[Proxy] SSL handshake with client failed for {host}: {e}", flush=True)
                return
            
            try:
                server_sock = socket.create_connection((host, port))
                server_context = ssl.create_default_context()
                server_ssl = server_context.wrap_socket(server_sock, server_hostname=host)
            except Exception as e:
                print(f"[Proxy] SSL connection to upstream {host}:{port} failed: {e}", flush=True)
                try: client_ssl.close()
                except: pass
                return
                
            process_http_session(client_ssl, server_ssl, host)
        else:
            server_sock = socket.create_connection((host, port))
            tcp_tunnel(client_sock, server_sock)
            
    except Exception as e:
        print(f"[Proxy] Connection handling error for host {host}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        try: client_sock.close()
        except: pass


# ── Synced Data Saving (同步数据持久化) ──────────────────────────

def save_synced_feeds(username, feeds):
    from backend.config import load_json, save_json
    from backend.channels import CHANNELS_FEEDS_FILE, CHANNELS_FAVORITES_FILE
    
    if not username or not feeds:
        return
        
    first_feed = feeds[0]
    contact = first_feed.get("contact", {})
    nickname = contact.get("nickname", "已同步作者")
    head_img_url = contact.get("headUrl", "")
    
    # Extract first video's decrypted CDN URL
    first_video_url = ""
    for feed in feeds:
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
        media_type = feed.get("objectDesc", {}).get("mediaType")
        if media_type != 4:
            continue # Sync videos only
            
        feed_id = feed.get("id")
        if not feed_id:
            continue
            
        media = feed.get("objectDesc", {}).get("media", [{}])[0]
        video_url = media.get("url", "") + media.get("urlToken", "")
        
        # Extract specs
        spec_list = media.get("spec", [])
        video_url_h264 = ""
        video_url_h265 = ""
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
            
        item = {
            "id": feed_id,
            "description": feed.get("objectDesc", {}).get("description", ""),
            "cover_url": media.get("coverUrl", ""),
            "video_url": video_url,
            "video_url_h264": video_url_h264,
            "video_url_h265": video_url_h265,
            "createtime": str(feed.get("createtime", 0)),
            "decode_key": media.get("decodeKey", "")
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

def _inject_feed_capture_hooks(js):
    """在 virtual_svg-icons-register.publish 里给 finderXxx 方法体包一层,
    把视频号 App 自己解析出来的 feed(含 objectDesc.media[0].decodeKey)通过 WXU.emit 抛给页面脚本。
    这样"抓取"不再依赖页面侧 APILoaded 的时序,也不依赖能否拦到明文网络 JSON。
    参考 wx_channels_download_temp/internal/interceptor/plugin.go 的服务端注入实现。
    """
    hooked = False
    
    # 1. Hook finderInit specifically (takes no arguments)
    init_pattern = r'async finderInit\(\)\{(.*?)\}async'
    match_init = re.search(init_pattern, js, flags=re.S)
    if match_init:
        body = match_init.group(1)
        start, end = match_init.span()
        repl = (
            f"async finderInit(){{var __wxr=await(async()=>{{{body}}})();"
            f"try{{if(window.WXU&&window.WXU.emit){{window.WXU.emit('Init',__wxr.data)}}}}catch(e){{}}"
            f"return __wxr}}async"
        )
        js = js[:start] + repl + js[end:]
        hooked = True
        print("[Proxy Hook] Injected finderInit hook specifically", flush=True)

    # 2. Hook other finder APIs (take one argument)
    specs = [
        ("finderPcFlow", "PCFlowLoaded"),
        ("finderGetRecommend", "RecommendFeedsLoaded"),
        ("finderUserPage", "UserFeedsLoaded"),
        ("finderGetCommentDetail", "FeedProfileLoaded"),
    ]
    for name, event in specs:
        pattern = r'async ' + name + r'\((\w+)\)\{(.*?)\}async'
        match = re.search(pattern, js, flags=re.S)
        if match:
            param = match.group(1)
            body = match.group(2)
            start, end = match.span()
            repl = (
                f"async {name}({param}){{var __wxr=await(async()=>{{{body}}})();"
                f"try{{if(window.WXU&&window.WXU.emit){{window.WXU.emit('{event}',__wxr.data.object)}}}}catch(e){{}}"
                f"return __wxr}}async"
            )
            js = js[:start] + repl + js[end:]
            hooked = True
    return js, hooked


def _inject_flow_scroll_hooks(js):
    """在 connect.publish / applyMic.publish 里给上下滑动切换 feed 的方法包一层,
    每次切到新视频就 emit 当前 feed(读 App 内存里的 flowTab.value.feeds[currentFeedIndex])。
    用于"推荐"等滚动流页面的当前视频捕获。"""
    hooked = False
    m = re.search(r'flowTab:([a-zA-Z_$][\w$]*)', js)
    var = m.group(1) if m else "yt"
    for fn, event in [("goToNextFlowFeed", "GotoNextFeed"), ("goToPrevFlowFeed", "GotoPrevFeed")]:
        pattern = fn + r':([a-zA-Z_$][\w$]*)'
        repl = (
            fn + r':async function(__v){await \1(__v);'
            'try{if(' + var + '&&' + var + '.value&&' + var + '.value.feeds){'
            'var __f=' + var + '.value.feeds[' + var + '.value.currentFeedIndex];'
            'if(window.WXU&&window.WXU.emit){window.WXU.emit("' + event + '",__f)}}}catch(e){}}'
        )
        js, n = re.subn(pattern, repl, js, count=1)
        if n:
            hooked = True
    return js, hooked


def get_injected_js_content():
    return r"""
    (() => {
        // Global Error Catcher to diagnose white-screens in WeChat client
        window.addEventListener('error', (event) => {
            const errorMsg = `[JS Error] ${event.message}\nFile: ${event.filename}:${event.lineno}\nStack: ${event.error ? event.error.stack : ''}`;
            console.error(errorMsg);
            fetch('/__wx_channels_api/log-error', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: errorMsg })
            }).catch(e => {});
        });
        window.addEventListener('unhandledrejection', (event) => {
            const errorMsg = `[Unhandled Promise Rejection] ${event.reason}\nStack: ${event.reason ? event.reason.stack : ''}`;
            console.error(errorMsg);
            fetch('/__wx_channels_api/log-error', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: errorMsg })
            }).catch(e => {});
        });

        // Debug log to trace injection success and client URL
        fetch('/__wx_channels_api/log-error', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: `[ChannelsSync] Injected script active on: ` + window.location.href })
        }).catch(e => {});


        const WXE = {
            _events: {},
            on(event, cb) {
                (this._events[event] = this._events[event] || []).push(cb);
            },
            emit(event, data) {
                (this._events[event] || []).forEach(cb => cb(data));
            },
            onAPILoaded(cb) { this.on('APILoaded', cb); },
            onUtilsLoaded(cb) { this.on('UtilsLoaded', cb); }
        };
        window.WXU = window.WXU || WXE;
        window.WXU.Events = {
            APILoaded: 'APILoaded',
            UtilsLoaded: 'UtilsLoaded',
            UserFeedsLoaded: 'UserFeedsLoaded'
        };

        var API_OBJECTS = [];
        var WXAPI = new Proxy({}, {
            get(target, prop) {
                for (const obj of API_OBJECTS) {
                    if (obj && typeof obj[prop] === 'function') {
                        return obj[prop].bind(obj);
                    }
                    if (obj && obj[prop] !== undefined) {
                        return obj[prop];
                    }
                }
                return undefined;
            }
        });
        window.WXU.API = WXAPI;

        window.capturedFeeds = window.capturedFeeds || {};
        
        function captureFeeds(feeds) {
            if (!Array.isArray(feeds)) return;
            feeds.forEach(feed => {
                if (feed && feed.id) {
                    window.capturedFeeds[feed.id] = feed;
                    try {
                        const m = feed.objectDesc && feed.objectDesc.media && feed.objectDesc.media[0];
                        const mediaType = feed.objectDesc && feed.objectDesc.mediaType;
                        if (m && m.url && mediaType === 4) {
                            window.__wx_last_video_feed = feed;
                        }
                    } catch (e) {}
                }
            });
        }

        // 订阅"代理服务端注入"抛出的 feed 事件(finderPcFlow/finderGetCommentDetail/滚动切换等)。
        // 这些事件携带 App 已解析的 feed(含 decodeKey),是最可靠的捕获来源,
        // 不依赖 APILoaded 时序,也不依赖能否拦到明文网络 JSON。
         (function subscribeFeedEvents() {
            const FEED_EVENTS = [
                'PCFlowLoaded', 'RecommendFeedsLoaded', 'UserFeedsLoaded',
                'FeedProfileLoaded', 'GotoNextFeed', 'GotoPrevFeed', 'Init'
            ];
            FEED_EVENTS.forEach(ev => {
                window.WXU.on(ev, payload => {
                    try {
                        if (ev === 'Init') {
                            if (payload && payload.mainFinderUsername) {
                                localStorage.setItem('__wx_main_finder_username', payload.mainFinderUsername);
                                console.log("[ChannelsSync] Captured mainFinderUsername: " + payload.mainFinderUsername);
                            }
                            return;
                        }
                        
                        const arr = Array.isArray(payload) ? payload : [payload];
                        captureFeeds(arr);
                        
                        // Track current playing feed for single-feed navigation/detail events
                        if (ev !== 'UserFeedsLoaded' && ev !== 'RecommendFeedsLoaded') {
                            if (payload && !Array.isArray(payload)) {
                                window.__wx_current_playing_feed = payload;
                            } else if (Array.isArray(payload) && payload.length === 1) {
                                window.__wx_current_playing_feed = payload[0];
                            }
                        }
                    } catch (e) {}
                });
            });
        })();

        // Fetch & XHR Interceptors as a robust fallback to auto-capture feed details & keys
        // (independent of JS file cache/hook states)
        try {
            // Intercept XMLHttpRequest
            const origOpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url, ...args) {
                this._url = url;
                return origOpen.call(this, method, url, ...args);
            };
            const origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(...args) {
                this.addEventListener('load', function() {
                    try {
                        if (this.responseText) {
                            const data = JSON.parse(this.responseText);
                            handleResponseData(data, this._url);
                        }
                    } catch (e) {}
                });
                return origSend.apply(this, args);
            };

            // Intercept Fetch transparently without modifying the returned Promise object
            const origFetch = window.fetch;
            window.fetch = function(resource, init) {
                const p = origFetch(resource, init);
                p.then(response => {
                    try {
                        const url = typeof resource === 'string' ? resource : (resource.url || '');
                        const clone = response.clone();
                        clone.json().then(data => {
                            handleResponseData(data, url);
                        }).catch(() => {});
                    } catch (e) {}
                }).catch(() => {});
                return p;
            };

            function handleResponseData(data, url) {
                try {
                    if (!data) return;
                    let feeds = [];
                    const foundFeeds = findFeedsInObject(data);
                    if (foundFeeds.length > 0) {
                        feeds = feeds.concat(foundFeeds);
                    }
                    if (feeds.length > 0) {
                        captureFeeds(feeds);
                    }
                } catch (err) {}
            }

            function findFeedsInObject(obj, visited = new Set()) {
                let results = [];
                if (!obj || typeof obj !== 'object' || visited.has(obj)) return results;
                visited.add(obj);

                if (obj.id && obj.objectDesc && obj.objectDesc.media) {
                    results.push(obj);
                }

                for (const key in obj) {
                    if (obj.hasOwnProperty(key)) {
                        results = results.concat(findFeedsInObject(obj[key], visited));
                    }
                }
                return results;
            }
        } catch (interceptErr) {
            console.error("[ChannelsSync] Hook interceptors setup failed:", interceptErr);
        }

        function reportDiag(message) {
            try {
                fetch('/__wx_channels_api/log-error', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message })
                }).catch(() => {});
            } catch (e) {}
        }

        window.WXU.onAPILoaded((variables) => {
            if (!variables) return;
            const keys = Object.keys(variables);
            for (let i = 0; i < keys.length; i++) {
                const key = keys[i];
                const methods = variables[key];
                if (methods && (typeof methods === "object" || typeof methods === "function")) {
                    if (typeof methods.finderGetCommentDetail === "function" || 
                        typeof methods.finderUserPage === "function" ||
                        typeof methods.finderSearch === "function" ||
                        typeof methods.finderLiveUserPage === "function" ||
                        typeof methods.finderGetFollowList === "function") {
                        if (!API_OBJECTS.includes(methods)) {
                            API_OBJECTS.push(methods);
                        }
                    }
                }
            }
            const ok = typeof WXAPI.finderUserPage === "function";
            console.log("[ChannelsSync] APILoaded, finderUserPage=" + ok);
            reportDiag("[ChannelsSync] APILoaded: exportVars=" + keys.length + ", hasFinderUserPage=" + ok + ", keysSample=" + keys.slice(0, 15).join(",") + ", finderUserPageType=" + typeof WXAPI.finderUserPage);
        });

        function injectFloatingUI() {
            if (document.getElementById('channels-sync-floating-btn')) return;

            const btn = document.createElement('div');
            btn.id = 'channels-sync-floating-btn';
            btn.innerHTML = '📥 同步当前作者作品到系统';
            btn.style.cssText = `
                position: fixed;
                bottom: 25px;
                right: 25px;
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
            `;
            btn.onmouseover = () => {
                btn.style.transform = 'scale(1.05)';
                btn.style.background = '#06ad53';
            };
            btn.onmouseout = () => {
                btn.style.transform = 'none';
                btn.style.background = '#07c160';
            };

            btn.onclick = startSyncProcess;
            document.body.appendChild(btn);
        }

        function injectBatchDownloadUI() {
            if (document.getElementById('channels-batch-download-floating-btn')) return;

            const btn = document.createElement('div');
            btn.id = 'channels-batch-download-floating-btn';
            btn.innerHTML = '⚡️ 批量下载当前作者视频';
            btn.style.cssText = `
                position: fixed;
                bottom: 145px;
                right: 25px;
                background: #ff9500;
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
            `;
            btn.onmouseover = () => {
                btn.style.transform = 'scale(1.05)';
                btn.style.background = '#e08200';
            };
            btn.onmouseout = () => {
                btn.style.transform = 'none';
                btn.style.background = '#ff9500';
            };

            btn.onclick = startBatchDownloadProcess;
            document.body.appendChild(btn);
        }

        async function startSyncProcess() {
            const btn = document.getElementById('channels-sync-floating-btn');
            if (btn.classList.contains('syncing')) return;
            btn.classList.add('syncing');
            btn.style.background = '#888';
            btn.style.cursor = 'default';
            btn.innerHTML = '⏳ 正在初始化...';

            try {
                if (!WXAPI.finderUserPage) {
                    alert('微信视频号 API 未完成初始化，请在微信中重新打开或刷新此页面重试！');
                    resetButton();
                    return;
                }

                const href = window.location.href;
                const params = new URLSearchParams(href.split('?')[1] || '');
                let username = params.get('username');
                if (!username) {
                    alert('无法从当前页面链接解析出作者 ID，请重新进入作者主页！');
                    resetButton();
                    return;
                }
                username = decodeURIComponent(username);
                if (username.indexOf('%') >= 0) {
                    username = decodeURIComponent(username);
                }

                let next_marker = "";
                let has_more = true;
                let total_synced = 0;

                let my_username = localStorage.getItem('__wx_main_finder_username') || username;
                my_username = decodeURIComponent(my_username);
                if (my_username.indexOf('%') >= 0) {
                    my_username = decodeURIComponent(my_username);
                }
                reportDiag("[ChannelsSync] startSyncProcess: username=" + username + ", finderUsername=" + my_username);

                while (has_more) {
                    btn.innerHTML = `⏳ 正在同步作品... (已同步 ` + total_synced + ` 个)`;
                    const payload = {
                        username: username,
                        finderUsername: my_username,
                        lastBuffer: next_marker,
                        needFansCount: 0,
                        objectId: "0"
                    };

                    const r = await WXAPI.finderUserPage(payload);
                    if (!r || r.errCode !== 0) {
                        alert('拉取视频作品列表失败: ' + (r ? r.errMsg : '未知错误'));
                        break;
                    }

                    const feeds = r.data.object || [];
                    if (feeds.length === 0) {
                        break;
                    }

                    const response = await fetch('/__wx_channels_api/sync-feed', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            username: username,
                            feeds: feeds
                        })
                    });

                    if (!response.ok) {
                        console.error('Failed to sync feeds page to proxy:', response.statusText);
                    }

                    total_synced += feeds.length;

                    if (!r.data.lastBuffer || feeds.length < 15) {
                        has_more = false;
                    } else {
                        next_marker = r.data.lastBuffer;
                    }
                    
                    await new Promise(resolve => setTimeout(resolve, 300));
                }

                btn.innerHTML = `✅ 同步成功！共 ` + total_synced + ` 个作品已存入系统`;
                btn.style.background = '#07c160';
                setTimeout(() => {
                    resetButton();
                }, 3000);

            } catch (err) {
                alert('同步失败: ' + err.message);
                resetButton();
            }

            function resetButton() {
                btn.classList.remove('syncing');
                btn.style.background = '#07c160';
                btn.style.cursor = 'pointer';
                btn.innerHTML = '📥 同步当前作者作品到系统';
            }
        }

        function selectBestSpec(feed) {
            const media = feed.objectDesc?.media?.[0];
            if (!media) return null;
            const specs = media.spec || [];
            if (specs.length === 0) return null;
            
            let bestSpec = specs[0];
            for (let i = 1; i < specs.length; i++) {
                const s = specs[i];
                const currentArea = (bestSpec.width || 0) * (bestSpec.height || 0);
                const sArea = (s.width || 0) * (s.height || 0);
                if (sArea > currentArea) {
                    bestSpec = s;
                } else if (sArea === currentArea) {
                    if ((s.codingFormat === 2 || s.codec === 2) && !(bestSpec.codingFormat === 2 || bestSpec.codec === 2)) {
                        bestSpec = s;
                    }
                }
            }
            return bestSpec.fileFormat;
        }

        async function startBatchDownloadProcess() {
            const btn = document.getElementById('channels-batch-download-floating-btn');
            if (btn.classList.contains('downloading')) return;
            btn.classList.add('downloading');
            btn.style.background = '#888';
            btn.style.cursor = 'default';
            btn.innerHTML = '⏳ 正在初始化...';

            try {
                if (!WXAPI.finderUserPage) {
                    alert('微信视频号 API 未完成初始化，请在微信中重新打开或刷新此页面重试！');
                    resetButton();
                    return;
                }

                const href = window.location.href;
                const params = new URLSearchParams(href.split('?')[1] || '');
                let username = params.get('username');
                if (!username) {
                    alert('无法从当前页面链接解析出作者 ID，请重新进入作者主页！');
                    resetButton();
                    return;
                }
                username = decodeURIComponent(username);
                if (username.indexOf('%') >= 0) {
                    username = decodeURIComponent(username);
                }

                let next_marker = "";
                let has_more = true;
                let allFeeds = [];

                let my_username = localStorage.getItem('__wx_main_finder_username') || username;
                my_username = decodeURIComponent(my_username);
                if (my_username.indexOf('%') >= 0) {
                    my_username = decodeURIComponent(my_username);
                }
                reportDiag("[ChannelsSync] startBatchDownloadProcess: username=" + username + ", finderUsername=" + my_username);

                while (has_more) {
                    btn.innerHTML = '⏳ 正在拉取作品列表... (已获取 ' + allFeeds.length + ' 个)';
                    const payload = {
                        username: username,
                        finderUsername: my_username,
                        lastBuffer: next_marker,
                        needFansCount: 0,
                        objectId: "0"
                    };

                    const r = await WXAPI.finderUserPage(payload);
                    if (!r || r.errCode !== 0) {
                        alert('拉取视频作品列表失败: ' + (r ? r.errMsg : '未知错误'));
                        break;
                    }

                    const feeds = r.data.object || [];
                    if (feeds.length === 0) {
                        break;
                    }

                    allFeeds = allFeeds.concat(feeds);

                    if (!r.data.lastBuffer || feeds.length < 15) {
                        has_more = false;
                    } else {
                        next_marker = r.data.lastBuffer;
                    }
                    
                    await new Promise(resolve => setTimeout(resolve, 300));
                }

                if (allFeeds.length === 0) {
                    alert('未找到任何作品！');
                    resetButton();
                    return;
                }

                let successCount = 0;
                let failCount = 0;

                for (let i = 0; i < allFeeds.length; i++) {
                    btn.innerHTML = '⏳ 正在提交下载任务... (' + (i + 1) + '/' + allFeeds.length + ')';
                    const feed = allFeeds[i];
                    
                    try {
                        const media = feed.objectDesc?.media?.[0];
                        if (!media || !media.url) {
                            failCount++;
                            continue;
                        }

                        const fileFormat = selectBestSpec(feed);
                        const video_url = media.url + (media.urlToken || "");
                        const final_url = fileFormat ? (video_url + (video_url.indexOf('?') >= 0 ? '&' : '?') + 'X-snsvideoflag=' + fileFormat) : video_url;

                        const response = await fetch('/__wx_channels_api/download', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                url: final_url,
                                description: feed.objectDesc.description || "视频号视频",
                                createtime: String(feed.createtime || 0),
                                key: String(media.decodeKey || "")
                            })
                        });

                        if (response.ok) {
                            successCount++;
                        } else {
                            failCount++;
                        }
                    } catch (e) {
                        failCount++;
                    }

                    await new Promise(resolve => setTimeout(resolve, 300));
                }

                btn.innerHTML = '✅ 成功提交: ' + successCount + '，失败: ' + failCount;
                btn.style.background = '#07c160';
                setTimeout(() => {
                    resetButton();
                }, 4000);

            } catch (err) {
                alert('批量下载失败: ' + err.message);
                resetButton();
            }

            function resetButton() {
                btn.classList.remove('downloading');
                btn.style.background = '#ff9500';
                btn.style.cursor = 'pointer';
                btn.innerHTML = '⚡️ 批量下载当前作者视频';
            }
        }

        // Floating video download UI
        function injectVideoDownloadUI() {
            if (document.getElementById('video-download-floating-btn')) return;

            const btn = document.createElement('div');
            btn.id = 'video-download-floating-btn';
            btn.style.cssText = `
                position: fixed;
                bottom: 85px;
                right: 25px;
                background: #0076ff;
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
                display: none;
            `;
            btn.onmouseover = () => {
                btn.style.transform = 'scale(1.05)';
                btn.style.background = '#0062d6';
            };
            btn.onmouseout = () => {
                btn.style.transform = 'none';
                btn.style.background = '#0076ff';
            };

            document.body.appendChild(btn);
            
            setInterval(() => {
                const videoEl = document.querySelector('video');
                if (!videoEl || !videoEl.src) {
                    btn.style.display = 'none';
                    return;
                }
                
                const src = videoEl.src;
                const feed = getFeedForVideo(src) || window.__wx_last_video_feed;
                if (feed && feed.objectDesc && feed.objectDesc.media && feed.objectDesc.media[0]) {
                    btn.style.display = 'block';
                    btn.innerHTML = '📥 下载当前播放视频';
                    btn.onclick = () => {
                        const specs = feed.objectDesc.media[0].spec || [];
                        if (specs.length > 1) {
                            showResolutionSelector(feed);
                        } else {
                            triggerVideoDownload(feed, null);
                        }
                    };
                } else {
                    btn.style.display = 'block';
                    btn.innerHTML = '📥 下载当前视频 (未提取到密钥)';
                    btn.onclick = () => triggerDirectVideoDownload(src);
                }
            }, 1000);
        }

        function getFeedForVideo(videoSrc) {
            if (!videoSrc) return null;
            
            if (window.__wx_current_playing_feed) {
                return window.__wx_current_playing_feed;
            }
            
            const videoEl = document.querySelector('video');
            const duration = videoEl ? videoEl.duration : 0;
            
            // 1. Try matching by path if videoSrc is a direct CDN URL
            let path = videoSrc.split('?')[0];
            path = path.replace(/^https?:\/\/[^\/]+/, '');
            if (path && !videoSrc.startsWith('blob:')) {
                for (const id in window.capturedFeeds) {
                    const feed = window.capturedFeeds[id];
                    const media = feed.objectDesc?.media?.[0];
                    if (media && media.url) {
                        let feedPath = media.url.split('?')[0];
                        feedPath = feedPath.replace(/^https?:\/\/[^\/]+/, '');
                        if (feedPath && (path.includes(feedPath) || feedPath.includes(path))) {
                            return feed;
                        }
                    }
                }
            }
            
            // 2. Try duration matching (highest precision, within 1.5 seconds tolerance)
            let durationMatches = [];
            if (duration > 0) {
                for (const id in window.capturedFeeds) {
                    const feed = window.capturedFeeds[id];
                    const media = feed.objectDesc?.media?.[0];
                    if (media) {
                        let feedDuration = media.videoPlayLen || 0;
                        if (feedDuration > 1000) {
                            feedDuration = feedDuration / 1000;
                        }
                        if (feedDuration > 0 && Math.abs(feedDuration - duration) < 1.5) {
                            durationMatches.push(feed);
                        }
                    }
                }
            }
            
            if (durationMatches.length === 1) {
                return durationMatches[0];
            }
            
            return null; // Eliminate broad DOM text matching which returns false positives on lists
        }

        function showResolutionSelector(feed) {
            const media = feed.objectDesc.media[0];
            const specs = media.spec || [];
            
            if (specs.length <= 1) {
                triggerVideoDownload(feed, null);
                return;
            }
            
            const overlay = document.createElement('div');
            overlay.id = 'resolution-selector-overlay';
            overlay.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                width: 100vw;
                height: 100vh;
                background: rgba(0,0,0,0.65);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 2147483647;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            `;
            
            const modal = document.createElement('div');
            modal.style.cssText = `
                background: #ffffff;
                padding: 24px;
                border-radius: 16px;
                width: 320px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.3);
                display: flex;
                flex-direction: column;
                gap: 12px;
                box-sizing: border-box;
                animation: scaleIn 0.2s ease-out;
            `;
            
            if (!document.getElementById('resolution-selector-style')) {
                const style = document.createElement('style');
                style.id = 'resolution-selector-style';
                style.innerHTML = `
                    @keyframes scaleIn {
                        from { transform: scale(0.9); opacity: 0; }
                        to { transform: scale(1); opacity: 1; }
                    }
                `;
                document.head.appendChild(style);
            }
            
            const title = document.createElement('div');
            title.innerHTML = '选择下载清晰度';
            title.style.cssText = `
                font-size: 16px;
                font-weight: bold;
                color: #333;
                text-align: center;
                margin-bottom: 8px;
            `;
            modal.appendChild(title);
            
            // Add original quality option at the top
            const origBtn = document.createElement('button');
            origBtn.innerHTML = '📥 原始视频质量 (默认)';
            origBtn.style.cssText = `
                background: #f5f5f7;
                border: none;
                padding: 12px;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 600;
                color: #0076ff;
                cursor: pointer;
                text-align: center;
                transition: all 0.2s;
            `;
            origBtn.onmouseover = () => {
                origBtn.style.background = '#e1efff';
                origBtn.style.transform = 'translateY(-1px)';
            };
            origBtn.onmouseout = () => {
                origBtn.style.background = '#f5f5f7';
                origBtn.style.transform = 'none';
            };
            origBtn.onclick = () => {
                document.body.removeChild(overlay);
                triggerVideoDownload(feed, null);
            };
            modal.appendChild(origBtn);
            
            specs.forEach(spec => {
                const optBtn = document.createElement('button');
                const w = spec.width || 0;
                const h = spec.height || 0;
                const codec = (spec.codingFormat === 2 || spec.codec === 2) ? 'H.265' : 'H.264';
                const resolutionText = w && h ? (w + 'x' + h + ' (' + codec + ')') : ('格式 ' + spec.fileFormat);
                
                optBtn.innerHTML = '📥 ' + resolutionText;
                optBtn.style.cssText = `
                    background: #f5f5f7;
                    border: none;
                    padding: 12px;
                    border-radius: 10px;
                    font-size: 14px;
                    font-weight: 600;
                    color: #0076ff;
                    cursor: pointer;
                    text-align: center;
                    transition: all 0.2s;
                `;
                optBtn.onmouseover = () => {
                    optBtn.style.background = '#e1efff';
                    optBtn.style.transform = 'translateY(-1px)';
                };
                optBtn.onmouseout = () => {
                    optBtn.style.background = '#f5f5f7';
                    optBtn.style.transform = 'none';
                };
                
                optBtn.onclick = () => {
                    document.body.removeChild(overlay);
                    triggerVideoDownload(feed, spec.fileFormat);
                };
                modal.appendChild(optBtn);
            });
            
            const cancelBtn = document.createElement('button');
            cancelBtn.innerHTML = '取消';
            cancelBtn.style.cssText = `
                background: transparent;
                border: 1px solid #d2d2d7;
                padding: 10px;
                border-radius: 10px;
                font-size: 14px;
                color: #86868b;
                cursor: pointer;
                text-align: center;
                margin-top: 4px;
                transition: background 0.2s;
            `;
            cancelBtn.onmouseover = () => cancelBtn.style.background = '#f5f5f7';
            cancelBtn.onmouseout = () => cancelBtn.style.background = 'transparent';
            cancelBtn.onclick = () => {
                document.body.removeChild(overlay);
            };
            modal.appendChild(cancelBtn);
            
            overlay.appendChild(modal);
            document.body.appendChild(overlay);
        }

        async function triggerVideoDownload(feed, fileFormat) {
            const btn = document.getElementById('video-download-floating-btn');
            if (btn.classList.contains('downloading')) return;
            btn.classList.add('downloading');
            btn.style.background = '#888';
            btn.innerHTML = '⏳ 正在提交下载...';

            try {
                const media = feed.objectDesc.media[0];
                const video_url = media.url + (media.urlToken || "");
                const final_url = fileFormat ? (video_url + (video_url.indexOf('?') >= 0 ? '&' : '?') + 'X-snsvideoflag=' + fileFormat) : video_url;

                const response = await fetch('/__wx_channels_api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: final_url,
                        description: feed.objectDesc.description || "视频号视频",
                        createtime: String(feed.createtime || 0),
                        key: String(media.decodeKey || "")
                    })
                });

                const data = await response.json();
                if (response.ok) {
                    btn.innerHTML = '✅ 已提交下载任务';
                    btn.style.background = '#0076ff';
                } else {
                    alert('下载失败: ' + (data.error || '未知错误'));
                    btn.innerHTML = '📥 下载当前播放视频';
                    btn.style.background = '#0076ff';
                }
            } catch (err) {
                alert('下载失败: ' + err.message);
                btn.innerHTML = '📥 下载当前播放视频';
                btn.style.background = '#0076ff';
            } finally {
                setTimeout(() => {
                    btn.classList.remove('downloading');
                }, 3000);
            }
        }

        async function triggerDirectVideoDownload(src) {
            if (src && src.startsWith('blob:')) {
                alert('提示：当前视频采用加密流媒体播放，由于微信视频号 API 未完成初始化，未提取到密钥，暂时无法下载。请尝试在微信中重新打开或刷新此页面以初始化 API 并提取密钥。');
                return;
            }
            const btn = document.getElementById('video-download-floating-btn');
            if (btn.classList.contains('downloading')) return;
            btn.classList.add('downloading');
            btn.style.background = '#888';
            btn.innerHTML = '⏳ 正在提交下载...';

            try {
                const response = await fetch('/__wx_channels_api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: src,
                        description: document.title || "视频号视频",
                        createtime: String(Math.floor(Date.now() / 1000)),
                        key: ""
                    })
                });

                const data = await response.json();
                if (response.ok) {
                    btn.innerHTML = '✅ 已提交下载任务';
                    btn.style.background = '#0076ff';
                } else {
                    alert('下载失败: ' + (data.error || '未知错误'));
                    btn.innerHTML = '📥 下载当前视频';
                    btn.style.background = '#0076ff';
                }
            } catch (err) {
                alert('下载失败: ' + err.message);
                btn.innerHTML = '📥 下载当前视频';
                btn.style.background = '#0076ff';
            } finally {
                setTimeout(() => {
                    btn.classList.remove('downloading');
                }, 3000);
            }
        }

        setInterval(() => {
            const pathname = window.location.pathname;
            const href = window.location.href;
            const params = new URLSearchParams(href.split('?')[1] || '');
            const hasUsername = !!params.get('username');
            
            if (pathname.includes('/web/pages/profile') || pathname.includes('/web/pages/home') || pathname.includes('/web/pages/feed')) {
                if (hasUsername) {
                    injectFloatingUI();
                    injectBatchDownloadUI();
                } else {
                    const syncBtn = document.getElementById('channels-sync-floating-btn');
                    if (syncBtn) syncBtn.remove();
                    const batchBtn = document.getElementById('channels-batch-download-floating-btn');
                    if (batchBtn) batchBtn.remove();
                }
                injectVideoDownloadUI();
            }
        }, 1000);
    })();
    """

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


# ── Proxy Service Manager (代理服务单例管理器) ─────────────────────

class ProxyManager:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        self.server_socket = None
        self.running = False
        self.thread = None
        self.port = 5202
        
    def start(self):
        if self.running:
            return True
            
        ensure_ca_certificates()
        
        # 1. Install certificate
        install_system_cert(CA_CERT_PATH)
        
        # 2. Listen on socket
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(("127.0.0.1", self.port))
            self.server_socket.listen(128)
        except Exception as e:
            print(f"Failed to bind proxy server on port {self.port}: {e}")
            return False
            
        self.running = True
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()
        
        # 3. Enable system proxy
        set_system_proxy(True, port=self.port)
        print(f"Channels MITM proxy started on 127.0.0.1:{self.port} and system proxy enabled.")
        return True
        
    def stop(self):
        if not self.running:
            return True
            
        self.running = False
        
        # Disable system proxy
        set_system_proxy(False, port=self.port)
        
        # Close listening socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
            self.server_socket = None
            
        print("Channels MITM proxy stopped and system proxy disabled.")
        return True
        
    def _run_server(self):
        while self.running:
            try:
                client_sock, addr = self.server_socket.accept()
                threading.Thread(target=handle_client_connection, args=(client_sock,), daemon=True).start()
            except Exception:
                if not self.running:
                    break
