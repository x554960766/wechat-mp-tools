"""历史文章图片回填。

早期版本或关掉 ``auto_save_images`` 时，``<标题>.html`` 里的正文图仍然是
公众号 CDN 直链。公众号的图片地址带时效签名，过期就成了死链，这类文章在
素材检索里等于没有配图。本命令把残留直链补下到 ``<文章目录>/media/``，
改写 ``<标题>.html`` 的引用，最后通知媒体服务增量扫描。

设计口径：

- 只补图，不动 ``_raw.html``、不重写正文、不改目录结构；
- 幂等：已经指向 ``media/`` 的图不再处理，重复执行不会重复下载；
- 失败不阻塞：单图失败只计数，继续下一篇；
- 默认只做本地文件操作，``--dry-run`` 只报告不落盘。

用法::

    media_service/.venv/bin/python -m media_service.backfill --limit 200
    media_service/.venv/bin/python -m media_service.backfill --account 某账号 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

from media_service.app import article_link

DEFAULT_REFERER = "https://mp.weixin.qq.com/"
MIN_SIDE = 80
TIMEOUT = 15.0
RETRIES = 2

_IMG_ATTR = re.compile(r'(?:data-src|src)="([^"]*(?:mmbiz|qpic)[^"]*)"', re.I)
_IMG_FILE = re.compile(r"^img_(\d{3,})\.[A-Za-z0-9]+$")


def _html_to_url(value: str) -> str:
    return value.replace("&amp;", "&").strip()


def _existing_indices(media_dir: Path) -> int:
    highest = 0
    if media_dir.is_dir():
        for child in media_dir.iterdir():
            match = _IMG_FILE.match(child.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest


def _suffix_from_bytes(head: bytes) -> str:
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"GIF8"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heic"
    return "jpg"


def _too_small(path: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001
        return False
    return min(width, height) < MIN_SIDE


def download_one(url: str, media_dir: Path, index: int) -> str | None:
    """把单张图落进 media_dir；返回文件名，失败返回 None。"""
    headers = {"Referer": DEFAULT_REFERER, "User-Agent": "Mozilla/5.0"}
    for attempt in range(RETRIES + 1):
        part = media_dir / f".img_{index:03d}.part"
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
            with requests.get(url, headers=headers, timeout=TIMEOUT, stream=True) as resp:
                resp.raise_for_status()
                head = b""
                with open(part, "wb") as handle:
                    for chunk in resp.iter_content(65536):
                        if len(head) < 16:
                            head += chunk[: 16 - len(head)]
                        handle.write(chunk)
            if not part.stat().st_size:
                raise RuntimeError("空响应")
            target = media_dir / f"img_{index:03d}.{_suffix_from_bytes(head)}"
            part.replace(target)
            return target.name
        except Exception:  # noqa: BLE001
            try:
                part.unlink()
            except OSError:
                pass
            if attempt < RETRIES:
                time.sleep(0.5 * (attempt + 1))
    return None


def _article_dirs(root: Path, account: str | None) -> list[Path]:
    if not root.is_dir():
        return []
    bases = [root / account] if account else sorted(p for p in root.iterdir() if p.is_dir())
    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "metadata.json").is_file():
                found.append(child)
    return found


def _article_html(art_dir: Path, meta: dict) -> Path | None:
    title = (meta.get("title") or "").strip()
    if title and (art_dir / f"{title}.html").is_file():
        return art_dir / f"{title}.html"
    candidates = [
        p
        for p in art_dir.glob("*.html")
        if not p.name.endswith("_raw.html")
    ]
    return candidates[0] if len(candidates) == 1 else None


def _media_count(media_dir: Path) -> int:
    if not media_dir.is_dir():
        return 0
    return sum(1 for p in media_dir.iterdir() if p.is_file() and not p.name.startswith("."))


def backfill_article(art_dir: Path, *, dry_run: bool) -> dict:
    meta_path = art_dir / "metadata.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"dir": str(art_dir), "status": "bad_metadata", "error": str(exc)[:200]}

    html_path = _article_html(art_dir, meta)
    if html_path is None:
        return {"dir": str(art_dir), "status": "no_html"}

    html = html_path.read_text(encoding="utf-8", errors="replace")
    raw_attrs = [m.group(1) for m in _IMG_ATTR.finditer(html)]
    # 上一次已被判为小图并跳过的 URL 记在 metadata 里，避免每次回填都重下一遍。
    skipped_before = set((meta.get("images") or {}).get("skipped_urls") or [])
    pending: dict[str, str] = {}
    for raw in raw_attrs:
        value = _html_to_url(raw)
        if not value or value.startswith("data:") or value.startswith("media/"):
            continue
        if value in skipped_before:
            continue
        pending.setdefault(raw, value)
    if not pending:
        return {"dir": str(art_dir), "status": "clean", "saved": 0}

    stats = {"expected": len(pending), "saved": 0, "failed": 0, "skipped": 0}
    newly_skipped: list[str] = []
    media_dir = art_dir / "media"
    next_index = _existing_indices(media_dir) + 1
    replacements: dict[str, str] = {}

    for raw, url in pending.items():
        if dry_run:
            stats["saved"] += 1
            continue
        name = download_one(url, media_dir, next_index)
        next_index += 1
        if not name:
            stats["failed"] += 1
            continue
        if _too_small(media_dir / name):
            try:
                (media_dir / name).unlink()
            except OSError:
                pass
            stats["skipped"] += 1
            newly_skipped.append(url)
            continue
        replacements[raw] = f"media/{name}"
        stats["saved"] += 1

    # 只命中极小图时也要落 metadata，否则下一次回填会重复下载同一批图标。
    if not dry_run and (replacements or newly_skipped):
        if replacements:
            for raw, local in replacements.items():
                html = html.replace(f'"{raw}"', f'"{local}"')
            html_path.write_text(html, encoding="utf-8")

        # metadata 里的 images 以磁盘实际数量为准，避免多次回填后计数漂移。
        meta["images"] = {
            "expected": int((meta.get("images") or {}).get("expected") or 0) + stats["expected"],
            "saved": _media_count(media_dir),
            "failed": stats["failed"],
            "skipped": stats["skipped"],
            "skipped_urls": sorted(skipped_before | set(newly_skipped)),
        }
        if not meta.get("article_key"):
            meta["article_key"] = article_link.normalize_article_url(meta.get("url") or "")
        meta["backfilled_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return {"dir": str(art_dir), "status": "updated", **stats}


def trigger_scan(scan_url: str, root: str) -> str:
    """通知媒体服务扫描；服务没起来时只提示，不算失败。"""
    try:
        resp = requests.post(scan_url, json={"root": root}, timeout=5)
        if resp.status_code in (200, 202):
            return f"已触发扫描：{resp.text[:120]}"
        return f"扫描请求被拒绝：{resp.status_code} {resp.text[:120]}"
    except Exception as exc:  # noqa: BLE001
        return f"媒体服务不可用，跳过扫描（{exc.__class__.__name__}）：稍后可在素材检索页手动扫描"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填历史文章的公众号图片")
    parser.add_argument("--limit", type=int, default=200, help="最多处理多少篇文章（默认 200）")
    parser.add_argument("--account", default="", help="只处理指定账号目录")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不下载也不改写")
    parser.add_argument(
        "--root",
        default="",
        help="文章根目录，默认 <仓库>/data/articles_full",
    )
    parser.add_argument("--scan-url", default="", help="媒体服务扫描接口，默认本机 8765")
    parser.add_argument("--no-scan", action="store_true", help="处理完不触发扫描")
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parent.parent
    root = Path(args.root).expanduser() if args.root else repo / "data" / "articles_full"
    if not root.is_dir():
        print(f"文章根目录不存在：{root}", file=sys.stderr)
        return 2

    dirs = _article_dirs(root, args.account or None)[: max(0, args.limit)]
    if not dirs:
        print(f"没有可处理的文章目录：{root}")
        return 0

    totals = {"updated": 0, "clean": 0, "no_html": 0, "bad_metadata": 0,
              "saved": 0, "failed": 0, "skipped": 0}
    for art_dir in dirs:
        result = backfill_article(art_dir, dry_run=args.dry_run)
        totals[result["status"]] = totals.get(result["status"], 0) + 1
        for key in ("saved", "failed", "skipped"):
            totals[key] += result.get(key, 0)
        if result["status"] in ("updated", "bad_metadata"):
            print(f"[{result['status']}] {result['dir']} saved={result.get('saved',0)} "
                  f"failed={result.get('failed',0)} skipped={result.get('skipped',0)}")

    print(
        f"完成：处理 {len(dirs)} 篇，更新 {totals['updated']}，无残留 {totals['clean']}，"
        f"缺正文 {totals['no_html']}，元数据异常 {totals['bad_metadata']}；"
        f"下载 {totals['saved']}，失败 {totals['failed']}，跳过小图 {totals['skipped']}"
        + ("（dry-run，未落盘）" if args.dry_run else "")
    )

    if not args.dry_run and not args.no_scan:
        scan_url = args.scan_url or "http://127.0.0.1:8765/api/media/scan"
        print(trigger_scan(scan_url, "articles-full"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
