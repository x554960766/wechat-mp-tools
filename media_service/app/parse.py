"""链接抽取与命名清洗。不依赖网络，可独立测试。"""
from __future__ import annotations

import re
import io
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config

# 公众号文章链接：/s?__biz=...&mid=...&idx=...&sn=...
URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s[/?][^\s\"'`<>()\[\]，。；、]*")
# 分享文本里常见的裸参数（有的客户端只给 __biz/mid/idx/sn 不带域名），这里不做猜测

TRAILING = ".,;:!?)]}，。；、！？）】》"


def normalize_url(raw: str) -> str:
    u = raw.strip().strip(TRAILING)
    u = u.replace("&amp;", "&")
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    return u


def extract_urls(text: str) -> list[str]:
    """从任意文本（粘贴、txt、csv、微信分享文案）里抽出公众号文章链接，去重保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in URL_RE.finditer(text or ""):
        u = normalize_url(m.group(0))
        key = url_key(u)
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


def url_key(url: str) -> str:
    """用 __biz/mid/idx 定位同一篇文章（同一篇的 sn 可能不同、短链可能不同）。"""
    try:
        q = parse_qs(urlparse(url).query)
    except ValueError:
        return url
    parts = [q.get(k, [""])[0] for k in ("__biz", "mid", "idx")]
    if parts[0] and parts[1]:
        return "|".join(parts)
    return url


def uniq_images(urls: list[str]) -> list[str]:
    """图片 URL 去重（保序）。

    微信页面里同一张图常同时以 http:// 和 https:// 出现，协议不同但资源相同；
    不归一就会把 1 张图算成 2 张。
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls or []:
        u = normalize_url(raw)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def safe_title(s: str, n: int = 80) -> str:
    """文件名安全化：去掉路径分隔符与保留字符。"""
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:n] or "untitled")


def extract_upload(filename: str, data: bytes) -> list[str]:
    """从 txt/csv 或 ZIP 中受限读取链接文本；不提取归档路径或执行内容。"""
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError("上传文件超过 30 MiB")
    suffix = Path(filename).suffix.lower()
    if suffix in (".txt", ".csv"):
        texts = [data.decode("utf-8-sig", errors="replace")]
    elif suffix == ".zip":
        texts = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > config.MAX_ZIP_ENTRIES:
                    raise ValueError("ZIP 条目超过上限")
                total = 0
                for info in infos:
                    if info.is_dir():
                        continue
                    if Path(info.filename).suffix.lower() not in (".txt", ".csv"):
                        continue
                    if info.file_size > config.MAX_ZIP_TEXT_BYTES - total:
                        raise ValueError("ZIP 文本累计解压超过上限")
                    total += info.file_size
                    with archive.open(info) as stream:
                        buf = stream.read(config.MAX_ZIP_TEXT_BYTES - total + info.file_size + 1)
                    if len(buf) > info.file_size:
                        raise ValueError("ZIP 条目实际内容超过声明大小")
                    texts.append(buf.decode("utf-8-sig", errors="replace"))
        except zipfile.BadZipFile as exc:
            raise ValueError("ZIP 文件无效") from exc
    else:
        raise ValueError("仅支持 txt、csv、zip")
    urls: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for url in extract_urls(text):
            key = url_key(url)
            if key not in seen:
                seen.add(key)
                urls.append(url)
                if len(urls) > config.MAX_URLS_PER_JOB:
                    raise ValueError("单任务链接超过 500 条")
    return urls
