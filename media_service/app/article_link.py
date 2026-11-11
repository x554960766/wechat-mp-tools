"""把扫描到的图片挂回它所属的公众号文章。

wmp 的归档结构固定为 ``<账号>/<标题>/media/img_001.jpg``，文章元数据在
``<账号>/<标题>/metadata.json``。本模块只做一件事：给定图片的 relpath，
读出侧车元数据并构造 ``store.add_image`` 需要的 article dict。

几个约束：

- 侧车缺失、损坏、字段残缺一律返回 ``None``，图片照常入索引，只是没有文章
  归属；索引不能因为归档目录不规范就失败。
- 关联键优先用下载时写进 metadata 的 ``article_key``（wmp 侧算好，唯一真相源），
  老文章没有该字段时才用 ``url`` 现场归一，归一算法与
  ``backend/media_sync.normalize_article_url`` 保持一致。
- 每轮扫描内部按文章目录缓存，几百张图同属一篇时只读一次盘。
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from . import media


def article_dir_of(relpath: str) -> str | None:
    """``a/b/media/img_001.jpg`` → ``a/b``；不符合该结构返回 None。"""
    parts = str(relpath or "").replace("\\", "/").split("/")
    if len(parts) < 3 or parts[-2] != "media":
        return None
    return "/".join(parts[:-2])


def normalize_article_url(url: str) -> str:
    """与 wmp 侧同算法：``biz:mid:idx`` → ``sn:<sn>`` → ``url:<归一 URL>``。"""
    if not isinstance(url, str):
        return ""
    raw = url.strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    query = parse_qs(parts.query)
    if parts.fragment and "=" in parts.fragment:
        for key, values in parse_qs(parts.fragment).items():
            query.setdefault(key, values)

    def first(name: str) -> str:
        values = query.get(name) or []
        return values[0].strip() if values else ""

    biz, mid, idx = first("__biz"), first("mid"), first("idx")
    if biz and mid and idx:
        return f"{biz}:{mid}:{idx}"
    sn = first("sn")
    path = parts.path or ""
    if not sn and path.startswith("/s/"):
        sn = path[3:].split("/")[0]
    if sn:
        return f"sn:{sn}"
    kept = {(k, v[0]) for k, v in query.items() if k in ("__biz", "mid", "idx", "sn")}
    base = f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}".rstrip("/")
    if kept:
        base = f"{base}?" + "&".join(f"{k}={v}" for k, v in sorted(kept))
    return f"url:{base.lower()}"


def article_id_for_key(article_key: str) -> str:
    return hashlib.sha256(article_key.encode("utf-8")).hexdigest()


def parse_metadata(meta: dict) -> dict | None:
    """把 metadata.json 转成 store 需要的 article dict；不可用返回 None。"""
    if not isinstance(meta, dict):
        return None
    key = meta.get("article_key") or normalize_article_url(meta.get("url") or "")
    if not key:
        return None
    title = (meta.get("title") or "").strip() or "(未命名文章)"
    return {
        "id": article_id_for_key(key),
        "title": title,
        "url": meta.get("url") or "",
        "published_at": meta.get("publish_time") or None,
        "observed_at": meta.get("time") or None,
        "account": meta.get("source") or "",
    }


def load_article(root_id: str, relpath: str, cache: dict | None = None) -> dict | None:
    """读图片所属文章的侧车元数据。 ``cache`` 以文章目录为键，跨图片复用。"""
    ardir = article_dir_of(relpath)
    if ardir is None:
        return None
    if cache is not None and ardir in cache:
        return cache[ardir]

    article = None
    try:
        raw = media.read_bytes(root_id, f"{ardir}/metadata.json")
        article = parse_metadata(json.loads(raw.decode("utf-8")))
    except Exception:  # noqa: BLE001 - 侧车缺失/损坏/远端读失败都不影响索引
        article = None

    if cache is not None:
        cache[ardir] = article
    return article
