"""全局 NSFW 检测：对已入库图片补跑初筛，不重建索引。

与扫描期初筛（``scan._nsfw_scan``）的区别：扫描只在文件新增/变化时顺带打分，
早先入库的图片没有结论；这里按 ``images`` 表遍历当前内容版本，逐张读源文件、
校验摘要、写 ``stage="first"`` 行。不碰 embedding，也不改索引状态，所以可以
随时取消、重复执行；单张失败只记账，不会让索引变成 partial。
"""
from __future__ import annotations

import hashlib

from . import config, media, review
from .store import STORE

# 失败明细只留前若干条：任务 JSON 会落盘并按轮询整份返回，几万条错误会把接口拖垮。
_MAX_ERRORS = 50


def available():
    """能不能发起检测。模型是否已加载不在判断内——首次推理时才真正加载。"""
    if not config.NSFW_SCAN:
        return {"enabled": False, "model": False, "reason": "nsfw_scan_disabled"}
    if not review.available():
        return {"enabled": True, "model": False, "reason": "nsfw_model_missing"}
    return {"enabled": True, "model": True, "reason": None}


def scan(roots=None, force=False, progress=None, cancel=None):
    """遍历就绪图片并写初筛结论。

    ``force=False`` 只处理「当前内容版本还没有 first=ok 结论」的图片，适合日常补检；
    ``force=True`` 全部重跑，用于换模型或调阈值之后。两种模式都按内容摘要校验：
    源文件已变而索引还没重扫时跳过，避免把新内容的结论挂到旧摘要上。
    """
    state = available()
    if not state["model"]:
        raise RuntimeError(state["reason"])

    counts = {"total": 0, "scanned": 0, "scored": 0, "flagged": 0, "skipped": 0,
              "failed": 0, "errors": [], "complete": False, "status": "running"}
    targets = STORE.nsfw_targets(roots, force=force)
    counts["total"] = len(targets)

    for row in targets:
        if cancel and cancel():
            counts["status"] = "cancelled"
            return counts
        counts["scanned"] += 1
        data = None
        try:
            data = media.read_bytes(row["root"], row["relpath"])
        except (OSError, TimeoutError, ValueError) as exc:
            _fail(counts, row, str(exc))
        if data is not None:
            digest = hashlib.sha256(data).hexdigest()
            indexed = row["content_sha256"]
            if indexed and digest != indexed:
                counts["skipped"] += 1
            else:
                result = review.review_bytes(data)
                if result.get("status") != "ok":
                    _fail(counts, row, result.get("error") or "review_failed")
                else:
                    try:
                        STORE.set_nsfw(row["root"], row["relpath"], digest,
                                       score=result["score"], label=result["label"],
                                       status="ok", model_version=config.NSFW_MODEL_SOURCE)
                    except Exception as exc:  # noqa: BLE001 - 单图落库失败不该中断整轮
                        _fail(counts, row, str(exc))
                    else:
                        counts["scored"] += 1
                        if result["label"] == "nsfw":
                            counts["flagged"] += 1
        if progress:
            progress(dict(counts, current=f'{row["root"]}:{row["relpath"]}'))

    counts["complete"] = True
    counts["status"] = "partial" if counts["failed"] else "completed"
    return counts


def _fail(counts, row, error):
    counts["failed"] += 1
    if len(counts["errors"]) < _MAX_ERRORS:
        counts["errors"].append({"root": row["root"], "path": row["relpath"],
                                 "error": str(error)[:200]})
