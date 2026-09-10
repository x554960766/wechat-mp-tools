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

# 保存 MITM 启动前的 NO_PROXY 环境变量，用于停止时还原
_original_no_proxy = None
_last_captured_cred_hash = None
_last_captured_time = 0

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


def _run_certutil(args, check=False):
    """
    Spawn certutil safely, even inside a PyInstaller onefile package.

    PyInstaller prepends sys._MEIPASS to the process PATH, so a spawned certutil.exe
    resolves its dependent DLLs from the bundle dir and fails to initialize with
    STATUS_DLL_INIT_FAILED (0xc0000142). To resolve this robustly, we isolate the
    child process by setting its PATH environment variable to ONLY contain Windows
    system directories (System32 and SystemRoot), preventing any third-party or
    packaged DLLs from polluting the search path.
    """
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        system32 = os.path.join(system_root, "System32")
        certutil = os.path.join(system32, "certutil.exe")
        env = os.environ.copy()
        # Force the PATH to contain ONLY system paths to completely isolate certutil.exe
        # from any outside or packaged DLLs that might conflict.
        env["PATH"] = f"{system32}{os.pathsep}{system_root}"
        flags = 0x08000000  # CREATE_NO_WINDOW: 彻底阻止 Windows 弹出的黑色命令行闪烁窗口
        return subprocess.run(
            [certutil] + list(args),
            capture_output=True, check=check, env=env, cwd=system32,
            creationflags=flags
        )
    return subprocess.run(["certutil"] + list(args), capture_output=True, check=check)

_cert_trusted_cache = {"ts": 0.0, "val": False}

def check_cert_trusted():
    now = time.time()
    if now - _cert_trusted_cache["ts"] < 4.0:
        return _cert_trusted_cache["val"]

    def _eval():
        if sys.platform == "darwin":
            try:
                # 0. 先检查证书是否在钥匙串中存在，若不存在则必定未信任
                out_find = subprocess.run(
                    ["security", "find-certificate", "-c", "Channels Interceptor CA"],
                    capture_output=True
                )
                if out_find.returncode != 0:
                    return False

                def has_valid_trust_count(output_text):
                    lines = output_text.splitlines()
                    for i, line in enumerate(lines):
                        if "Channels Interceptor CA" in line:
                            for j in range(i + 1, min(i + 5, len(lines))):
                                if "Number of trust settings" in lines[j]:
                                    match = re.search(r"Number of trust settings\s*:\s*(\d+)", lines[j])
                                    if match:
                                        return True
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
                out = _run_certutil(["-verifystore", "-user", "root", "Channels Interceptor CA"])
                if out.returncode == 0:
                    return True
                out2 = _run_certutil(["-verifystore", "root", "Channels Interceptor CA"])
                if out2.returncode == 0:
                    return True
                return False
            except Exception:
                return False
        return False

    val = _eval()
    _cert_trusted_cache["ts"] = now
    _cert_trusted_cache["val"] = val
    return val

def _delete_all_matching_mac_certs(common_name: str = "Channels Interceptor CA"):
    """安全清空钥匙串中所有同名（包括历史重复/冲突的）证书，通过 SHA-1 哈希精准删除以杜绝歧义错误。"""
    try:
        res = subprocess.run(["security", "find-certificate", "-a", "-c", common_name, "-Z"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout:
            hashes = re.findall(r"SHA-1 hash:\s*([0-9A-Fa-f]+)", res.stdout)
            for h in set(hashes):
                subprocess.run(["security", "delete-certificate", "-Z", h], capture_output=True)
                subprocess.run(["security", "delete-certificate", "-Z", h, "/Library/Keychains/System.keychain"], capture_output=True)
    except Exception:
        pass
    try:
        subprocess.run(["security", "delete-certificate", "-c", common_name], capture_output=True)
    except Exception:
        pass


def install_system_cert(ca_cert_path):
    if check_cert_trusted():
        return True
        
    if sys.platform == "darwin":
        # 1. 尝试直接安装至用户 Login 钥匙串
        try:
            keychain_path = os.path.expanduser("~/Library/Keychains/login.keychain-db")
            if not os.path.exists(keychain_path):
                keychain_path = os.path.expanduser("~/Library/Keychains/login.keychain")
            res = subprocess.run([
                "security", "add-trusted-cert",
                "-r", "trustRoot",
                "-k", keychain_path,
                str(ca_cert_path)
            ], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and check_cert_trusted():
                return True
        except Exception as e:
            print(f"Login keychain installation note: {e}")
            print(f"Login keychain installation note: {e}")

        # 2. 唤起系统提权弹窗安装至系统钥匙串 (/Library/Keychains/System.keychain)
        try:
            cert_str = str(ca_cert_path)
            inner = f"/usr/bin/security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain '{cert_str}'"
            osa = f'do shell script "{inner}" with administrator privileges'
            res = subprocess.run(["osascript", "-e", osa], capture_output=True, text=True, timeout=30)
            if res.returncode == 0 and check_cert_trusted():
                return True
        except Exception as system_err:
            print(f"System keychain osascript failed: {system_err}")

        return check_cert_trusted()
    elif sys.platform == "win32":
        try:
            _run_certutil(["-addstore", "-user", "root", str(ca_cert_path)], check=True)
            return True
        except Exception as e:
            print(f"Failed to install Win cert: {e}")
            return False
    return False

def uninstall_system_cert():
    if sys.platform == "darwin":
        # 0. 若本身已不在钥匙串中且未受信任，直接判定为已卸载
        out_find = subprocess.run(
            ["security", "find-certificate", "-c", "Channels Interceptor CA"],
            capture_output=True
        )
        if out_find.returncode != 0 and not check_cert_trusted():
            return True

        cert_path = str(CA_CERT_PATH)
        # 1. 通过 SHA-1 哈希彻底清理用户登录钥匙串中的同名证书
        _delete_all_matching_mac_certs("Channels Interceptor CA")

        # 2. 清理用户域信任设置
        if CA_CERT_PATH.exists():
            try:
                subprocess.run(["security", "remove-trusted-cert", cert_path], capture_output=True)
            except Exception:
                pass

        # 3. 仅当证书确实存在于 System.keychain 时才调起系统管理员权限弹窗进行删除
        in_sys_kc = False
        try:
            r_sys = subprocess.run(
                ["security", "find-certificate", "-c", "Channels Interceptor CA", "/Library/Keychains/System.keychain"],
                capture_output=True
            )
            in_sys_kc = (r_sys.returncode == 0)
        except Exception:
            pass

        if in_sys_kc and CA_CERT_PATH.exists():
            inner = (
                f"/usr/bin/security remove-trusted-cert -d '{cert_path}' 2>/dev/null || true; "
                "/usr/bin/security delete-certificate -c 'Channels Interceptor CA' "
                "/Library/Keychains/System.keychain 2>/dev/null || true"
            )
            osa = (
                'do shell script "' + inner.replace('"', '\"') + '" '
                'with administrator privileges'
            )
            try:
                subprocess.run(["osascript", "-e", osa], capture_output=True, timeout=15)
            except Exception as e:
                print(f"osascript uninstall failed: {e}")

        # 4. 再次检查钥匙串：若已无该证书即代表卸载成功
        out_find_after = subprocess.run(
            ["security", "find-certificate", "-c", "Channels Interceptor CA"],
            capture_output=True
        )
        if out_find_after.returncode != 0:
            return True

        still_trusted = check_cert_trusted()
        if still_trusted:
            print("Cert still trusted after uninstall attempt")
        return not still_trusted
    elif sys.platform == "win32":
        try:
            _run_certutil(["-delstore", "-user", "root", "Channels Interceptor CA"])
            _run_certutil(["-delstore", "root", "Channels Interceptor CA"])
            return True
        except Exception as e:
            print(f"Failed to delete Win cert: {e}")
            return False
    return False

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

_original_socks_state = {}

def get_all_mac_network_services():
    """获取 macOS 上所有有效的网络服务名称列表（如 'USB 10/100 LAN', 'Wi-Fi' 等）"""
    services = []
    try:
        out = subprocess.check_output(["networksetup", "-listallnetworkservices"], timeout=3).decode("utf-8")
        for line in out.splitlines():
            line = line.strip()
            if line and not line.startswith("*") and "An asterisk" not in line:
                services.append(line)
    except Exception as e:
        print(f"Error listing network services: {e}")
    if not services:
        services = ["Wi-Fi"]
    return services

import socket

def _is_tcp_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def detect_upstream_proxy() -> str | None:
    """智能探测本机正在运行的 VPN / 科学上网代理服务（如 Clash, Surge, V2Ray, Clash Verge 等）。
    探测优先级：
    1. 检查 macOS/Windows 当前系统网络配置中原本设置的 HTTP/HTTPS/SOCKS 代理；
    2. 主动快速探活常见的本地 VPN 默认监听端口 (7897, 7890, 10809, 10808, 1087, 20811, 6152 等)。
    若探测到存活且非本服务的上游代理，返回格式如 'http://127.0.0.1:7897'；未运行 VPN 时返回 None。
    """
    # 1. 检查 macOS 系统中原有的 Web 代理设置
    if sys.platform == "darwin":
        try:
            services = get_all_mac_network_services()
            active = get_active_mac_service()
            if active in services:
                services.remove(active)
                services.insert(0, active)
            for s in services:
                for opt in ("-getwebproxy", "-getsecurewebproxy", "-getsocksfirewallproxy"):
                    try:
                        out = subprocess.check_output(["networksetup", opt, s], timeout=1.5, text=True)
                        if "Enabled: Yes" in out:
                            m_srv = re.search(r"Server:\s*(\S+)", out)
                            m_prt = re.search(r"Port:\s*(\d+)", out)
                            if m_srv and m_prt:
                                srv, prt = m_srv.group(1), int(m_prt.group(1))
                                if prt not in (5202, 5200) and _is_tcp_port_open(srv, prt):
                                    return f"http://{srv}:{prt}"
                    except Exception:
                        pass
        except Exception:
            pass

    # 2. 检查 Windows 注册表中原有的 ProxyServer
    elif sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                0,
                winreg.KEY_READ
            )
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            winreg.CloseKey(key)
            if enabled and server:
                if "=" in server:
                    parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
                    server = parts.get("http") or parts.get("https") or ""
                if server and ":" in server:
                    srv, prt_str = server.split(":", 1)
                    prt = int(prt_str)
                    if prt not in (5202, 5200) and _is_tcp_port_open(srv, prt):
                        return f"http://{srv}:{prt}"
        except Exception:
            pass

    # 3. 常见本地 VPN 客户端默认端口的主动探活（如 Clash Verge 7897, Clash 7890, V2Ray 10809, Surge 6152 等）
    common_vpn_ports = [7897, 7890, 10809, 10808, 1087, 20811, 6152]
    for port in common_vpn_ports:
        if port not in (5202, 5200) and _is_tcp_port_open("127.0.0.1", port):
            return f"http://127.0.0.1:{port}"

    return None

_original_mac_proxy_backup = {}

def set_mac_proxy(enabled, host="127.0.0.1", port=5202):
    global _original_socks_state, _original_mac_proxy_backup
    services = get_all_mac_network_services()
    active_service = get_active_mac_service()
    if active_service in services:
        services.remove(active_service)
        services.insert(0, active_service)

    for service in services:
        try:
            if enabled:
                # 备份原有 HTTP / HTTPS 代理配置，防止关闭时误杀用户的 VPN
                if service not in _original_mac_proxy_backup:
                    info = {}
                    try:
                        w_out = subprocess.check_output(["networksetup", "-getwebproxy", service], timeout=2, text=True)
                        if "Enabled: Yes" in w_out:
                            m_s = re.search(r"Server:\s*(\S+)", w_out)
                            m_p = re.search(r"Port:\s*(\d+)", w_out)
                            if m_s and m_p and int(m_p.group(1)) not in (port, 5200):
                                info["web"] = (m_s.group(1), m_p.group(1))
                    except Exception:
                        pass
                    try:
                        s_out = subprocess.check_output(["networksetup", "-getsecurewebproxy", service], timeout=2, text=True)
                        if "Enabled: Yes" in s_out:
                            m_s = re.search(r"Server:\s*(\S+)", s_out)
                            m_p = re.search(r"Port:\s*(\d+)", s_out)
                            if m_s and m_p and int(m_p.group(1)) not in (port, 5200):
                                info["secure"] = (m_s.group(1), m_p.group(1))
                    except Exception:
                        pass
                    _original_mac_proxy_backup[service] = info

                # 检查并临时关闭 PAC 自动代理（防止 PAC 脚本覆盖）
                try:
                    pac_out = subprocess.check_output(["networksetup", "-getautoproxyurl", service], timeout=2, text=True)
                    if "Enabled: Yes" in pac_out:
                        subprocess.run(["networksetup", "-setautoproxystate", service, "off"], capture_output=True)
                except Exception:
                    pass

                # 设置 HTTP / HTTPS 代理至 5202
                subprocess.run(["networksetup", "-setwebproxy", service, host, str(port)], check=True)
                subprocess.run(["networksetup", "-setsecurewebproxy", service, host, str(port)], check=True)
            else:
                # 关闭时：若原本开启了用户的 VPN 代理，则精准恢复，绝不切断用户的科学上网！
                orig = _original_mac_proxy_backup.get(service, {})
                if "web" in orig:
                    subprocess.run(["networksetup", "-setwebproxy", service, orig["web"][0], orig["web"][1]], check=True)
                    subprocess.run(["networksetup", "-setwebproxystate", service, "on"], check=True)
                else:
                    subprocess.run(["networksetup", "-setwebproxystate", service, "off"], check=True)

                if "secure" in orig:
                    subprocess.run(["networksetup", "-setsecurewebproxy", service, orig["secure"][0], orig["secure"][1]], check=True)
                    subprocess.run(["networksetup", "-setsecurewebproxystate", service, "on"], check=True)
                else:
                    subprocess.run(["networksetup", "-setsecurewebproxystate", service, "off"], check=True)
        except Exception as e:
            print(f"Error setting Mac proxy for {service} (enabled={enabled}): {e}")

_original_windows_pac = None
_original_win_proxy_backup = None

def set_windows_proxy(enabled, host="127.0.0.1", port=5202):
    global _original_windows_pac, _original_win_proxy_backup
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE
        )
        if enabled:
            # 备份用户原有的 ProxyServer 与 ProxyEnable
            try:
                penable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                pserver, _ = winreg.QueryValueEx(key, "ProxyServer")
                if penable and pserver and f":{port}" not in pserver:
                    _original_win_proxy_backup = (penable, pserver)
            except Exception:
                pass

            # 备份并临时清理 PAC 脚本，避免第三方代理的 PAC 规则冲突
            try:
                pac_val, _ = winreg.QueryValueEx(key, "AutoConfigURL")
                if pac_val:
                    _original_windows_pac = pac_val
                    winreg.DeleteValue(key, "AutoConfigURL")
            except FileNotFoundError:
                pass
            except Exception:
                pass

            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"{host}:{port}")
            # Ensure local and intranet addresses bypass the proxy to prevent local loops
            # with Flask (127.0.0.1:5200) and WebView2 / internal services.
            override_val = "<local>;localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.20.*;172.21.*;172.22.*;172.23.*;172.24.*;172.25.*;172.26.*;172.27.*;172.28.*;172.29.*;172.30.*;172.31.*;192.168.*"
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, override_val)
        else:
            if _original_win_proxy_backup:
                # 优雅还原用户原有的 VPN 代理设置
                penable, pserver = _original_win_proxy_backup
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, penable)
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, pserver)
                _original_win_proxy_backup = None
            else:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)

            if _original_windows_pac:
                try:
                    winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, _original_windows_pac)
                except Exception:
                    pass
                _original_windows_pac = None
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

    def __init__(self, ready_callback=None):
        self.ready_callback = ready_callback

    def running(self):
        if self.ready_callback:
            try:
                self.ready_callback()
            except Exception:
                pass

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
                    # Persist to file so Windows packaged builds can retrieve JS errors
                    try:
                        err_log = DATA_DIR / "frontend_errors.log"
                        with open(err_log, "a", encoding="utf-8") as _ef:
                            _ef.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
                    except Exception:
                        pass
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

            if path == "/__wx_official_api/mp-batch-next":
                self._forward_to_flask(
                    flow, "http://127.0.0.1:5200/api/auth/mp-batch-next"
                )
                return

            if path == "/__wx_official_api/mp-batch-status":
                self._forward_to_flask(
                    flow, "http://127.0.0.1:5200/api/auth/mp-batch-status"
                )
                return

            if path == "/__wx_official_api/log":
                try:
                    payload = json.loads(flow.request.get_text())
                    msg = payload.get("message", "")
                    print(f"[AutoRSSTool JS] {msg}", flush=True)
                except Exception as ex:
                    print(f"[AutoRSSTool JS LOG FAILED] {ex}", flush=True)
                self._local_json(flow, 200, b'{"success":true}')
                return

            # 静默截获 mp.weixin.qq.com 各端 (Web/PC 微信/公众号后台) 客户端凭证参数与 Cookie
            try:
                # 消除从 127.0.0.1 聚合页跳转造成的跨域 Referer 拦截 (导致微信返回「操作频繁，请稍后再试」)
                ref = flow.request.headers.get("Referer", "")
                if "127.0.0.1" in ref or "localhost" in ref:
                    flow.request.headers["Referer"] = "https://mp.weixin.qq.com/"

                import urllib.parse, re
                parsed = urllib.parse.urlparse(flow.request.url)
                qs = urllib.parse.parse_qs(parsed.query)

                if flow.request.method == "POST":
                    try:
                        if flow.request.urlencoded_form:
                            for k, v in flow.request.urlencoded_form.items():
                                if k not in qs and v:
                                    qs[k] = [v]
                    except Exception:
                        pass

                token = (qs.get("token") or qs.get("appmsg_token") or [""])[0]

                # 诊断：打印 profile_ext 请求的脱敏 URL（参数名可见、凭证值遮蔽），用于排查客户端协议变化
                if (parsed.path or "").startswith("/mp/profile_ext"):
                    from backend.cred_redact import redact_url
                    _diag_qs = urllib.parse.urlencode({k: f"<len={len(v[0])}>" for k, v in qs.items()})
                    print(f"[MP-DIAG] params: {_diag_qs}", flush=True)

                key = (qs.get("key") or [""])[0]
                pass_ticket = (qs.get("pass_ticket") or [""])[0]
                uin = (qs.get("uin") or [""])[0]
                poc_token = (qs.get("poc_token") or [""])[0]
                wxtoken = (qs.get("wxtoken") or ["777"])[0]
                cookie_str = flow.request.headers.get("Cookie", "")
                if cookie_str:
                    cookie_str = cookie_str.replace(", ", "; ")

                if token:
                    token = urllib.parse.unquote(token)

                if key:
                    key = urllib.parse.unquote(key)

                if poc_token:
                    poc_token = urllib.parse.unquote(poc_token)

                poc_sid = ""
                if cookie_str:
                    m = re.search(r'(?:^|;\s*)poc_sid=([^;,\s]+)', cookie_str)
                    if m:
                        poc_sid = urllib.parse.unquote(m.group(1))

                if not token and cookie_str:
                    m = re.search(r'(?:^|;\s*)appmsg_token=([^;,\s]+)', cookie_str) or re.search(r'(?:^|;\s*)token=([^;,\s]+)', cookie_str)
                    if m:
                        token = urllib.parse.unquote(m.group(1))

                if not key and cookie_str:
                    m = re.search(r'(?:^|;\s*)key=([^;,\s]+)', cookie_str)
                    if m:
                        key = urllib.parse.unquote(m.group(1))

                if not pass_ticket and cookie_str:
                    m = re.search(r'(?:^|;\s*)pass_ticket=([^;,\s]+)', cookie_str)
                    if m:
                        pass_ticket = urllib.parse.unquote(m.group(1))

                if not uin and cookie_str:
                    m = re.search(r'wxuin=([^;,\s]+)', cookie_str) or re.search(r'(?:^|;\s*)uin=([^;,\s]+)', cookie_str) or re.search(r'data_bizuin=([^;,\s]+)', cookie_str)
                    if m:
                        uin = urllib.parse.unquote(m.group(1))

                biz = (qs.get("__biz") or qs.get("biz") or [""])[0]
                if biz:
                    biz = urllib.parse.unquote(biz)

                user_agent = flow.request.headers.get("User-Agent", "")

                # 标记凭证来源：只有客户端自己发起的 profile_ext 请求（打开公众号主页/列表）携带的
                # key 才能用于 getmsg 拉取列表；文章页 /s 来源的 key 会被微信以 ret=-3(no session) 拒绝
                src_path = (parsed.path or "").rstrip("/")
                if src_path == "/mp/profile_ext":
                    biz_source = "profile_ext"
                elif src_path == "/s":
                    biz_source = "article"
                else:
                    biz_source = "other"

                if (token or pass_ticket or cookie_str or key) and (uin or "slave=" in cookie_str or "wxuin=" in cookie_str or "pass_ticket=" in cookie_str or key):
                    from backend.account_pool import account_pool, _normalize_uin
                    norm_uin = _normalize_uin(uin)
                    cred_hash = f"{norm_uin}_{token}_{key}_{pass_ticket}_{biz}_{poc_token}"
                    global _last_captured_cred_hash, _last_captured_time
                    now_ts = time.time()
                    if cred_hash != _last_captured_cred_hash or (now_ts - _last_captured_time > 5):
                        _last_captured_cred_hash = cred_hash
                        _last_captured_time = now_ts
                        cred_payload = {
                            "token": token,
                            "appmsg_token": token,
                            "key": key,
                            "pass_ticket": pass_ticket,
                            "poc_token": poc_token,
                            "poc_sid": poc_sid,
                            "wxtoken": wxtoken,
                            "uin": norm_uin,
                            "biz": biz,
                            "biz_source": biz_source,
                            "cookie_str": cookie_str,
                            "user_agent": user_agent,
                            "nickname": "动态微信凭证",
                            "save_time": time.time(),
                        }
                        def _bg_save_cred(payload):
                            try:
                                saved_acc = account_pool.add_or_update(payload)
                                if saved_acc:
                                    from backend.cred_redact import mask_secret
                                    print(f"[MITM Captured Credential] src={payload['biz_source']} uin={mask_secret(payload['uin'], 4)} biz={(payload['biz'] or '')[:10]} token={mask_secret(payload['token'], 6)} keylen={len(payload['key'] or '')} ptlen={len(payload['pass_ticket'] or '')}", flush=True)
                            except Exception as ex:
                                print(f"[AccountPool Async Save Error] {ex}", flush=True)
                        threading.Thread(target=_bg_save_cred, args=(cred_payload,), daemon=True).start()
            except Exception as cap_err:
                print(f"[MITM Capture Error] {cap_err}")

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
                    # DIAG: dump raw+hooked bundles to DATA_DIR for cross-platform debugging
                    _diag_bundles = ("virtual_svg-icons-register.publish", "connect.publish", "applyMic.publish")
                    for _db in _diag_bundles:
                        if _db in pathname:
                            try:
                                _tag = _db.replace(".", "_")
                                _dump_dir = DATA_DIR / "diag_bundles"
                                _dump_dir.mkdir(parents=True, exist_ok=True)
                                (_dump_dir / f"{_tag}_raw.js").write_text(js_content, encoding="utf-8")
                                (_dump_dir / f"{_tag}_hooked.js").write_text(modified, encoding="utf-8")
                                print(f"[DIAG] dumped {_db} bundle raw+hooked to {_dump_dir}", flush=True)
                            except Exception as _e:
                                print(f"[DIAG] dump failed: {_e}", flush=True)
                            break
                    flow.response.text = modified
            except Exception as ex:
                print(f"[Proxy] Error hooking JS bundle: {ex}", flush=True)
            return

        # 2. Handle 302 redirect on /web/pages/feed — prevent redirect to home page
        #    (Borrowed from wx_channels_download: when the feed page returns 302,
        #     re-fetch with modified query params to get the actual page content)
        if host == "channels.weixin.qq.com":
            path_only = flow.request.path.split("?", 1)[0]
            if path_only == "/web/pages/feed" and flow.response.status_code == 302:
                try:
                    import urllib.parse
                    original_url = flow.request.url
                    parsed = urllib.parse.urlparse(original_url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    qs["flow"] = ["2"]
                    qs["fpid"] = ["FinderLike"]
                    qs["bus"] = [str(int(time.time()))]
                    qs["entrance_id"] = ["1002"]
                    qs["wx_header"] = ["0"]
                    new_query = urllib.parse.urlencode(qs, doseq=True)
                    retry_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
                    headers = dict(flow.request.headers)
                    headers.pop("accept-encoding", None)
                    headers.pop("Accept-Encoding", None)
                    resp2 = __import__("requests").get(
                        retry_url, headers=headers, timeout=10,
                        allow_redirects=False, verify=False
                    )
                    if resp2.status_code == 200:
                        from mitmproxy import http
                        flow.response = http.Response.make(
                            200, resp2.content,
                            {"Content-Type": resp2.headers.get("Content-Type", "text/html; charset=utf-8")}
                        )
                        content_type = flow.response.headers.get("content-type", "").lower()
                        print(f"[Proxy] Intercepted 302 on /web/pages/feed, re-fetched successfully", flush=True)
                except Exception as ex302:
                    print(f"[Proxy] Failed to intercept 302 on /web/pages/feed: {ex302}", flush=True)

        # 3. Check for channels.weixin.qq.com / mp.weixin.qq.com HTML
        if host == "mp.weixin.qq.com" and "/mp/profile_ext" in flow.request.path:
            # 仅在显式批量流转模式 (URL 明确带 batch_mode=1) 且非真实异步 XHR 时才封装为流转 HUD 网页
            # 避免被 f=json 参数误判为 XHR，确保顶级页面导航能够正常呈现流转 HUD 动画并执行注入脚本
            is_explicit_batch = "batch_mode=1" in flow.request.url
            is_xhr_header = flow.request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
            if is_explicit_batch and not is_xhr_header and "text/html" not in content_type:
                # 将 API 响应动态封装为包含可视 HUD 动画和自动流转脚本的完整流转网页
                raw_text = flow.response.get_text(strict=False) or "{}"
                flow.response.headers["content-type"] = "text/html; charset=utf-8"
                flow.response.text = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>正在同步公众号会话凭证</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            background: #f7f8fa;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            color: #191919;
            text-align: center;
        }}
        .card {{
            background: white;
            padding: 32px 28px;
            border-radius: 16px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.06);
            max-width: 360px;
            width: 90%;
        }}
        .icon {{
            font-size: 44px;
            margin-bottom: 12px;
            animation: spin 2s linear infinite;
        }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
        h2 {{
            font-size: 1.15rem;
            margin: 0 0 8px 0;
        }}
        p {{
            font-size: 0.85rem;
            color: #777;
            margin: 0 0 16px 0;
            line-height: 1.5;
        }}
        .progress-bar {{
            height: 6px;
            background: #eee;
            border-radius: 3px;
            overflow: hidden;
        }}
        .progress-bar-fill {{
            height: 100%;
            background: #07c160;
            width: 100%;
            animation: pulse 1.2s infinite ease-in-out;
        }}
        @keyframes pulse {{ 0% {{ opacity: 0.4; }} 50% {{ opacity: 1; }} 100% {{ opacity: 0.4; }} }}
    </style>
    <script>{get_injected_official_js()}</script>
</head>
<body>
    <div class="card">
        <div class="icon">🔄</div>
        <h2 id="stream-title">正在自动同步凭证...</h2>
        <p id="stream-desc">当前公众号主页凭证已成功捕获，正在准备流转下一个...</p>
        <div class="progress-bar">
            <div class="progress-bar-fill"></div>
        </div>
    </div>
    <div id="raw-resp" style="display:none;">{raw_text}</div>
</body>
</html>"""
                for h in ("etag", "last-modified", "expires", "content-security-policy", "x-content-security-policy", "x-webkit-csp", "x-frame-options", "frame-options"):
                    if h in flow.response.headers:
                        del flow.response.headers[h]
                flow.response.headers["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
                return
        elif "text/html" not in content_type:
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
        elif host == "mp.weixin.qq.com":
            inject_script = f"<script>{get_injected_official_js()}</script>"
            print(f"[Proxy Injected Official JS] path={flow.request.path}", flush=True)

            # 参考 wechatDownload：从文章 HTML (/s) 页面嗅探 biz、appmsg_token 与 nickname
            try:
                if "/s" in flow.request.path:
                    m_biz = re.search(r'(?:var\s+biz\s*=\s*|window\.biz\s*=\s*|__biz=)"?([^"&\'\s]+)"?', html)
                    m_token = re.search(r'(?:var\s+appmsg_token\s*=\s*|window\.appmsg_token\s*=\s*|appmsg_token=)"?([^"&\'\s]+)"?', html)
                    m_nick = re.search(r'(?:var\s+nickname\s*=\s*|window\.nickname\s*=\s*)"([^"\r\n]+)"', html)
                    sniffed_biz = m_biz.group(1) if m_biz else ""
                    sniffed_token = m_token.group(1) if m_token else ""
                    sniffed_nick = m_nick.group(1) if m_nick else ""
                    if sniffed_biz:
                        from backend.account_pool import account_pool, AccountPool
                        if AccountPool._is_favorite_biz(sniffed_biz):
                            account_pool.add_or_update({
                                "biz": sniffed_biz,
                                "appmsg_token": sniffed_token,
                                "nickname": sniffed_nick or "动态微信凭证",
                                "biz_name": sniffed_nick,
                                "biz_source": "article",
                                "save_time": time.time(),
                            })
            except Exception as ex_sniff:
                print(f"[Proxy HTML Sniff Error] {ex_sniff}", flush=True)
        else:
            return

        if host == "mp.weixin.qq.com":
            meta_csp = re.search(r"<meta[^>]+http-equiv=[\"']?Content-Security-Policy[\"']?[^>]*>", html, flags=re.IGNORECASE)
            if meta_csp:
                print(f"[DIAG CSP] Found <meta CSP> tag, stripping: {meta_csp.group(0)}", flush=True)
                html = html.replace(meta_csp.group(0), "")

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

        for h in ("content-security-policy", "x-content-security-policy", "x-webkit-csp", "x-frame-options", "frame-options"):
            if h in flow.response.headers:
                del flow.response.headers[h]

        for h in ("content-security-policy", "x-content-security-policy", "x-webkit-csp", "x-frame-options", "frame-options"):
            if h in flow.response.headers:
                del flow.response.headers[h]


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
            "decode_key": decode_key,
            "nickname": feed.get("contact", {}).get("nickname") or nickname,
            "like_count": feed.get("likeCount", 0),
            "comment_count": feed.get("commentCount", 0),
            "forward_count": feed.get("forwardCount", 0),
            "fav_count": feed.get("favCount", 0),
        }
        
        found = False
        for ex_item in feeds_db[username]:
            if ex_item.get("id") == feed_id:
                ex_item.update(item)
                found = True
                break
        if not found:
            item["needs_upload"] = True
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

def _extract_function_body(js, open_brace_pos):
    """Extract a JS function body using brace balancing, starting right after the '{' at open_brace_pos.

    Skips single/double/template-literal strings and // / /* */ comments so that
    braces inside them don't break balancing.

    Returns (body_str, end_pos) where end_pos is the index of the closing '}',
    or None if balancing fails (e.g. truncated bundle).
    """
    i = open_brace_pos + 1  # skip the opening '{'
    depth = 1
    n = len(js)
    while i < n and depth > 0:
        ch = js[i]
        if ch in ('"', "'"):
            # Skip string literal
            quote = ch
            i += 1
            while i < n and js[i] != quote:
                if js[i] == '\\':
                    i += 1  # skip escaped char
                i += 1
            i += 1  # skip closing quote
            continue
        if ch == '`':
            # Skip template literal (handle ${...} by tracking nested depth within)
            i += 1
            while i < n and js[i] != '`':
                if js[i] == '\\':
                    i += 1
                elif js[i] == '$' and i + 1 < n and js[i + 1] == '{':
                    # Template expression: track nested braces within ${...}
                    i += 2  # skip '${'
                    tdepth = 1
                    while i < n and tdepth > 0:
                        tc = js[i]
                        if tc == '{':
                            tdepth += 1
                        elif tc == '}':
                            tdepth -= 1
                        elif tc in ('"', "'", '`'):
                            # skip nested string inside template expression
                            tq = tc
                            i += 1
                            while i < n and js[i] != tq:
                                if js[i] == '\\':
                                    i += 1
                                i += 1
                        i += 1
                    continue  # tdepth closed the '}' of ${...}
                i += 1
            i += 1  # skip closing backtick
            continue
        if ch == '/' and i + 1 < n:
            next_ch = js[i + 1]
            if next_ch == '/':
                # Line comment
                i += 2
                while i < n and js[i] != '\n':
                    i += 1
                continue
            if next_ch == '*':
                # Block comment
                i += 2
                while i < n and not (js[i] == '*' and i + 1 < n and js[i + 1] == '/'):
                    i += 1
                i += 2  # skip '*/'
                continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return (js[open_brace_pos + 1 : i], i)
        i += 1
    return None  # balancing failed


def _hook_async_method(js_script, func_name, wrapper_template, has_param=True):
    """Hook an async method by name using brace-balanced extraction.

    func_name:        e.g. "finderInit" or "finderPcFlow"
    wrapper_template: a format string with {param} and {body} placeholders, e.g.
                      "var result=await(async()=>{{ {body} }})();...;return result;"
    has_param:        True if the function has one parameter, False if zero-arg.

    Returns (modified_js, matched) where matched indicates if the hook was applied.
    """
    if has_param:
        pattern = re.compile(r'async\s+' + re.escape(func_name) + r'\s*\(\s*(\w+)\s*\)\s*\{')
    else:
        pattern = re.compile(r'async\s+' + re.escape(func_name) + r'\s*\(\s*\)\s*\{')

    m = pattern.search(js_script)
    if not m:
        return js_script, False

    open_brace = m.end() - 1  # position of '{'
    result = _extract_function_body(js_script, open_brace)
    if result is None:
        print(f"[Bundle Hook] WARNING: brace balancing failed for {func_name}, skipping hook", flush=True)
        return js_script, False

    body, close_brace_pos = result
    param = m.group(1) if has_param else ""

    # Build the replacement function body
    new_body = wrapper_template.format(param=param, body=body)

    # Reconstruct: everything before '{' + '{' + new_body + '}' + everything after '}'
    if has_param:
        func_sig = f"async {func_name}({param})"
    else:
        func_sig = f"async {func_name}()"
    replacement = func_sig + "{" + new_body + "}"
    js_script = js_script[:m.start()] + replacement + js_script[close_brace_pos + 1:]
    return js_script, True


def _hook_wx_bundle(pathname, js_script):
    v = "?t=local"
    
    # Cache-busting imports in JS
    js_script = re.sub(r'from\s*"([^"]+)\.js"', r'from "\1.js' + v + '"', js_script)
    js_script = re.sub(r'"js/([^"]+)\.js"', r'"js/\1.js' + v + '"', js_script)
    js_script = re.sub(r'import\("([^"]+)\.js"\)', r'import("\1.js' + v + '")', js_script)
    js_script = re.sub(r'import\s*"([^"]+)\.js"', r'import "\1.js' + v + '"', js_script)

    if "virtual_svg-icons-register.publish" in pathname:
        print("[Bundle Hook] Hooking virtual_svg-icons-register.publish", flush=True)
        hook_report = {}

        # 1. finderInit (no param)
        js_script, ok = _hook_async_method(js_script, "finderInit",
            "var result=await(async()=>{{ {body} }})();var data=result.data;WXU.emit(WXU.Events.Init,data);return result;",
            has_param=False)
        hook_report["finderInit"] = ok

        # 2. finderPcFlow
        js_script, ok = _hook_async_method(js_script, "finderPcFlow",
            "var result=await(async()=>{{ {body} }})();var feeds=result.data.object;WXU.emit(WXU.Events.PCFlowLoaded,feeds);return result;")
        hook_report["finderPcFlow"] = ok

        # 3. finderGetRecommend
        js_script, ok = _hook_async_method(js_script, "finderGetRecommend",
            "var result=await(async()=>{{ {body} }})();var feeds=result.data.object;WXU.emit(WXU.Events.RecommendFeedsLoaded,feeds);return result;")
        hook_report["finderGetRecommend"] = ok

        # 4. finderGetCommentDetail
        js_script, ok = _hook_async_method(js_script, "finderGetCommentDetail",
            "var result=await(async()=>{{ {body} }})();var feed=result.data.object;WXU.emit(WXU.Events.FeedProfileLoaded,feed);return result;")
        hook_report["finderGetCommentDetail"] = ok

        # 5. finderGetCommentList
        js_script, ok = _hook_async_method(js_script, "finderGetCommentList",
            "var result=await(async()=>{{ {body} }})();WXU.emit(WXU.Events.FeedCommentListLoaded,result.data);return result;")
        hook_report["finderGetCommentList"] = ok

        # 6. finderPCSearch
        js_script, ok = _hook_async_method(js_script, "finderPCSearch",
            "var result=await(async()=>{{ {body} }})();return result;")
        hook_report["finderPCSearch"] = ok

        # 7. finderSearch
        js_script, ok = _hook_async_method(js_script, "finderSearch",
            "var result=await(async()=>{{ {body} }})();return result;")
        hook_report["finderSearch"] = ok

        # 8. finderGetInteractionedFeedList
        js_script, ok = _hook_async_method(js_script, "finderGetInteractionedFeedList",
            "var result=await(async()=>{{ {body} }})();var feeds=result.data.object;WXU.emit(WXU.Events.InteractionedFeedsLoaded,feeds);return result;")
        hook_report["finderGetInteractionedFeedList"] = ok

        # 9. finderUserPage
        js_script, ok = _hook_async_method(js_script, "finderUserPage",
            "var result=await(async()=>{{ {body} }})();var feeds=result.data.object;WXU.emit(WXU.Events.UserFeedsLoaded,{{feeds:feeds,lastBuffer:result.data.lastBuffer,raw:result.data}});return result;")
        hook_report["finderUserPage"] = ok

        # 10. finderLiveUserPage
        js_script, ok = _hook_async_method(js_script, "finderLiveUserPage",
            "var result=await(async()=>{{ {body} }})();var feeds=result.data.object;WXU.emit(WXU.Events.LiveUserFeedsLoaded,feeds);return result;")
        hook_report["finderLiveUserPage"] = ok

        # 11. finderGetLiveInfo
        js_script, ok = _hook_async_method(js_script, "finderGetLiveInfo",
            "var result=await(async()=>{{ {body} }})();var live=result.data;WXU.emit(WXU.Events.LiveProfileLoaded,live);return result;")
        hook_report["finderGetLiveInfo"] = ok

        # 12. joinLive
        js_script, ok = _hook_async_method(js_script, "joinLive",
            "var result=await(async()=>{{ {body} }})();var data=result.data;WXU.emit(WXU.Events.JoinLive,data);return result;")
        hook_report["joinLive"] = ok

        hit = sum(1 for v in hook_report.values() if v)
        print(f"[Bundle Hook] Hook report: {hit}/12 applied — {hook_report}", flush=True)

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
        
        api_methods_escaped = api_methods.replace('\\', '\\\\')
        js_wxapi = f";WXU.emit(WXU.Events.APILoaded,{api_methods_escaped});export{{"
        js_script, n_subs = re.subn(r'exports?\s*\{', js_wxapi, js_script, count=1)
        if n_subs == 0:
            print("[Bundle Hook] WARNING: export{} pattern not found in virtual_svg bundle", flush=True)

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
        
        // 3. Inject Auto-Refresh Keep-Alive Loop for Official Account Credentials inside PC WeChat WebView
        function startAutoCredentialRefreshLoop() {
            if (window.self !== window.top) {
                // In iframe mode (e.g. inside batch portal), credentials are automatically captured by MITM on request
                return;
            }
            // 0. 批量授权管道流转 (支持 URL query batch_mode=1 或 profile_ext 页面自动流转)
            try {
                const urlParams = new URLSearchParams(window.location.search);
                const isBatchMode = urlParams.get('batch_mode') === '1' || sessionStorage.getItem('mp_batch_mode') === 'true';
                let currentBiz = urlParams.get('__biz') || window.biz || '';
                if (!currentBiz && document.documentElement) {
                    const m = document.documentElement.outerHTML.match(/(?:var\s+biz\s*=\s*|__biz=)"?([^"&'\s]+)"?/);
                    if (m && m[1]) currentBiz = m[1];
                }

                if (isBatchMode && currentBiz && !window.location.pathname.includes('/s')) {
                    // 若页面中无 HUD 元素（如原生公众号主页），动态注入顶部悬浮指示条
                    let desc = document.getElementById('stream-desc');
                    let title = document.getElementById('stream-title');
                    if (!desc) {
                        const hud = document.createElement('div');
                        hud.id = 'stream-hud-floating';
                        hud.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:rgba(20,20,20,0.88);color:#fff;padding:10px 20px;border-radius:24px;z-index:999999;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,0.2);display:flex;align-items:center;gap:10px;font-family:-apple-system,sans-serif;backdrop-filter:blur(10px);pointer-events:none;';
                        hud.innerHTML = '<span style="font-size:16px;display:inline-block;animation:stream-spin 2s linear infinite;">🔄</span><span id="stream-desc">正在自动同步凭证...</span><style>@keyframes stream-spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}</style>';
                        if (document.body) {
                            document.body.appendChild(hud);
                        } else {
                            document.addEventListener('DOMContentLoaded', () => document.body.appendChild(hud));
                        }
                        desc = document.getElementById('stream-desc');
                    }

                    fetch('/__wx_official_api/mp-batch-next?current_biz=' + encodeURIComponent(currentBiz))
                        .then(r => r.json())
                        .then(data => {
                            if (data && data.next_url) {
                                remoteLog('✅ 凭证已捕获，即将流转至第 [' + (data.step + 1) + '/' + data.total + '] 个公众号【' + (data.next_name || '') + '】...');
                                if (title) title.innerText = '✅ 凭证捕获成功';
                                if (desc) desc.innerText = '✅ 凭证已捕获，即将流转至第 [' + (data.step + 1) + '/' + data.total + '] 个【' + (data.next_name || '') + '】...';
                                setTimeout(() => {
                                    window.location.href = data.next_url;
                                }, 1800);
                            } else if (data && data.completed) {
                                try { sessionStorage.removeItem('mp_batch_mode'); } catch(e) {}
                                remoteLog('🎉 全部公众号凭证已完成捕获，正在返回聚合控制台');
                                if (title) title.innerText = '🎉 全部授权完成！';
                                if (desc) desc.innerText = '🎉 全部 ' + data.total + ' 个公众号会话凭据已全部就绪，正在返回控制台...';
                                setTimeout(() => {
                                    window.location.href = 'http://127.0.0.1:5200/api/auth/mp-batch-portal';
                                }, 1200);
                            }
                        }).catch(err => {
                            if (desc) desc.innerText = '流转请求异常: ' + err.message;
                        });
                }
            } catch(e) {}

            // 生产配置：动态随机区间 (探针 4~6 分钟，兜底 45~55 分钟)
            function getNextPingInterval() {
                return Math.floor(240000 + Math.random() * 120000); // 240s ~ 360s (4 ~ 6分钟)
            }

            function getNextSafetyReloadInterval() {
                return Math.floor(2700000 + Math.random() * 600000); // 2700s ~ 3300s (45 ~ 55分钟)
            }

            function remoteLog(message) {
                try {
                    fetch('/__wx_official_api/log', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: message })
                    }).catch(() => {});
                } catch (e) {}
            }

            remoteLog('凭证自动续期已激活 (动态随机模式：探针4-6分钟/兜底45-55分钟)');

            function findArticleLinkFromDom() {
                // 1. 尝试从 window.msgList 或 window.cgiData 中读取 JSON
                try {
                    let rawList = window.msgList || (window.cgiData && window.cgiData.msgList);
                    if (typeof rawList === 'string' && rawList.trim()) {
                        let txt = rawList.replace(/&quot;/g, '"').replace(/&amp;/g, '&');
                        rawList = JSON.parse(txt);
                    }
                    if (rawList && rawList.list && rawList.list.length > 0) {
                        for (const item of rawList.list) {
                            const info = item.app_msg_ext_info;
                            if (info && info.content_url) {
                                let u = info.content_url.replace(/\\/g, '').replace(/&amp;/g, '&');
                                if (u.startsWith('http://') || u.startsWith('https://') || u.startsWith('/s')) {
                                    if (u.startsWith('/s')) u = 'https://mp.weixin.qq.com' + u;
                                    return u;
                                }
                            }
                        }
                    }
                } catch(e) {}

                // 2. 尝试从 DOM 元素中提取
                try {
                    const selectors = ['a[href]', '[href_src]', '[data-link]', '[data-url]', '.weui_media_title', '.msg_title', '.appmsg_title', '.appmsg_title_link'];
                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            let val = el.getAttribute('href') || el.getAttribute('href_src') || el.getAttribute('data-link') || el.getAttribute('data-url') || '';
                            if (el.tagName === 'A' && el.href && !el.href.startsWith('javascript:')) {
                                val = el.href;
                            }
                            val = val.replace(/&amp;/g, '&');
                            if (val && (val.includes('/s?') || val.includes('/s/')) && !val.startsWith('javascript:')) {
                                if (val.startsWith('/s')) val = 'https://mp.weixin.qq.com' + val;
                                return val;
                            }
                        }
                    }
                } catch(e) {}

                // 3. 尝试直接在 outerHTML 匹配 /s?__biz= 文章链接
                try {
                    const html = document.documentElement ? document.documentElement.outerHTML : '';
                    const match = html.match(/(?:https?:\/\/mp\.weixin\.qq\.com)?\/s\?__biz=[^"'\s<>&]+(?:&amp;[^"'\s<>]+)*/i);
                    if (match && match[0]) {
                        let u = match[0].replace(/\\/g, '').replace(/&amp;/g, '&');
                        if (u.startsWith('/s')) u = 'https://mp.weixin.qq.com' + u;
                        return u;
                    }
                } catch(e) {}

                return null;
            }

            function forceReloadPage() {
                const urlParams = new URLSearchParams(window.location.search);
                const biz = urlParams.get('__biz') || window.biz || '';
                const onArticlePage = window.location.pathname.includes('/s');

                if (onArticlePage && biz) {
                    // 文章详情页 -> 跳转到该公众号文章列表页（不同页面，PC 微信重新签发凭证）
                    remoteLog('导航至列表页获取新凭证');
                    try {
                        window.location.href = '/mp/profile_ext?action=home&__biz=' + biz + '&scene=124&_t=' + Date.now();
                        return;
                    } catch(e) {}
                } else {
                    // 列表页 -> 从页面中提取一篇文章链接跳转过去
                    const articleUrl = findArticleLinkFromDom();
                    if (articleUrl) {
                        remoteLog('导航至文章页获取新凭证: ' + articleUrl);
                        try {
                            window.location.href = articleUrl;
                            return;
                        } catch(e) {}
                    }
                }
                // 兜底：剥除旧 key 重载当前页面
                remoteLog('剥除旧 key 重载当前页面');
                try {
                    const u = new URL(window.location.href);
                    u.searchParams.delete('key');
                    u.searchParams.delete('pass_ticket');
                    u.searchParams.delete('uin');
                    u.searchParams.delete('exportkey');
                    u.searchParams.set('_t', Date.now());
                    window.location.href = u.toString();
                    return;
                } catch(e) {}
                try { window.location.href = window.location.href; return; } catch(e) {}
                try { window.location.reload(); } catch(e) {}
            }

            function doPing() {
                try {
                    const urlParams = new URLSearchParams(window.location.search);
                    let biz = urlParams.get('__biz') || window.biz || (window.cgiData && window.cgiData.biz) || '';
                    let appmsg_token = urlParams.get('appmsg_token') || window.appmsg_token || '';
                    let key = urlParams.get('key') || window.key || '';
                    let pass_ticket = urlParams.get('pass_ticket') || window.pass_ticket || '';
                    let uin = urlParams.get('uin') || window.uin || '';

                    if (!biz && document.documentElement) {
                        const m = document.documentElement.outerHTML.match(/(?:var\s+biz\s*=\s*|__biz=)"?([^"&'\s]+)"?/);
                        if (m && m[1]) biz = m[1];
                    }

                    if (!biz) return;

                    let pingUrl = `/mp/profile_ext?action=getmsg&__biz=${encodeURIComponent(biz)}&f=json&offset=0&count=1&is_ok=1&scene=126`;
                    if (appmsg_token) pingUrl += `&appmsg_token=${encodeURIComponent(appmsg_token)}`;
                    if (key) pingUrl += `&key=${encodeURIComponent(key)}`;
                    if (pass_ticket) pingUrl += `&pass_ticket=${encodeURIComponent(pass_ticket)}`;
                    if (uin) pingUrl += `&uin=${encodeURIComponent(uin)}`;

                    fetch(pingUrl, { method: 'GET', cache: 'no-cache', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                        .then(r => r.json())
                        .then(data => {
                            if (data && (data.ret === -3 || data.ret === -4 || data.ret === 200003)) {
                                remoteLog('凭证已过期 (ret=' + data.ret + ')，立即刷新');
                                forceReloadPage();
                            }
                        })
                        .catch(() => {});
                } catch (e) {}
            }

            function scheduleNextPing() {
                const nextMs = getNextPingInterval();
                setTimeout(() => {
                    doPing();
                    scheduleNextPing();
                }, nextMs);
            }

            function scheduleNextSafetyReload() {
                const nextMs = getNextSafetyReloadInterval();
                setTimeout(() => {
                    forceReloadPage();
                    scheduleNextSafetyReload();
                }, nextMs);
            }

            setTimeout(doPing, 1000);
            scheduleNextPing();
            scheduleNextSafetyReload();

            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) doPing();
            });
        }

        const interval = setInterval(() => {
            if (document.body) {
                clearInterval(interval);
                // 仅在文章详情页注入下载按钮
                if (window.location.pathname.includes('/s')) {
                    injectDetailFloatingButton();
                    setInterval(injectListDownloadButtons, 1000);
                }
                // 仅在显式带 batch_mode=1 参数时执行批量流转（普通公众号主页绝不干扰与劫持）
                try {
                    const urlParams = new URLSearchParams(window.location.search);
                    if (urlParams.get('batch_mode') === '1') {
                        startAutoCredentialRefreshLoop();
                    }
                } catch(e) {}
            }
        }, 100);
    })();
    """


try:
    import mitmproxy.log
    _orig_legacy_emit = mitmproxy.log.LegacyLogEvents.emit
    def _safe_legacy_emit(self, record):
        try:
            if getattr(self, "master", None) and getattr(self.master, "event_loop", None):
                if not self.master.event_loop.is_closed():
                    _orig_legacy_emit(self, record)
        except Exception:
            pass
    mitmproxy.log.LegacyLogEvents.emit = _safe_legacy_emit
except Exception:
    pass


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


def _set_no_proxy():
    """设置 NO_PROXY=* 使 Python 的 requests/urllib3/curl_cffi 绕过系统代理。

    MITM 代理会通过 networksetup（macOS）/ 注册表（Windows）设置系统级代理，
    浏览器读取系统代理来走 MITM 拦截，但 Python 后端代码不需要走 MITM。
    设置 NO_PROXY=* 后，Python 的 HTTP 库不再自动使用系统代理，
    而用户在应用设置中配置的显式代理（通过 proxies= 参数传递）不受影响。
    """
    global _original_no_proxy
    _original_no_proxy = os.environ.get("NO_PROXY")
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    print("NO_PROXY=* set: Python HTTP clients will bypass system proxy.")


def _restore_no_proxy():
    """还原 MITM 启动前的 NO_PROXY 环境变量。"""
    global _original_no_proxy
    if _original_no_proxy is not None:
        os.environ["NO_PROXY"] = _original_no_proxy
        os.environ["no_proxy"] = _original_no_proxy
    else:
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)
    _original_no_proxy = None
    print("NO_PROXY restored to original.")


# ── Proxy Service Manager (代理服务单例管理器) ─────────────────────

class ProxyManager:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        self._running = False
        self.process = None
        self.port = 5202
        self.last_error = None
        self._monitor_thread = None

    @property
    def running(self):
        if self._running and self.process and self.process.poll() is None:
            return True
        return False

    @running.setter
    def running(self, val):
        self._running = bool(val)

    def _is_port_listening(self, port: int, timeout: float = 1.0) -> bool:
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except Exception:
            return False

    def _kill_port_owner(self, port: int):
        """强制清理霸占代理端口的任何遗留孤儿进程"""
        try:
            if sys.platform == "win32":
                flags = 0x08000000  # CREATE_NO_WINDOW
                out = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True, text=True, creationflags=flags)
                for line in out.strip().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and "LISTENING" in parts:
                        pid = parts[-1]
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True, creationflags=flags)
            else:
                out = subprocess.check_output(["lsof", "-ti", f":{port}"], text=True).strip()
                if out:
                    for pid in out.splitlines():
                        try:
                            os.kill(int(pid), 9)
                        except Exception:
                            pass
        except Exception:
            pass

    def start(self):
        if self.running and self._is_port_listening(self.port):
            return True

        self.last_error = None
        cleanup_mitmproxy_logging_handlers()

        try:
            # 0. 准备证书与目录
            ensure_ca_certificates()
            cert_ok = install_system_cert(CA_CERT_PATH)
            if not cert_ok:
                print("[WARNING] CA 证书可能未被系统信任，MITM 拦截可能失败。"
                      "请检查是否有其他 VPN/安全软件的证书冲突。")

            confdir = prepare_mitm_confdir()

            # 1. 检查端口是否有残留，若有则强制清理孤儿进程
            if self._is_port_listening(self.port):
                print(f"[Proxy] 检测到端口 {self.port} 处于监听状态，正在清理旧实例...")
                self._kill_port_owner(self.port)
                self.stop()
                time.sleep(0.5)

            # 2. 智能探测本机是否运行着 VPN / 科学上网代理客户端
            upstream_proxy = detect_upstream_proxy()
            if upstream_proxy:
                print(f"[Proxy] 🚀 自动探测到本机正在运行的 VPN 代理 ({upstream_proxy})，"
                      f"已启用链式上游转发 (Upstream Chaining)，实现 VPN 与同步助手无缝并存！")

            # 3. 以完全隔离的独立子进程启动 mitmproxy worker
            if getattr(sys, 'frozen', False):
                cmd = [
                    sys.executable,
                    "--proxy-worker",
                    "--port", str(self.port),
                    "--confdir", str(confdir),
                ]
            else:
                cmd = [
                    sys.executable,
                    "-m", "backend.proxy_worker",
                    "--port", str(self.port),
                    "--confdir", str(confdir),
                ]
            if upstream_proxy:
                cmd.extend(["--upstream", upstream_proxy])
            
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: 彻底隐藏子进程黑色控制台窗口

            self.process = subprocess.Popen(
                cmd,
                **popen_kwargs
            )

            # 3. 等待子进程启动就绪握手（最多 6 秒）
            is_ready = False
            start_wait = time.time()
            while time.time() - start_wait < 6.0:
                if self.process.poll() is not None:
                    # 子进程提前退出
                    out_text = self.process.stdout.read() if self.process.stdout else ""
                    raise RuntimeError(f"代理工作进程异常退出: {out_text.strip()}")
                
                # 读取一行输出验证 WORKER_READY
                import select
                if sys.platform != "win32":
                    rlist, _, _ = select.select([self.process.stdout], [], [], 0.2)
                    if rlist:
                        line = self.process.stdout.readline()
                        if "WORKER_READY" in line:
                            is_ready = True
                            break
                else:
                    if self._is_port_listening(self.port, timeout=0.2):
                        is_ready = True
                        break
                    time.sleep(0.2)

            if not is_ready:
                is_ready = self._is_port_listening(self.port, timeout=1.0)

            if not is_ready:
                self.stop()
                raise RuntimeError("mitmproxy 代理助手启动握手超时，未能成功就绪。")

            self._running = True

            # 4. 启动后台监控线程：收集日志并在子进程意外退出时自动回滚系统代理
            def _monitor_worker():
                try:
                    while self.process and self.process.poll() is None:
                        line = self.process.stdout.readline()
                        if not line:
                            break
                        line_s = line.strip()
                        if line_s and not line_s.startswith("WORKER_READY"):
                            print(f"[ProxyWorker] {line_s}", flush=True)
                except Exception:
                    pass
                if self._running:
                    print("[ProxyWorker] 代理进程退出，正在自动还原系统代理...", flush=True)
                    self._running = False
                    try:
                        set_system_proxy(False, port=self.port)
                    except Exception:
                        pass

            self._monitor_thread = threading.Thread(target=_monitor_worker, daemon=True)
            self._monitor_thread.start()

            # 5. 设置 NO_PROXY=* 使 Python 后端代码绕过系统代理（仅浏览器需走 MITM）
            _set_no_proxy()

            # 6. 开启系统代理（浏览器/微信客户端会走此代理）
            set_system_proxy(True, port=self.port)
            if not getattr(self, "_atexit_registered", False):
                import atexit
                atexit.register(self.stop)
                self._atexit_registered = True
            print(f"Channels MITM proxy started on 127.0.0.1:{self.port} and system proxy enabled.")
            return True
        except Exception as ex:
            self._running = False
            self.last_error = str(ex)
            # 兜底清理，确保系统网络立即可用
            self.stop()
            raise ex

    def stop(self):
        # 1. 首先无论如何优先还原系统代理，保障用户系统网络通畅
        try:
            set_system_proxy(False, port=self.port)
        except Exception as e:
            print(f"Error restoring system proxy: {e}")

        self._running = False

        # 2. 干净终结子进程，操作系统内核会在微秒级回收所有绑定的 socket
        if self.process:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=1.0)
            except Exception as e:
                print(f"Error terminating proxy worker process: {e}")
            finally:
                try:
                    if self.process.stdout:
                        self.process.stdout.close()
                except Exception:
                    pass
                self.process = None

        cleanup_mitmproxy_logging_handlers()

        # 3. 还原 NO_PROXY 环境变量
        _restore_no_proxy()

        print("Channels MITM proxy stopped and system proxy disabled.")
        return True
