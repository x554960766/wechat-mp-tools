"""视频代表帧：ffmpeg 抽帧 → Chinese-CLIP 编码 → 入库，供画面检索命中视频。

口径与约束：
- 只在扫描入库阶段抽帧；间隔 ``AI_MEDIA_FRAME_INTERVAL``（默认 10 秒），单视频最多
  ``AI_MEDIA_FRAME_MAX`` 帧（默认 24），避免长视频把库和磁盘一起撑爆。
- 只处理 ``local`` 类型媒体根：远端（zspace）根抽帧要先把整段视频拉回本地，代价远大于
  收益，这类根等作品归档成本地副本后再补。
- 版本判定用视频文件的流式 sha256（分块读，不整段进内存）；摘要没变直接跳过，变了先删
  旧帧再重抽，保证「库里的帧一定属于当前版本的视频」。
- 单个视频失败只记录，不改变本轮扫描结论：视频损坏、ffmpeg 缺失、时长探测失败都算。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, encoder
from .store import STORE

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".flv"}
_FFMPEG = "ffmpeg"
_FFPROBE = "ffprobe"


def available() -> bool:
    """抽帧能力是否齐备；缺 ffmpeg/ffprobe 时扫描照常，只是不产帧。"""
    return bool(shutil.which(_FFMPEG) and shutil.which(_FFPROBE))


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def probe_duration(path: Path) -> float | None:
    """读容器时长（秒）；探测失败返回 None，调用方按「未知时长」处理。"""
    try:
        done = subprocess.run(
            [shutil.which(_FFPROBE) or _FFPROBE, "-v", "error",
             "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (done.stdout or "").strip().splitlines()
    try:
        value = float(text[0]) if text else 0.0
    except ValueError:
        return None
    return value if value > 0 else None


def extract_frames(path: Path, duration: float | None, interval: float, limit: int) -> list[tuple[float, bytes]]:
    """按固定间隔抽帧，返回 [(时间点秒, JPEG 字节)]，时间点从 0 开始。"""
    if interval <= 0 or limit <= 0:
        return []
    ffmpeg = shutil.which(_FFMPEG)
    if not ffmpeg:
        raise RuntimeError("ffmpeg 不可用")
    with tempfile.TemporaryDirectory(prefix="media-frames-") as tmp:
        pattern = str(Path(tmp) / "f%06d.jpg")
        done = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-i", str(path),
             "-vf", f"fps=1/{interval:g}", "-frames:v", str(limit),
             "-q:v", "3", "-y", pattern],
            capture_output=True, text=True, timeout=300, check=False)
        if done.returncode != 0:
            raise RuntimeError(f"ffmpeg 抽帧失败：{(done.stderr or '').strip()[:200]}")
        files = sorted(Path(tmp).glob("f*.jpg"))
        frames: list[tuple[float, bytes]] = []
        for index, item in enumerate(files):
            if item.stat().st_size == 0:
                continue
            frames.append((round(index * interval, 3), item.read_bytes()))
        return frames


def _frame_dir(root_id: str) -> Path:
    directory = Path(config.FRAMES_DIR) / root_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def frame_file(root_id: str, video_relpath: str, ts: float) -> Path:
    """帧图落盘位置：按视频相对路径散列 + 时间点命名，重抽同时间点直接覆盖。"""
    key = hashlib.sha1(video_relpath.encode("utf-8")).hexdigest()[:16]
    return _frame_dir(root_id) / f"{key}_{int(round(ts)):06d}.jpg"


def scan_videos(root_id: str, entries, cancel=None) -> dict:
    """对本地根下的视频补/更新代表帧，返回本轮计数（失败不抛出）。"""
    counts = {"videos": 0, "videos_indexed": 0, "videos_unchanged": 0, "frames": 0, "failed": 0, "disabled": None}
    if not available():
        counts["disabled"] = "ffmpeg 不可用"
        return counts
    root = config.get_root(root_id)
    if root.get("type") != "local":
        counts["disabled"] = "只对本地媒体根抽帧"
        return counts
    base = Path(root["path"]).resolve()
    errors: list[dict] = []
    for entry in entries:
        if cancel and cancel():
            break
        if Path(entry.relpath).suffix.lower() not in VIDEO_EXTS:
            continue
        counts["videos"] += 1
        try:
            path = (base / entry.relpath).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                raise OSError("视频文件不存在")
            digest = _sha256_file(path)
            record = STORE.video_record(root_id, entry.relpath)
            if record and record["content_sha256"] == digest:
                counts["videos_unchanged"] += 1
                continue
            duration = probe_duration(path)
            samples = extract_frames(path, duration, config.FRAME_INTERVAL, config.FRAME_MAX)
            if not samples:
                raise RuntimeError("抽帧结果为空")
            stored: list[tuple[float, str, bytes]] = []
            for ts, data in samples:
                frame_file(root_id, entry.relpath, ts).write_bytes(data)
                stored.append((ts, hashlib.sha256(data).hexdigest(), encoder.encode_image(data).tobytes()))
            old = STORE.replace_frames(root_id, entry.relpath, digest, duration, stored)
            keep = {float(ts) for ts, _, _ in stored}
            for old_ts, _old_sha in old:
                if float(old_ts) not in keep:
                    try:
                        frame_file(root_id, entry.relpath, old_ts).unlink()
                    except OSError:
                        pass
            counts["videos_indexed"] += 1
            counts["frames"] += len(stored)
        except Exception as exc:  # noqa: BLE001 - 单视频失败不该影响整轮扫描
            counts["failed"] += 1
            errors.append({"path": entry.relpath, "error": str(exc)[:300]})
    if errors:
        counts["errors"] = errors
    return counts
