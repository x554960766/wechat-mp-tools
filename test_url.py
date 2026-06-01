import re
import requests

def resolve(url):
    target = url.strip()
    resp = requests.get(target, allow_redirects=True, timeout=10, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
    return resp.url

def extract(url):
    url = url.strip()
    print("Extracting from:", url)
    if re.match(r"^\d+$", url):
        return url
    patterns = [
        r"video/(\d+)",
        r"note/(\d+)",
        r"aweme_id=(\d+)",
        r"modal_id=(\d+)",
        r"/(\d{18,21})"
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return ""

u = resolve("https://v.douyin.com/ieL7A14o/")
print("Resolved:", u)
print("Extracted:", extract(u))
