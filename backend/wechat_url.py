"""Normalization helpers for WeChat short article URLs."""
import re
from urllib.parse import urlsplit, urlunsplit

_REVIEW_ID = re.compile(r"^MP_WXS_[0-9]+_(?P<short_id>[A-Za-z0-9_~-]{15,})$")
_SHORT_ID = re.compile(r"^[A-Za-z0-9_~-]{15,}$")


def short_id_from_review_id(review_id: str) -> str:
    """Extract the complete short ID after the WeRead account prefix."""
    if not isinstance(review_id, str):
        return ""
    match = _REVIEW_ID.fullmatch(review_id)
    if not match:
        return ""
    return match.group("short_id").replace("~", "_")


def normalize_wechat_short_url(url: str) -> str:
    """Restore underscores in WeChat short-link paths without touching other URL parts."""
    if not isinstance(url, str) or not url:
        return url

    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return url
        if parsed.hostname != "mp.weixin.qq.com" or parsed.username or parsed.password:
            return url
        if parsed.port not in {None, 80, 443}:
            return url
        if not parsed.path.startswith("/s/"):
            return url

        short_id = parsed.path[3:]
        if "/" in short_id or not _SHORT_ID.fullmatch(short_id):
            return url

        normalized_id = short_id.replace("~", "_")
        if normalized_id == short_id:
            return url
        return urlunsplit(parsed._replace(path="/s/" + normalized_id))
    except (TypeError, ValueError):
        return url
