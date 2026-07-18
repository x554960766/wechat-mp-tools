import time
import re
import hashlib
import urllib.parse
import requests

# Cache for WbiImg keys
_wbi_cache = {
    "img_key": "",
    "sub_key": "",
    "expire_time": 0
}

def _get_key_from_url(url: str) -> str:
    return url.split("/")[-1].split(".")[0]

def _get_mixin_key(string: str) -> str:
    char_indices = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5,
        49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55,
        40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57,
        62, 11, 36, 20, 34, 44, 52,
    ]
    return "".join([string[idx] for idx in char_indices[:32]])

def get_wbi_keys(cookie: str = "", proxies: dict = None) -> tuple[str, str]:
    """获取 B 站当前的 img_key 和 sub_key"""
    global _wbi_cache
    now = time.time()
    
    # 缓存 10 分钟
    if _wbi_cache["img_key"] and _wbi_cache["sub_key"] and _wbi_cache["expire_time"] > now:
        return _wbi_cache["img_key"], _wbi_cache["sub_key"]
        
    url = "https://api.bilibili.com/x/web-interface/nav"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com"
    }
    if cookie:
        headers["Cookie"] = cookie
        
    try:
        resp = requests.get(url, headers=headers, proxies=proxies, timeout=10)
        data = resp.json()
        if "data" in data and "wbi_img" in data["data"]:
            wbi_img = data["data"].get("wbi_img", {})
            img_url = wbi_img.get("img_url")
            sub_url = wbi_img.get("sub_url")
            if img_url and sub_url:
                img_key = _get_key_from_url(img_url)
                sub_key = _get_key_from_url(sub_url)
                _wbi_cache["img_key"] = img_key
                _wbi_cache["sub_key"] = sub_key
                _wbi_cache["expire_time"] = now + 600  # 10 minutes cache
                return img_key, sub_key
    except Exception as e:
        print(f"DEBUG: get_wbi_keys request failed: {e}")
        pass
        
    # 兜底返回默认密钥（如果请求失败）
    return "72505f0e133045cfae90d79cc5cf5187", "cc1d6c81062f483c92dfd9d5926ec0e0"

def sign_wbi(params: dict, cookie: str = "", proxies: dict = None) -> dict:
    """对请求参数进行 WBI 签名，并返回包含 wts 和 w_rid 的新参数字典"""
    img_key, sub_key = get_wbi_keys(cookie, proxies)
    mixin_key = _get_mixin_key(img_key + sub_key)
    
    # 浅拷贝参数并添加当前时间戳
    signed_params = params.copy()
    signed_params["wts"] = int(time.time())
    
    # 排除特殊字符的值，按字典序排序
    illegal_pattern = re.compile(r"[!'\(\)*]")
    cleaned_params = {}
    for k, v in signed_params.items():
        v_str = str(v)
        cleaned_v = illegal_pattern.sub("", v_str)
        cleaned_params[str(k)] = cleaned_v
        
    # 排序拼接
    sorted_keys = sorted(cleaned_params.keys())
    query_parts = []
    for k in sorted_keys:
        # urlencode k and v
        encoded_k = urllib.parse.quote(k, safe="")
        encoded_v = urllib.parse.quote(cleaned_params[k], safe="")
        query_parts.append(f"{encoded_k}={encoded_v}")
        
    query_string = "&".join(query_parts)
    
    # 计算 MD5 w_rid
    w_rid = hashlib.md5((query_string + mixin_key).encode("utf-8")).hexdigest()
    
    # 写入最终返回的参数
    signed_params["w_rid"] = w_rid
    return signed_params
