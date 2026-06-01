import requests
import json
import random
import string
import urllib.parse
from backend.douyin import DouyinClient
from backend.sign import sign

client = DouyinClient()

def _get_ms_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=107))

def _generate_s_v_web_id():
    charset = string.ascii_lowercase + string.digits
    random_str = ''.join(random.choices(charset, k=16))
    return f"verify_0{random_str}"

base_params = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "190600",
    "version_name": "19.6.0",
    "cookie_enabled": "true",
    "screen_width": "1680",
    "screen_height": "1050",
    "browser_language": "zh-CN",
    "browser_platform": "MacIntel",
    "browser_name": "Edge",
    "browser_version": "145.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "145.0.0.0",
    "os_name": "Mac OS",
    "os_version": "10.15.7",
    "cpu_core_num": "8",
    "device_memory": "8",
    "platform": "PC",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "50",
    "msToken": _get_ms_token(),
    "verifyFp": _generate_s_v_web_id()
}

params = {
    "keyword": "coder",
    "search_channel": "aweme_user_web",
    "search_source": "normal_search",
    "query_correct_type": "1",
    "is_filter_search": "0",
    "from_group_id": "",
    "offset": "0",
    "count": "10",
    "pc_search_top_1_params": '{"enable_ai_search_top_1":1}'
}
all_params = params.copy()
all_params.update(base_params)

qs = urllib.parse.urlencode(all_params, safe='', quote_via=urllib.parse.quote)

headers = client.session.headers.copy()
headers["Referer"] = "https://www.douyin.com/jingxuan/search/coder?type=user"
url = f"https://www.douyin.com/aweme/v1/web/discover/search/?{qs}"

resp = requests.get(url, headers=headers)
print("Status:", resp.status_code)
print("Text len:", len(resp.text))
print("Text:", resp.text[:200])

