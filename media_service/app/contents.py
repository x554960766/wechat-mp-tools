"""把各平台下载目录归一成可检索的内容条目。

同一件事在六个平台上的落盘方式并不对称：

- 公众号是一篇文章一个目录，``metadata.json`` + ``content.txt`` + ``media/`` 齐全；
- 视频号、抖音、快手的视频基本只有一个 mp4，标题写在文件名里；
- 抖音/快手的图集是一个图片目录，B 站是一个 ``<标题>_BV号`` 目录；
- 小红书一个笔记一个目录，``文案.txt`` 里带着标题、作者、链接和正文。

本模块只做两件事：按各平台的文件形状把文件归组成「内容条目」，再把标题与
正文切成检索词。归组过程只读，不写、不改、不移动任何原件。

检索词不使用 FTS5 自带的 ``trigram`` 分词器：trigram 需要三个字符才命中，
中文最常用的两字词（“壁纸”“头像”）根本搜不到。这里在应用层切中文单字与
双字滑窗，ASCII 按整词小写，再交给 FTS5 的 ``unicode61`` 建倒排。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, media

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".flv", ".ts"}
PLATFORMS = {
    "wechat": "公众号",
    "channels": "视频号",
    "douyin": "抖音",
    "kuaishou": "快手",
    "xhs": "小红书",
    "bilibili": "B站",
}
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_SEGMENT = re.compile(r"[0-9A-Za-z]+|[\u4e00-\u9fff]+")
_AWEME = re.compile(r"^(\d{10,})_(.+)$")
_BVID = re.compile(r"_BV[0-9A-Za-z]+$")
_READ_LIMIT = 512 * 1024


def tokenize(text: str) -> list[str]:
    """把文本切成 FTS5 可索引的词：中文单字 + 双字滑窗，ASCII 整词小写。"""
    out: list[str] = []
    for match in _SEGMENT.finditer(text or ""):
        seg = match.group(0)
        if seg[0].isascii():
            out.append(seg.lower())
            continue
        out.extend(seg)
        out.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return out


def content_id_for(platform: str, root: str, relpath: str) -> str:
    return hashlib.sha256(f"{platform}|{root}|{relpath}".encode("utf-8")).hexdigest()


def root_platform(root_id: str, path: str) -> str:
    """按根目录的 id/路径猜平台；公众号归档根里的 channels 子目录另行判定。"""
    text = f"{root_id} {path}".lower()
    if "douyin" in text:
        return "douyin"
    if "kuaishou" in text:
        return "kuaishou"
    if "bilibili" in text:
        return "bilibili"
    if "xhs" in text or "xiaohongshu" in text:
        return "xhs"
    return "wechat"


def _posix_dir(relpath: str) -> str:
    return relpath.rsplit("/", 1)[0] if "/" in relpath else ""


def _name(relpath: str) -> str:
    return relpath.rsplit("/", 1)[-1]


def _parts(relpath: str) -> list[str]:
    return relpath.split("/")


def _read_bytes(root_id: str, relpath: str) -> bytes | None:
    try:
        return media.read_bytes(root_id, relpath)[: _READ_LIMIT]
    except Exception:  # noqa: BLE001 - 侧车缺失/远端读失败不阻断其它条目
        return None


def _read_text(root_id: str, relpath: str) -> str | None:
    data = _read_bytes(root_id, relpath)
    if data is None:
        return None
    return data.decode("utf-8", "ignore")


def _read_json(root_id: str, relpath: str) -> dict | None:
    text = _read_text(root_id, relpath)
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _local_datetime(value) -> str | None:
    """把 epoch 秒/毫秒或常见日期字符串统一成 ``YYYY-MM-DDTHH:MM:SS``。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        if ts > 1e12:
            ts /= 1000
        if ts <= 0:
            return None
        try:
            return datetime.fromtimestamp(ts, _LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None


def _snowflake_datetime(identifier: str) -> str | None:
    """抖音/快手的 19 位 ID 是雪花号，右移 22 位就是毫秒时间戳。"""
    try:
        value = int(identifier)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    ts = (value >> 22) / 1000
    try:
        moment = datetime.fromtimestamp(ts, _LOCAL_TZ)
    except (OverflowError, OSError, ValueError):
        return None
    now = datetime.now(_LOCAL_TZ)
    if moment < datetime(2016, 1, 1, tzinfo=_LOCAL_TZ) or moment > now + timedelta(days=30):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _digest_of(body: str | None, fallback: str | None = None) -> str | None:
    text = (body or fallback or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    return text[:200]


def _item(*, root: str, relpath: str, platform: str, kind: str, title: str,
          account: str | None = None, url: str | None = None,
          published_at: str | None = None, digest: str | None = None,
          body: str | None = None, cover_relpath: str | None = None,
          media_count: int = 0, observed_at: str | None = None,
          is_dir: bool = True) -> dict:
    return {
        "content_id": content_id_for(platform, root, relpath),
        "root": root,
        "relpath": relpath,
        "platform": platform,
        "kind": kind,
        "title": (title or "").strip() or "(未命名)",
        "account": (account or "").strip() or None,
        "url": url or None,
        "published_at": published_at,
        "digest": digest,
        "body": (body or "").strip() or None,
        "cover_relpath": cover_relpath,
        "media_count": int(media_count or 0),
        "observed_at": observed_at,
        "is_dir": is_dir,
    }


def _image_files(files_by_dir: dict[str, list[media.MediaEntry]], directory: str) -> list[media.MediaEntry]:
    prefix = directory + "/" if directory else ""
    out: list[media.MediaEntry] = []
    for path, entries in files_by_dir.items():
        if path == directory or path.startswith(prefix):
            out.extend(e for e in entries if Path(e.relpath).suffix.lower() in media.IMAGE_EXTS)
    return sorted(out, key=lambda e: e.relpath)


def discover(root_id: str, files: list[media.MediaEntry]) -> list[dict]:
    """把一次完整列举出的文件归组成内容条目。"""
    root = config.get_root(root_id)
    platform = root_platform(root_id, root["path"])
    by_dir: dict[str, list[media.MediaEntry]] = {}
    for entry in files:
        by_dir.setdefault(_posix_dir(entry.relpath), []).append(entry)

    items: dict[str, dict] = {}
    if platform == "wechat":
        for entry in files:
            if _name(entry.relpath) == "metadata.json":
                item = _wechat_item(root_id, entry, by_dir)
                if item:
                    items[item["relpath"]] = item
    if platform in ("wechat", "channels"):
        for entry in files:
            if entry.relpath.endswith(tuple(VIDEO_EXTS)) and "channels" in _parts(entry.relpath)[:-1]:
                item = _channel_item(root_id, entry)
                if item:
                    items[item["relpath"]] = item
    if platform in ("douyin", "kuaishou"):
        for entry in files:
            item = _short_video_item(root_id, platform, entry, by_dir)
            if item:
                items[item["relpath"]] = item
    if platform == "bilibili":
        for entry in files:
            item = _bilibili_item(root_id, entry, by_dir)
            if item:
                items[item["relpath"]] = item
    if platform == "xhs":
        for entry in files:
            if _name(entry.relpath) == "文案.txt":
                item = _xhs_item(root_id, entry, by_dir)
                if item:
                    items[item["relpath"]] = item
    return sorted(items.values(), key=lambda i: i["relpath"])


def _wechat_item(root_id, metadata: media.MediaEntry, by_dir) -> dict | None:
    directory = _posix_dir(metadata.relpath)
    if len(_parts(directory)) < 2:
        return None
    meta = _read_json(root_id, metadata.relpath)
    if not meta:
        return None
    body = _read_text(root_id, f"{directory}/content.txt")
    md_entry = next(
        (e for e in by_dir.get(directory, []) if e.relpath.endswith(".md") and not _name(e.relpath).startswith(".")),
        None,
    )
    if not body and md_entry:
        body = _read_text(root_id, md_entry.relpath)
    images = _image_files(by_dir, f"{directory}/media")
    if not images:
        images = _image_files(by_dir, directory)
    observed = max([metadata.mtime] + ([md_entry.mtime] if md_entry else []))
    title = meta.get("title") or directory.rsplit("/", 1)[-1]
    account = meta.get("source") or _parts(directory)[0]
    return _item(
        root=root_id,
        relpath=directory,
        platform="wechat",
        kind="article",
        title=str(title),
        account=str(account) if account else None,
        url=meta.get("url") or None,
        published_at=_local_datetime(meta.get("publish_time") or meta.get("time")),
        digest=_digest_of(meta.get("digest"), body),
        body=body,
        cover_relpath=images[0].relpath if images else None,
        media_count=len(images),
        observed_at=str(observed),
    )


def _channel_item(root_id, entry: media.MediaEntry) -> dict | None:
    parts = _parts(entry.relpath)
    if len(parts) < 2:
        return None
    title = Path(_name(entry.relpath)).stem
    if not title:
        return None
    return _item(
        root=root_id,
        relpath=entry.relpath,
        platform="channels",
        kind="video",
        title=title,
        account="视频号",
        published_at=None,
        digest=None,
        cover_relpath=None,
        media_count=1,
        observed_at=str(entry.mtime),
        is_dir=False,
    )


def _short_video_item(root_id, platform, entry, by_dir) -> dict | None:
    """抖音/快手：``<作者>/<数字ID>_<标题>.mp4`` 或 ``<作者>/<数字ID>_<标题>/图.jpg``。"""
    parts = _parts(entry.relpath)
    if len(parts) < 2:
        return None
    account = parts[-2]
    # 目录型图集：用目录自身作为条目路径，避免每张图各成一条。
    candidate_dir = _posix_dir(entry.relpath)
    dir_match = _AWEME.match(_name(candidate_dir))
    if dir_match and len(parts) >= 3:
        identifier, title = dir_match.group(1), dir_match.group(2)
        # 图集的作者是目录再上一层，不能用 parts[-2]（那是 "<id>_<标题>" 目录本身）。
        account = candidate_dir.rsplit("/", 2)[-2] if "/" in candidate_dir else account
        images = _image_files(by_dir, candidate_dir)
        return _item(
            root=root_id,
            relpath=candidate_dir,
            platform=platform,
            kind="gallery",
            title=title,
            account=account,
            published_at=_snowflake_datetime(identifier),
            digest=None,
            body=None,
            cover_relpath=images[0].relpath if images else None,
            media_count=len(images),
            observed_at=str(max(e.mtime for e in by_dir.get(candidate_dir, [entry]))),
        )
    name = _name(entry.relpath)
    if Path(name).suffix.lower() not in VIDEO_EXTS:
        return None
    match = _AWEME.match(Path(name).stem)
    if not match:
        return None
    identifier, title = match.group(1), match.group(2)
    return _item(
        root=root_id,
        relpath=entry.relpath,
        platform=platform,
        kind="video",
        title=title,
        account=account,
        published_at=_snowflake_datetime(identifier),
        digest=None,
        body=None,
        cover_relpath=None,
        media_count=1,
        observed_at=str(entry.mtime),
        is_dir=False,
    )


def _bilibili_item(root_id, entry, by_dir) -> dict | None:
    parts = _parts(entry.relpath)
    for index in range(len(parts) - 1, 0, -1):
        if not _BVID.search(parts[index]):
            continue
        directory = "/".join(parts[: index + 1])
        name = parts[index]
        title = _BVID.sub("", name)
        stem = Path(_name(entry.relpath)).stem
        poster = next(
            (e for e in by_dir.get(directory, []) if _name(e.relpath) == f"{stem}-poster.jpg"),
            None,
        )
        if poster is None:
            images = _image_files(by_dir, directory)
            poster = images[0] if images else None
        videos = [e for e in by_dir.get(directory, []) if Path(e.relpath).suffix.lower() in VIDEO_EXTS]
        return _item(
            root=root_id,
            relpath=directory,
            platform="bilibili",
            kind="video",
            title=title,
            account=parts[index - 1] if index >= 1 else None,
            published_at=None,
            digest=None,
            body=None,
            cover_relpath=poster.relpath if poster else None,
            media_count=max(len(videos), 1),
            observed_at=str(max(e.mtime for e in by_dir.get(directory, [entry]))),
        )
    return None


def _parse_xhs(text: str) -> dict[str, str]:
    head, _, rest = text.partition("\n\n")
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    fields["_body"] = rest.strip()
    return fields


def _xhs_item(root_id, text_entry: media.MediaEntry, by_dir) -> dict | None:
    directory = _posix_dir(text_entry.relpath)
    parts = _parts(directory)
    if len(parts) < 2:
        return None
    text = _read_text(root_id, text_entry.relpath)
    if not text:
        return None
    fields = _parse_xhs(text)
    images = _image_files(by_dir, directory)
    title = fields.get("标题") or directory.rsplit("/", 1)[-1]
    return _item(
        root=root_id,
        relpath=directory,
        platform="xhs",
        kind="note",
        title=title,
        account=fields.get("作者") or parts[-2],
        url=fields.get("链接") or None,
        published_at=_local_datetime(fields.get("发布时间")),
        digest=_digest_of(fields.get("_body")),
        body=fields.get("_body") or None,
        cover_relpath=images[0].relpath if images else None,
        media_count=len(images),
        observed_at=str(max(e.mtime for e in by_dir.get(directory, [text_entry]))),
    )


def image_prefix(item: dict) -> str | None:
    """条目内图片的 relpath 前缀；文件型条目（单个视频）没有配图。"""
    return item["relpath"] + "/" if item.get("is_dir", True) else None
