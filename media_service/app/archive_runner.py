"""逐篇归档：内容摘要身份、目标核对、成功后索引。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import config, media
from .archive import article_id, to_png
from .browser import open_browser, capture, download_image
from .filter import inspect, judge
from .store import STORE


def _save(job: dict) -> None:
    from .jobs import JOBS, _now
    job["updated"] = _now()
    with JOBS.lock:
        JOBS._save(job)


def _same_target(root: str, relpath: str, digest: str) -> bool:
    try:
        actual = media.read_bytes(root, relpath)
    except FileNotFoundError:
        return False
    if hashlib.sha256(actual).hexdigest() != digest:
        raise RuntimeError(f"target_conflict: {relpath}")
    return True


# 浏览器/上下文/页面被回收时 Playwright 抛出的报错特征；命中即视为「整批浏览器已死」。
_BROWSER_GONE_MARKERS = (
    "target page",
    "target closed",
    "context or browser has been closed",
    "browser has been closed",
    "connection closed",
    "page has been closed",
)
# 同一批次最多按代重启几次，避免浏览器持续崩溃时无限重开。
_MAX_BROWSER_RESTART = 3


def _browser_gone(exc: Exception) -> bool:
    """判断异常是否来自浏览器/上下文/页面已关闭（决定整批按代重启）。"""
    msg = str(exc).lower()
    return any(marker in msg for marker in _BROWSER_GONE_MARKERS)


def run_archive(job: dict) -> None:
    root = config.get_root(job["target_root"])
    if root["path"] != job["target_path"]:
        raise RuntimeError("目标 root 配置路径已变化，拒绝继续写入")
    work = config.DATA_DIR / "jobs" / job["id"]
    work.mkdir(parents=True, exist_ok=True)
    job.setdefault("articles", [])
    job.setdefault("errors", [])
    prior = {x.get("url"): x for x in job["articles"]}
    batch_urls = list(job["urls"])
    index = 0
    restarts = 0
    while index < len(batch_urls):
        # 每代浏览器开始前刷新已完成快照：重启后已归档的篇不再重抓。
        prior = {x.get("url"): x for x in job["articles"]}
        batch_lost = False
        with open_browser() as (context, page):
            while index < len(batch_urls):
                url = batch_urls[index]
                if job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    _save(job)
                    return
                old = prior.get(url)
                if old and old.get("status") == "completed":
                    index += 1
                    continue
                record = old or {"url": url, "status": "running", "files": [], "errors": []}
                if old is None:
                    job["articles"].append(record)
                if old and old.get("errors"):
                    record.setdefault("error_history", []).extend(old["errors"])
                    record["errors"] = []
                record["status"] = "running"
                _save(job)
                try:
                    page_data = capture(page, url)
                    if page_data.get("status") != "ok":
                        record["status"] = page_data.get("status", "failed")
                        record["errors"].append(page_data.get("reason") or "页面不可用")
                        if record["status"] == "verify":
                            job["status"] = "waiting_verification"
                            _save(job)
                            return
                        _save(job)
                        index += 1
                        continue
                    aid = article_id(url)
                    record.update({"id": aid, "title": page_data.get("title") or "未命名文章",
                                   "published_at": page_data.get("publish") or None,
                                   "comment_status": "comment_needs_login" if page_data.get("commentNeedsLogin") else "not_requested"})
                    urls = []
                    if page_data.get("cover"):
                        urls.append(page_data["cover"])
                    urls.extend(page_data.get("contentImgs") or [])
                    urls = list(dict.fromkeys(urls))
                    seen = []
                    article_dir = "/".join(x for x in (job.get("target_dir", ""), aid) if x)
                    article_work = work / aid
                    article_work.mkdir(exist_ok=True)
                    for source_url in urls:
                        raw = download_image(context, source_url)
                        if raw is None:
                            record["errors"].append({"url": source_url, "error": "download_failed"})
                            continue
                        png = to_png(raw)
                        if png is None:
                            record["errors"].append({"url": source_url, "error": "decode_failed"})
                            continue
                        digest = hashlib.sha256(png).hexdigest()
                        local = article_work / (digest + ".png")
                        if not local.exists():
                            fd, temp = tempfile.mkstemp(dir=article_work, prefix=".image-")
                            try:
                                with os.fdopen(fd, "wb") as stream:
                                    stream.write(png)
                                    stream.flush()
                                    os.fsync(stream.fileno())
                                os.replace(temp, local)
                            finally:
                                if os.path.exists(temp): os.unlink(temp)
                        info = inspect(local)
                        reason = judge(info, seen)
                        if reason:
                            record.setdefault("filtered", []).append({"url": source_url, "reason": reason})
                            continue
                        seen.append(info)
                        relpath = article_dir + "/" + local.name
                        result = media.write_new(job["target_root"], relpath, local)
                        if result["status"] == "skipped":
                            _same_target(job["target_root"], relpath, digest)
                        actual = media.read_bytes(job["target_root"], relpath)
                        if hashlib.sha256(actual).hexdigest() != digest:
                            raise RuntimeError(f"target_conflict: {relpath}")
                        stat = media.stat_entry(job["target_root"], relpath)
                        indexed = STORE.add_image(job["target_root"], relpath, actual,
                                                   article={"id": aid, "title": record["title"], "url": url,
                                                            "published_at": record["published_at"]},
                                                   origin="archive", size=stat.size, mtime=stat.mtime)
                        from datetime import datetime, timezone
                        file_record = {"relpath": relpath, "sha256": digest, "uploaded_at": datetime.now(timezone.utc).isoformat(), "upload_confirmed": True, "version": indexed["content_version"],
                                       "index_status": indexed["status"], "local_path": str(local)}
                        if not any(f.get("relpath") == relpath for f in record["files"]):
                            record["files"].append(file_record)
                        _save(job)
                    record["status"] = "partial" if record["errors"] else "completed"
                except Exception as exc:
                    if _browser_gone(exc) and restarts < _MAX_BROWSER_RESTART:
                        batch_lost = True
                    else:
                        record["status"] = "failed"
                        record["errors"].append(str(exc)[:500])
                _save(job)
                if batch_lost:
                    break
                index += 1
        if batch_lost and index < len(batch_urls):
            restarts += 1
            continue
        break
    # 来源清单确认是七天原件清理的前置条件；仅写成功图片的来源关系。
    files = [f for article in job["articles"] for f in article.get("files", [])]
    if files:
        manifest = {"version": 1, "job_id": job["id"], "articles": [
            {"id": article.get("id"), "title": article.get("title"), "url": article.get("url"),
             "published_at": article.get("published_at"),
             "files": [{"relpath": f["relpath"], "sha256": f["sha256"]} for f in article.get("files", [])]}
            for article in job["articles"] if article.get("files")
        ]}
        payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        local_manifest = work / "manifest.json"
        temporary = work / ".manifest.tmp"
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, local_manifest)
        manifest_dir = "/".join(x for x in (job.get("target_dir", ""), "_manifests") if x)
        relpath = f"{manifest_dir}/{job['id']}.json"
        try:
            media.write_new(job["target_root"], relpath, local_manifest)
            if hashlib.sha256(media.read_bytes(job["target_root"], relpath)).digest() != hashlib.sha256(payload).digest():
                raise RuntimeError("target_conflict: 来源清单内容不一致")
            for f in files:
                f["manifest_confirmed"] = True
                f["manifest_path"] = relpath
            job["manifest_path"] = relpath
            _save(job)
        except Exception as exc:
            job["errors"].append(f"来源清单未确认：{str(exc)[:300]}")
    job["status"] = "partial" if job["errors"] or any(r["status"] != "completed" for r in job["articles"]) else "completed"
    _save(job)
