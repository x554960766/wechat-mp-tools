"""归档导出与七天原件清理；所有远端读取都做 SHA-256 复核。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from . import config, media

_use_lock = threading.Lock()
_in_use: dict[str, int] = {}

# 写入时用的临时前缀；崩溃残留不参与扫描（media 列举时排除），启动时按任务清单清掉。
TEMP_PREFIXES = (".download-", ".ai-media-", ".download.")


def sweep_temp_files() -> dict:
    """清理任务暂存区里的临时残留。只动 DATA_DIR/jobs，不遍历媒体根。"""
    jobs_dir = config.DATA_DIR / "jobs"
    allowed = jobs_dir.resolve()
    report = {"removed": 0, "errors": []}
    if not jobs_dir.is_dir():
        return report
    for path in jobs_dir.rglob("*"):
        if not path.is_file() or not path.name.startswith(TEMP_PREFIXES):
            continue
        if not path.resolve().is_relative_to(allowed):
            continue
        try:
            path.unlink()
            report["removed"] += 1
        except OSError as exc:
            report["errors"].append({"path": str(path), "error": str(exc)})
    return report


@contextmanager
def protect_download(job_id: str):
    begin_download(job_id)
    try:
        yield
    finally:
        end_download(job_id)


def begin_download(job_id: str) -> None:
    with _use_lock:
        _in_use[job_id] = _in_use.get(job_id, 0) + 1


def end_download(job_id: str) -> None:
    with _use_lock:
        count = _in_use.get(job_id, 0)
        if count <= 1:
            _in_use.pop(job_id, None)
        else:
            _in_use[job_id] = count - 1


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def build_export(job: dict) -> dict:
    """完整恢复并校验清单中的文件后，原子发布 ZIP。"""
    from .jobs import JOBS, _now

    source = JOBS.get_job(job["source_job_id"])
    if source is None or source.get("kind") != "archive":
        raise ValueError("归档任务不存在")
    root = config.get_root(source["target_root"])
    if root["path"] != source.get("target_path"):
        raise ValueError("归档 root 配置已变化，拒绝恢复 ZIP")
    files = [f for article in source.get("articles", []) for f in article.get("files", [])]
    if not files:
        raise ValueError("任务清单没有可导出的图片")
    out_dir = config.DATA_DIR / "jobs" / source["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "export.zip"
    fd, temp_name = tempfile.mkstemp(prefix=".export-", suffix=".zip", dir=out_dir)
    os.close(fd)
    try:
        with protect_download(source["id"]), ZipFile(temp_name, "w", ZIP_DEFLATED) as archive:
            for item in files:
                expected = item.get("sha256")
                if not isinstance(expected, str) or len(expected) != 64:
                    raise ValueError(f"导出清单摘要无效：{item.get('relpath')}")
                local = Path(item.get("local_path", ""))
                if local.is_file():
                    data = local.read_bytes()
                else:
                    data = media.read_bytes(source["target_root"], item["relpath"])
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError(f"导出图片摘要不一致：{item['relpath']}")
                archive.writestr(item["relpath"], data)
        os.replace(temp_name, target)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    job.update({"export_path": str(target), "status": "completed", "completed": _now(),
                "exported_at": _now(), "error": None})
    return job


def cleanup_expired(now: datetime | None = None) -> dict:
    """只清理拥有明确上传确认时间、满 7×24 小时且远端摘要匹配的原件。"""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now - timedelta(days=7)
    report = {"checked": 0, "deleted": 0, "deferred": 0, "errors": []}
    jobs_dir = config.DATA_DIR / "jobs"
    for path in jobs_dir.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if job.get("kind") != "archive":
            continue
        job_id = job.get("id", "")
        try:
            root = config.get_root(job["target_root"])
            if root["path"] != job.get("target_path"):
                report["errors"].append({"job": job_id, "error": "root_binding_changed"})
                continue
        except (KeyError, TypeError):
            report["errors"].append({"job": job_id, "error": "root_missing"})
            continue
        with _use_lock:
            protected = job_id in _in_use
        for article in job.get("articles", []):
            for item in article.get("files", []):
                local_name = item.get("local_path")
                if not local_name or item.get("cleaned_at"):
                    continue
                local = Path(local_name).resolve()
                allowed = (config.DATA_DIR / "jobs").resolve()
                if not local.is_relative_to(allowed):
                    report["errors"].append({"job": job_id, "path": local_name, "error": "path_outside_job_data"})
                    continue
                uploaded_at = _parse_utc(item.get("uploaded_at"))
                if item.get("upload_confirmed") is not True or item.get("manifest_confirmed") is not True or not item.get("manifest_path") or uploaded_at is None or uploaded_at > cutoff or protected:
                    report["deferred"] += 1
                    continue
                report["checked"] += 1
                try:
                    remote = media.read_bytes(job["target_root"], item["relpath"])
                    digest = hashlib.sha256(remote).hexdigest()
                    if digest != item.get("sha256"):
                        report["deferred"] += 1
                        report["errors"].append({"job": job_id, "path": item["relpath"], "error": "remote_digest_mismatch"})
                        continue
                    if local.exists():
                        if hashlib.sha256(local.read_bytes()).hexdigest() != digest:
                            report["deferred"] += 1
                            report["errors"].append({"job": job_id, "path": local_name, "error": "local_digest_mismatch"})
                            continue
                        local.unlink()
                        report["deleted"] += 1
                    item["cleaned_at"] = now.isoformat(timespec="microseconds")
                except Exception as exc:
                    report["deferred"] += 1
                    report["errors"].append({"job": job_id, "path": item.get("relpath"), "error": str(exc)[:250]})
        all_files = [f for article in job.get("articles", []) for f in article.get("files", [])]
        export_path = jobs_dir / job_id / "export.zip"
        # ZIP 派生文件在其全部原件均完成七天清理后才成为候选。
        if all_files and all(f.get("cleaned_at") for f in all_files) and export_path.is_file() and not protected:
            try:
                export_path.unlink()
            except OSError as exc:
                report["errors"].append({"job": job_id, "path": str(export_path), "error": str(exc)[:250]})
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
        # 同步当前进程状态，避免后续任务保存旧快照覆盖 cleaned_at。
        try:
            from .jobs import JOBS
            with JOBS.lock:
                live = JOBS.jobs.get(job_id)
                if live is not None:
                    live.clear()
                    live.update(job)
        except ImportError:
            pass
    return report
