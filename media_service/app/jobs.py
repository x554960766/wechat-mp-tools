"""持久化的单 worker 队列；扫描/归档维护不并发占用浏览器与索引。"""
from __future__ import annotations

import json
import copy
import queue
import threading
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class JobManager:
    def __init__(self) -> None:
        self.dir = config.DATA_DIR / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.verify_done = threading.Event()
        self.verify_done.set()
        self.pending: queue.Queue[str] = queue.Queue()
        self.last_cleanup = time.monotonic()
        self.jobs: dict[str, dict[str, Any]] = {}
        for path in self.dir.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if item.get("status") in {"queued", "running", "waiting_verification", "cancelling"}:
                    item["status"] = "interrupted"
                    item["message"] = "服务重启，任务可恢复"
                    self._save(item)
                self.jobs[item["id"]] = item
            except (OSError, ValueError, KeyError, TypeError):
                continue
        self.worker = threading.Thread(target=self._loop, daemon=True, name="ai-media-worker")
        self.worker.start()
        self.pending.put("__cleanup__")

    def _save(self, item: dict[str, Any]) -> None:
        path = self.dir / f"{item['id']}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def create(self, kind: str, **values: Any) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex, "kind": kind, "status": "queued", "created": _now(),
                "updated": _now(), "errors": [], "cancel_requested": False, **values}
        with self.lock:
            active = sum(job.get("status") in {"queued", "running", "waiting_verification", "cancelling"}
                         for job in self.jobs.values())
            if active >= config.MAX_PENDING_JOBS:
                raise RuntimeError("queue_full")
            if shutil.disk_usage(config.DATA_DIR).free < config.MIN_FREE_BYTES:
                raise RuntimeError("disk_full")
            self.jobs[item["id"]] = item
            self._save(item)
        self.pending.put(item["id"])
        return dict(item)

    def enqueue_scan(self, root: str, rebuild: bool = False) -> dict[str, Any]:
        return self.create("scan", root=root, rebuild=bool(rebuild), scanned=0,
                           indexed_new=0, indexed_updated=0, indexed_unchanged=0,
                           deleted=0, failed=0, complete=False)

    def retry(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            old = self.jobs.get(job_id)
            if old is None:
                return None
            if old.get("active_retry_id"):
                active = self.jobs.get(old["active_retry_id"])
                if active and active["status"] in {"queued", "running", "waiting_verification", "cancelling"}:
                    return dict(active)
            if old["status"] not in {"interrupted", "partial", "failed", "cancelled"}:
                raise ValueError("当前任务不可恢复")
            if old["kind"] != "archive":
                raise ValueError("当前任务不支持恢复")
            fields = {k: old[k] for k in ("urls", "target_root", "target_path", "target_dir")}
            new = self.create("archive", **fields, articles=copy.deepcopy(old.get("articles", [])), parent_id=job_id)
            old["active_retry_id"] = new["id"]
            self._save(old)
            return new

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            item = self.jobs.get(job_id)
            return dict(item) if item else None

    def list(self, limit: int, offset: int, kind: str | None = None) -> tuple[list[dict[str, Any]], int]:
        with self.lock:
            jobs = (job for job in self.jobs.values() if kind is None or job.get("kind") == kind)
            ordered = sorted(jobs, key=lambda x: x["created"], reverse=True)
            return [dict(x) for x in ordered[offset:offset + limit]], len(ordered)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            item = self.jobs.get(job_id)
            if not item:
                return None
            if item["status"] in {"queued", "running"}:
                item["cancel_requested"] = True
                item["status"] = "cancelling"
                item["updated"] = _now()
                self._save(item)
            return dict(item)

    def enqueue_export(self, source_job_id: str) -> dict[str, Any]:
        with self.lock:
            source = self.jobs.get(source_job_id)
            if source is None or source.get("kind") != "archive":
                raise ValueError("归档任务不存在")
            active_id = source.get("active_export_id")
            active = self.jobs.get(active_id) if active_id else None
            if active and active.get("status") in {"queued", "running"}:
                return dict(active)
            item = self.create("export", source_job_id=source_job_id, status="queued", error=None)
            source["active_export_id"] = item["id"]
            self._save(source)
            return item

    def start_verify(self, job_id: str, index: int | None = None) -> dict[str, Any]:
        with self.lock:
            source = self.jobs.get(job_id)
            if source is None or source.get("kind") != "archive":
                raise ValueError("归档任务不存在")
            if source.get("verification_id"):
                current = self.jobs.get(source["verification_id"])
                if current and current.get("status") in {"queued", "running"}:
                    return dict(current)
            if any(job.get("kind") == "verify" and job.get("status") in {"queued", "running"}
                   for job in self.jobs.values()):
                raise ValueError("已有人工验证窗口正在运行")
            articles = source.get("articles", [])
            pending = [i for i, a in enumerate(articles) if a.get("status") == "verify"]
            if not pending:
                raise ValueError("任务没有等待验证的文章")
            if index is None:
                index = pending[0]
            if index not in pending:
                raise ValueError("指定文章不处于等待验证状态")
            self.verify_done.clear()
            try:
                ctrl = self.create("verify", source_job_id=job_id, article_index=index,
                                   article_url=articles[index].get("url"), expires_seconds=180)
            except Exception:
                self.verify_done.set()
                raise
            source["verification_id"] = ctrl["id"]
            source["status"] = "waiting_verification"
            source["verification_index"] = index
            self._save(source)
            return ctrl

    def _loop(self) -> None:
        while True:
            try:
                job_id = self.pending.get(timeout=60)
            except queue.Empty:
                job_id = "__cleanup__" if time.monotonic() - self.last_cleanup >= 24 * 60 * 60 else None
            if job_id is None:
                continue
            if time.monotonic() - self.last_cleanup >= 24 * 60 * 60 and job_id != "__cleanup__":
                self.pending.put(job_id)
                job_id = "__cleanup__"
            if job_id == "__cleanup__":
                try:
                    from .archive_maintenance import cleanup_expired
                    cleanup_expired()
                except Exception:
                    logging.getLogger(__name__).exception("归档原件清理失败")
                finally:
                    self.last_cleanup = time.monotonic()
                continue
            with self.lock:
                item = self.jobs.get(job_id)
                if item and item.get("kind") == "archive" and not self.verify_done.is_set():
                    self.pending.put(job_id)
                    defer_archive = True
                else:
                    defer_archive = False
            if defer_archive:
                self.verify_done.wait(timeout=0.25)
                continue
            with self.lock:
                item = self.jobs.get(job_id)
                if not item or item["cancel_requested"]:
                    if item:
                        item["status"] = "cancelled"
                        self._save(item)
                    continue
                item["status"] = "running"
                item["updated"] = _now()
                self._save(item)
            try:
                self._execute(item)
            except Exception as exc:
                item["status"] = "failed"
                item["error"] = str(exc)[:300]
                item["errors"].append(str(exc)[:300])
            finally:
                item["updated"] = _now()
                with self.lock:
                    self._save(item)

    @staticmethod
    def _execute(item: dict[str, Any]) -> None:
        if item["kind"] == "scan":
            from .scan import scan_root
            result = scan_root(item["root"], rebuild=item.get("rebuild", False),
                               progress=lambda p: item.update(p),
                               cancel=lambda: bool(item.get("cancel_requested")))
            item.update(result)
            item["status"] = "cancelled" if result.get("status") == "cancelled" else result.get("status", "completed")
            if item["status"] not in {"cancelled", "partial", "failed"}:
                item["status"] = "completed"
            return
        if item["kind"] == "archive":
            from .archive_runner import run_archive
            run_archive(item)
            return
        if item["kind"] == "export":
            from .archive_maintenance import build_export
            build_export(item)
            return
        if item["kind"] == "verify":
            try:
                threading.Thread(target=JobManager._verify_window, args=(item,), daemon=True,
                                 name=f"ai-media-verify-{item['id'][:8]}").start()
            except Exception:
                JOBS.verify_done.set()
                raise
            return
        raise ValueError("未知任务类型")

    @staticmethod
    def _verify_window(item: dict[str, Any]) -> None:
        from . import browser
        from .jobs import JOBS, _now
        try:
            with browser.open_browser(headless=False) as (_ctx, page):
                page.goto(item["article_url"], wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)
                page.wait_for_timeout(int(item.get("expires_seconds", 180)) * 1000)
            item["status"] = "completed"
            item["result"] = "window_closed; verification_not_assumed"
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)[:300]
        item["updated"] = _now()
        JOBS.verify_done.set()
        with JOBS.lock:
            JOBS._save(item)
            parent = JOBS.jobs.get(item.get("source_job_id"))
            if parent and parent.get("status") == "waiting_verification":
                parent["status"] = "interrupted"
                parent["message"] = "验证窗口已结束；请显式恢复任务，系统不推断验证成功"
                parent["updated"] = _now()
                JOBS._save(parent)


JOBS = JobManager()


def enqueue_scan(root: str, rebuild: bool = False) -> dict[str, Any]:
    return JOBS.enqueue_scan(root, rebuild)
