"""归档暂存与稳定内容身份。"""
from __future__ import annotations

import csv
import io
import json
import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import config
from .parse import safe_title


def unique_dir(base: Path, name: str) -> Path:
    """同名文章不覆盖：第二次叫「标题-2」。"""
    d = base / name
    if not d.exists():
        return d
    i = 2
    while (base / f"{name}-{i}").exists():
        i += 1
    return base / f"{name}-{i}"


def to_png(raw: bytes) -> bytes | None:
    """真转码成 PNG；不是图片就返回 None。"""
    try:
        with Image.open(io.BytesIO(raw)) as im:
            from .media import check_pixels
            check_pixels(im)
            im.load()
            if im.mode in ("P", "LA", "RGBA"):
                out = im.convert("RGBA")
            else:
                out = im.convert("RGB")
            buf = io.BytesIO()
            out.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except ValueError as exc:
        if "图片像素数超过安全上限" in str(exc):
            raise
        return None
    except Exception:
        return None


def is_emoji(url: str, content: bytes) -> bool:
    gif = "wx_fmt=gif" in url or url.lower().endswith(".gif")
    return gif and len(content) < config.EMOJI_MAX_BYTES


def save_png(path: Path, raw: bytes) -> bool:
    png = to_png(raw)
    if png is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # 任务暂存文件不参与索引；原子替换可避免重启后看到半张图。
    fd, tmp = tempfile.mkstemp(prefix=".download-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(png)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    return True


def content_sha256(data: bytes) -> str:
    """以最终 PNG 字节计算稳定内容身份。"""
    return hashlib.sha256(data).hexdigest()


def article_id(url: str, *, biz: str = "", mid: str = "", idx: str = "") -> str:
    """身份优先使用微信文章三元组，缺失时回退规范文章 URL 键。"""
    from .parse import normalize_url, url_key

    key = "|".join((biz, mid, idx)) if biz and mid and idx else url_key(normalize_url(url))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def write_meta(directory: Path, meta: dict) -> None:
    meta = dict(meta)
    meta["archived_at"] = datetime.now().isoformat(timespec="seconds")
    (directory / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_report(job_dir: Path, rows: list[dict]) -> Path:
    path = job_dir / "report.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["title", "status", "cover", "content", "comments", "url"],
            extrasaction="ignore",
        )
        w.writeheader()
        w.writerows(rows)
    return path
