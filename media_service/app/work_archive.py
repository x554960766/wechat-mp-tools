"""作品级归档：把已下载的完整作品（正文/文案/配图/封面/视频/字幕/来源）
复制到 NAS，逐文件读回校验、持久化校验结果，再按不变量清理本地文件。

对应规格 nas-archive-media-library.md §4.1、§4.2：
- 作品是归档单位，保持目录内相对路径，身份为平台 + 稳定作品 ID。
- 清理不变量：每个作品的全部文件及内容摘要与本次清单一致、NAS 校验结果已
  持久化、对应本地文件仍属于同一版本时，才允许清理。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import config, media

# 作品状态机（§4.2 七态）
PENDING = "pending"                       # 待归档
COPYING = "copying"                       # 复制或校验中
FAILED = "failed"                         # 失败（可重试，本地保留）
TARGET_MATCHED = "target_matched"         # 目标已有同版本（验证一致后沿用正常清理）
CONFLICT = "conflict"                     # 目标已有不同内容（标记冲突，保留两边）
PENDING_CLEANUP = "archived_pending_cleanup"  # 已归档待清理
ARCHIVED = "archived"                     # 已归档

WORK_STATUS_LABELS = {
    PENDING: "待归档",
    COPYING: "复制或校验中",
    FAILED: "失败",
    TARGET_MATCHED: "目标已有同版本",
    CONFLICT: "目标已有不同内容",
    PENDING_CLEANUP: "已归档待清理",
    ARCHIVED: "已归档",
}

# 已通过目标校验、可以进入清理阶段的状态
_VERIFIED = (TARGET_MATCHED, PENDING_CLEANUP)


def _save(job: dict) -> None:
    """持久化任务快照，复用 job 队列的原子写。"""
    from .jobs import JOBS, _now

    job["updated"] = _now()
    with JOBS.lock:
        JOBS._save(job)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_target(root_id: str, relpath: str, digest: str) -> bool:
    """读回目标内容比对摘要；目标不存在返回 False，内容不一致判冲突。"""
    try:
        actual = media.read_bytes(root_id, relpath)
    except FileNotFoundError:
        return False
    if _digest(actual) != digest:
        raise RuntimeError(f"target_conflict: {relpath}")
    return True


def _safe_segment(name: str) -> str:
    """把稳定作品 ID 规整成可放进相对路径的一段。"""
    text = "".join(ch if (ch.isalnum() or ch in "-_.") else "-" for ch in str(name))
    text = text.strip("-.")
    return text or "work"


def _work_target_dir(job: dict, work: dict) -> str:
    target_dir = (job.get("target_dir") or "").strip("/")
    prefix = f"{target_dir}/" if target_dir else ""
    return f"{prefix}{_safe_segment(work['id'])}"


def _local_path(root: dict, relpath: str) -> Path:
    """把相对路径解析到本地媒体根内，防符号链接/越界。"""
    base = Path(root["path"]).resolve()
    target = (base / relpath).resolve()
    if not target.is_relative_to(base):
        raise ValueError("路径越过媒体根")
    return target


def _suffix(relpath: str) -> str:
    suffix = Path(relpath).suffix
    return suffix if suffix and len(suffix) <= 8 else ".bin"


def _archive_work(job: dict, work: dict, target_root: dict) -> None:
    """复制一个作品的全部文件到目标根，逐文件读回校验。"""
    if work.get("status") in (ARCHIVED, PENDING_CLEANUP, TARGET_MATCHED):
        return
    work["status"] = COPYING
    work.setdefault("errors", [])
    source_root_id = work.get("source_root")
    source_root = config.get_root(source_root_id)
    target_root_id = job["target_root"]
    target_dir = _work_target_dir(job, work)

    target_match = False
    for item in work.get("files", []):
        rel = item["relpath"]
        try:
            media.validate_relpath(rel)
            target_rel = f"{target_dir}/{rel}"
            media.validate_relpath(target_rel)
        except ValueError as exc:
            # 越界/非法相对路径：本地保留，判失败
            work["errors"].append(f"非法相对路径，本地保留：{rel}（{exc}）")
            work["status"] = FAILED
            return

        try:
            data = media.read_bytes(source_root_id, rel)
        except Exception as exc:  # 源不可读：本地保留，判失败
            work["errors"].append(f"读取本地文件失败 {rel}：{str(exc)[:300]}")
            work["status"] = FAILED
            return
        digest = _digest(data)
        if digest != item.get("digest"):
            # 源并发变更：本地文件已不属于本次清单版本，保留并判失败
            work["errors"].append(f"本地文件内容已变化，保留本地：{rel}")
            work["status"] = FAILED
            return

        with tempfile.NamedTemporaryFile(
            prefix=".ai-media-wa-", suffix=_suffix(rel), delete=False
        ) as tf:
            tf.write(data)
            staged = tf.name
        try:
            result = media.write_new(target_root_id, target_rel, staged)
        except RuntimeError as exc:
            # target_conflict 是内容冲突；其余（NAS 断连等）是可重试的写入失败
            if "target_conflict" in str(exc):
                work["errors"].append(f"目标已有不同内容，保留两边：{target_rel}")
                work["status"] = CONFLICT
            else:
                work["errors"].append(f"目标写入失败，本地保留：{target_rel}：{str(exc)[:300]}")
                work["status"] = FAILED
            return
        finally:
            os.unlink(staged)

        if result.get("status") == "skipped":
            # 目标已存在：读回校验决定是「同版本」还是「内容冲突」
            try:
                same = _same_target(target_root_id, target_rel, digest)
            except RuntimeError as exc:
                if "target_conflict" in str(exc):
                    work["errors"].append(f"目标已有不同内容，保留两边：{target_rel}")
                    work["status"] = CONFLICT
                else:
                    work["errors"].append(f"目标校验失败，本地保留：{target_rel}：{str(exc)[:300]}")
                    work["status"] = FAILED
                return
            if same:
                target_match = True
            else:
                work["errors"].append(f"目标已有不同内容，保留两边：{target_rel}")
                work["status"] = CONFLICT
                return

        try:
            actual = media.read_bytes(target_root_id, target_rel)
        except Exception as exc:
            work["errors"].append(f"目标读回失败 {target_rel}：{str(exc)[:300]}")
            work["status"] = FAILED
            return
        if _digest(actual) != digest:
            work["errors"].append(f"目标校验不一致：{target_rel}")
            work["status"] = FAILED
            return

        item["target_relpath"] = target_rel
        item["target_size"] = result.get("size")
        item["target_digest"] = digest

    work["target_dir"] = target_dir
    work["manifest_confirmed"] = False
    # 目标原本就有同版本则标记 target_matched，否则进入待清理
    work["status"] = TARGET_MATCHED if target_match else PENDING_CLEANUP


def _persist_manifest(job: dict, target_root: dict) -> None:
    """把本次归档的作品清单与内容摘要写入目标 _manifests/，读回校验后发布。

    发布成功后才允许清理本地：清单是「NAS 校验结果已持久化」的凭据。
    """
    works = job.get("works", [])
    files = [
        {
            "work_id": w["id"],
            "platform": w.get("platform"),
            "relpath": f.get("target_relpath") or f.get("relpath"),
            "source_relpath": f["relpath"],
            "digest": f.get("target_digest") or f["digest"],
            "size": f.get("target_size") or f.get("size"),
        }
        for w in works
        if w.get("status") in (_VERIFIED + (ARCHIVED,))
        for f in w.get("files", [])
    ]
    if not files:
        return
    payload = {
        "job_id": job["id"],
        "target_root": job["target_root"],
        "target_dir": job.get("target_dir", ""),
        "created": job.get("created"),
        "files": files,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    work_dir = config.DATA_DIR / "jobs" / job["id"]
    work_dir.mkdir(parents=True, exist_ok=True)
    local_manifest = work_dir / "manifest.json"
    local_manifest.write_text(blob, encoding="utf-8")

    manifest_dir = "/".join(x for x in (job.get("target_dir", ""), "_manifests") if x)
    relpath = f"{manifest_dir}/{job['id']}.json"
    try:
        media.write_new(job["target_root"], relpath, str(local_manifest))
        actual = media.read_bytes(job["target_root"], relpath)
        if _digest(actual) != _digest(blob.encode("utf-8")):
            raise RuntimeError("target_conflict: 来源清单内容不一致")
    except Exception as exc:
        job["errors"].append(f"来源清单未确认：{str(exc)[:300]}")
        for w in works:
            if w.get("status") in _VERIFIED:
                w["manifest_confirmed"] = False
        return
    for w in works:
        if w.get("status") in _VERIFIED:
            w["manifest_confirmed"] = True
    job["manifest_path"] = relpath
    _save(job)


def _cleanup_work(job: dict, work: dict) -> None:
    """按 §4.2 不变量清理本地文件。

    清理前先复核目标仍持有本次校验过的同版本内容：目标缺失或已变则保留本地，
    工作留在已归档待清理，重试时再复核。
    """
    if work.get("status") not in _VERIFIED:
        return
    if not work.get("manifest_confirmed"):
        return
    if work.get("cleaned"):
        work["status"] = ARCHIVED
        return

    source_root = config.get_root(work["source_root"])
    if source_root.get("type") != "local":
        work["cleanup_warnings"] = ["源媒体根不是本地目录，跳过本地清理"]
        return

    removed: list[str] = []
    blocked: list[dict] = []
    for item in work.get("files", []):
        rel = item["relpath"]
        expected = item.get("target_digest") or item.get("digest")
        target_rel = item.get("target_relpath")

        # 复核目标仍有效（§4.2：已归档待清理重试须复核目标仍有效）
        if target_rel:
            try:
                target_bytes = media.read_bytes(job["target_root"], target_rel)
            except Exception as exc:
                blocked.append({"relpath": rel, "reason": f"目标已失效，保留本地：{str(exc)[:200]}"})
                continue
            if _digest(target_bytes) != expected:
                blocked.append({"relpath": rel, "reason": "目标内容已变化，保留本地"})
                continue

        try:
            path = _local_path(source_root, rel)
        except ValueError as exc:
            blocked.append({"relpath": rel, "reason": str(exc)})
            continue
        if not path.exists():
            removed.append(rel)
            continue
        try:
            actual = media.read_bytes(work["source_root"], rel)
        except Exception as exc:
            blocked.append({"relpath": rel, "reason": f"读取失败：{str(exc)[:200]}"})
            continue
        if _digest(actual) != expected:
            blocked.append({"relpath": rel, "reason": "本地内容已变化，保留"})
            continue
        try:
            os.remove(path)
            removed.append(rel)
        except OSError as exc:
            blocked.append({"relpath": rel, "reason": f"删除失败：{str(exc)[:200]}"})

    work["cleanup_removed"] = removed
    work["cleanup_blocked"] = blocked
    if not blocked:
        work["cleaned"] = True
        work["status"] = ARCHIVED
    else:
        work["cleanup_warnings"] = [f"本地仍有 {len(blocked)} 个文件需保留，未清理"]


def run_work_archive(job: dict) -> None:
    """作品级归档执行器：复制 → 校验 → 发布清单 → 清理本地。"""
    target_root = config.get_root(job["target_root"])
    if target_root["path"] != job.get("target_path"):
        raise RuntimeError("目标 root 配置路径已变化，拒绝继续写入")
    if not target_root.get("writable"):
        raise RuntimeError("目标媒体根只读")
    job.setdefault("works", [])
    job.setdefault("errors", [])

    # cleanup=False 用于「下载即自动归档」：NAS 校验通过后保留本地文件，
    # 否则素材检索的本地根与离线打开能力会被破坏。默认 True 保持手动归档语义。
    cleanup = job.get("cleanup", True)
    try:
        # 阶段一：复制并逐文件校验（已校验作品跳过）
        for work in job["works"]:
            if work.get("status") in _VERIFIED + (ARCHIVED,):
                continue
            _archive_work(job, work, target_root)
            _save(job)

        # 阶段二：发布已校验的作品清单，作为清理凭据
        _persist_manifest(job, target_root)

        # 阶段三：按不变量清理本地文件（仅清单一致且目标已校验的作品）
        for work in job["works"]:
            if cleanup:
                _cleanup_work(job, work)
            elif work.get("status") in _VERIFIED:
                # 保留本地：已通过目标校验的作品直接记为已归档，并留下不清理原因
                work["status"] = ARCHIVED
                work["local_retained"] = True
                work.setdefault("cleanup_warnings", []).append("自动归档模式：保留本地文件")
            _save(job)

        archived = sum(1 for w in job["works"] if w.get("status") == ARCHIVED)
        active = [
            w for w in job["works"]
            if w.get("status") in (PENDING, COPYING, FAILED, CONFLICT)
        ]
        if active and archived == 0:
            job["status"] = "failed"
        elif active or job["errors"]:
            job["status"] = "partial"
        else:
            job["status"] = "completed"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)[:300]
        job["errors"].append(job["error"])
    _save(job)
