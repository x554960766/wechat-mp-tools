import requests
import json
from backend.douyin import DouyinClient

client = DouyinClient()
try:
    # 7356262451000000000 随便一个假的，或者找个真的aweme_id
    # 随便找个真实的: 7324838641973054770 (from some test)
    # Let's just resolve a share url to get an ID
    res = client.get_video_detail("7324838641973054770")
    print("Detail keys:", list(res.keys()) if res else "None")
except Exception as e:
    print("Error:", e)
