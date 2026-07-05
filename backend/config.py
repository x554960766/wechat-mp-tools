"""
全局配置管理模块
管理应用设置、数据目录路径、下载参数等
"""

import sys
import json
import time
import random
import string
import threading
from pathlib import Path

from backend.runtime import app_dir

# ── 版本号 ────────────────────────────────────────────────
APP_VERSION = "1.5.2"

# ── 路径配置 ──────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    # 打包后数据保存在 exe 或 .app 旁边，避免写入应用包内部。
    SCRIPT_DIR = app_dir()
else:
    SCRIPT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SCRIPT_DIR / "data"
OUTPUT_DIR = DATA_DIR / "articles_full"
CONFIG_FILE = DATA_DIR / "wechat_mp_config.json"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
PROXY_CONFIG_FILE = DATA_DIR / "proxy_config.json"
ACCOUNT_POOL_FILE = DATA_DIR / "account_pool.json"
APP_SETTINGS_FILE = DATA_DIR / "app_settings.json"
DOWNLOAD_HISTORY_FILE = DATA_DIR / "download_history.json"

# ── 微信 API 配置 ─────────────────────────────────────────
BASE_URL = "https://mp.weixin.qq.com"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
    "Accept": "application/json, text/plain, */*",
}

# ── 默认应用设置 ──────────────────────────────────────────
DEFAULT_SETTINGS = {
    "download_dir": str(OUTPUT_DIR),
    "page_size": 10,
    "max_articles": 50,
    "max_retries": 3,
    "request_delay": 0.8,
    "concurrent_downloads": 1,
    "auto_save_images": True,
    "auto_save_videos": True,
    "device_id": "公众号_caiji100",
    "rss_start_hour": 0,
    "rss_start_minute": 0,
    "rss_end_hour": 24,
    "rss_end_minute": 0,
    "rss_upload_enabled": False,
    "rss_upload_url": "",
}


def ensure_dirs():
    """确保必要的目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_json(filepath: Path, default=None):
    """安全地加载 JSON 文件"""
    if default is None:
        default = {}
    try:
        if filepath.exists():
            return json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        pass
    return default


def save_json(filepath: Path, data):
    """安全地保存 JSON 文件"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_settings() -> dict:
    """获取应用设置"""
    settings = load_json(APP_SETTINGS_FILE, DEFAULT_SETTINGS.copy())
    # 合并默认值（防止缺少新增的配置项）
    for key, val in DEFAULT_SETTINGS.items():
        if key not in settings:
            settings[key] = val
    return settings


def save_settings(settings: dict):
    """保存应用设置"""
    save_json(APP_SETTINGS_FILE, settings)


# ── 代理管理器全局状态管理 ────────────────────────────────────
_proxy_states = {}                  # 代理节点状态, key: raw_host, val: {"failures": 0, "last_used": 0.0, "cooldown_until": 0.0}
_resolved_to_template_map = {}      # 动态子域名映射回模板, key: resolved_url, val: raw_host
_proxy_lock = threading.Lock()


def get_random_subdomain() -> str:
    """生成 8 位随机字符作为 Cloudflare 等通配符代理的子域名"""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def get_proxy_config() -> dict:
    """获取代理配置 (如果为空则自动添加用户提供的 6 个优质代理)"""
    default = {
        "enabled": False,
        "type": "http",        # http / https / socks5
        "host": "",
        "port": "",
        "username": "",
        "password": "",
        "proxy_list": [],      # 代理池列表
        "rotation": False,     # 是否启用轮换
    }
    
    # 默认种子代理池 (用户提供的 6 个 CDN 代理)
    default_proxies = [
        {"type": "http", "host": "*.worker-proxy.asia", "port": "443", "username": "", "password": "", "label": "Worker Proxy Asia"},
        {"type": "http", "host": "*.net-proxy.asia", "port": "443", "username": "", "password": "", "label": "Net Proxy Asia"},
        {"type": "http", "host": "*.1235566.space", "port": "443", "username": "", "password": "", "label": "Space Proxy"},
        {"type": "http", "host": "*.worker-proxy.shop", "port": "443", "username": "", "password": "", "label": "Worker Proxy Shop"},
        {"type": "http", "host": "*.worker-proxys.cyou", "port": "443", "username": "", "password": "", "label": "Worker Proxys Cyou"},
        {"type": "http", "host": "*.worker-proxy.cyou", "port": "443", "username": "", "password": "", "label": "Worker Proxy Cyou"},
    ]
    
    config = load_json(PROXY_CONFIG_FILE, default)
    for key, val in default.items():
        if key not in config:
            config[key] = val
            
    # 如果代理池列表为空，自动写入用户代理节点并开启轮换
    if not config.get("proxy_list"):
        config["proxy_list"] = default_proxies
        config["rotation"] = True
        config["enabled"] = False  # 默认关闭代理功能，避免没有正确配置前导致网络请求失败
        save_proxy_config(config)
        
    return config


def save_proxy_config(config: dict):
    """保存代理配置"""
    save_json(PROXY_CONFIG_FILE, config)


def get_proxy_url(config: dict = None) -> str | None:
    """根据代理配置生成代理 URL (支持智能多代理池轮换及自动负载均衡与故障转移)"""
    if config is None:
        config = get_proxy_config()
    if not config.get("enabled"):
        return None

    current_time = time.time()

    # 1. 启用代理池轮换模式
    if config.get("rotation") and config.get("proxy_list"):
        pool = config["proxy_list"]
        valid_nodes = [node for node in pool if node.get("host")]
        
        if not valid_nodes:
            return None

        with _proxy_lock:
            active_nodes = []
            cooldown_nodes = []
            
            for node in valid_nodes:
                host = node["host"]
                state = _proxy_states.get(host)
                if not state:
                    state = {"failures": 0, "last_used": 0.0, "cooldown_until": 0.0}
                    _proxy_states[host] = state
                
                # 检查连续失败 5 次进入 10 分钟冷却期 (600秒)
                if state["cooldown_until"] > current_time:
                    cooldown_nodes.append((node, state))
                else:
                    active_nodes.append((node, state))
            
            if active_nodes:
                # 自动轮询选择最优节点：按最少失败次数 + 最久未使用的策略选择
                active_nodes.sort(key=lambda x: (x[1]["failures"], x[1]["last_used"]))
                selected_node, state = active_nodes[0]
            else:
                # 故障转移：若所有节点都在冷却中，则选择最快结束冷却的那个
                cooldown_nodes.sort(key=lambda x: x[1]["cooldown_until"])
                selected_node, state = cooldown_nodes[0]
                
            state["last_used"] = current_time
            
        proxy_type = selected_node.get("type", "http")
        raw_host = selected_node["host"]
        
        # 负载均衡：节点中带通配符 * 时，自动替换为随机子域名以提高并发与防止单 IP 被微信限流
        if "*" in raw_host:
            actual_host = raw_host.replace("*", f"node-{get_random_subdomain()}")
        else:
            actual_host = raw_host
            
        port = selected_node.get("port", "")
        username = selected_node.get("username", "")
        password = selected_node.get("password", "")
        
        auth = f"{username}:{password}@" if username and password else ""
        port_str = f":{port}" if port else ""
        
        if proxy_type == "socks5":
            actual_url = f"socks5://{auth}{actual_host}{port_str}"
        elif port == "443" or proxy_type == "https":
            actual_url = f"https://{auth}{actual_host}{port_str}"
        else:
            actual_url = f"http://{auth}{actual_host}{port_str}"
            
        with _proxy_lock:
            _resolved_to_template_map[actual_url] = raw_host
            
        return actual_url

    # 2. 未启用轮换，使用单个代理配置
    host = config.get("host")
    if not host:
        return None
        
    proxy_type = config.get("type", "http")
    
    if "*" in host:
        actual_host = host.replace("*", f"node-{get_random_subdomain()}")
    else:
        actual_host = host
        
    port = config.get("port", "")
    username = config.get("username", "")
    password = config.get("password", "")
    
    auth = f"{username}:{password}@" if username and password else ""
    port_str = f":{port}" if port else ""
    
    if proxy_type == "socks5":
        actual_url = f"socks5://{auth}{actual_host}{port_str}"
    elif port == "443" or proxy_type == "https":
        actual_url = f"https://{auth}{actual_host}{port_str}"
    else:
        actual_url = f"http://{auth}{actual_host}{port_str}"
        
    with _proxy_lock:
        _resolved_to_template_map[actual_url] = host
        
    return actual_url


def get_proxies_dict(config: dict = None) -> dict | None:
    """获取 requests 库使用的 proxies 字典"""
    url = get_proxy_url(config)
    if not url:
        return None
    return {"http": url, "https": url}


def report_proxy_status(proxy_url: str, success: bool):
    """反馈代理的使用状态，用于智能调度与 10 分钟冷却期故障转移"""
    if not proxy_url:
        return
    current_time = time.time()
    with _proxy_lock:
        raw_host = _resolved_to_template_map.get(proxy_url)
        if not raw_host:
            # 备用从 URL 直接提取并重写为通配符
            try:
                from urllib.parse import urlparse
                raw_host = urlparse(proxy_url).hostname or ""
                for domain in ["worker-proxy.asia", "net-proxy.asia", "1235566.space", "worker-proxy.shop", "worker-proxys.cyou", "worker-proxy.cyou"]:
                    if domain in raw_host:
                        raw_host = f"*.{domain}"
                        break
            except Exception:
                raw_host = proxy_url
                
        state = _proxy_states.get(raw_host)
        if not state:
            state = {"failures": 0, "last_used": current_time, "cooldown_until": 0.0}
            _proxy_states[raw_host] = state
            
        state["last_used"] = current_time
        
        if success:
            state["failures"] = 0
            state["cooldown_until"] = 0.0
        else:
            state["failures"] += 1
            if state["failures"] >= 5:
                state["cooldown_until"] = current_time + 600  # 连续失败 5 次，进入 10 分钟冷却期
