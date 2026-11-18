"""视频代表帧：抽帧、入库幂等、内容变化重建、远端根跳过、坏视频不影响他人。"""
import pathlib
import shutil
import subprocess

import numpy as np
import pytest

from app import config, encoder, frames
from app.store import Store

pytestmark = pytest.mark.skipif(not frames.available(), reason="本机没有 ffmpeg/ffprobe")


def _vector(seed=0):
    vec = np.zeros(512, dtype=np.float32)
    vec[seed % 512] = 1.0
    return vec


def _make_video(path: pathlib.Path, seconds: int = 3, size: str = "64x48"):
    """用 ffmpeg 自己造一段测试视频（testsrc 有渐变与时间戳，抽帧不会全黑）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=10",
         "-pix_fmt", "yuv420p", "-y", str(path)],
        capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    return path


def _setup(monkeypatch, tmp_path, root_type="local"):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": "r", "type": root_type, "path": str(root), "writable": True},))
    monkeypatch.setattr(config, "FRAMES_DIR", tmp_path / "frames")
    monkeypatch.setattr(config, "FRAME_INTERVAL", 1.0)
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(frames, "STORE", store)
    monkeypatch.setattr(encoder, "encode_image", lambda data: _vector(len(data)))
    return root, store


def _entry(relpath):
    class Entry:
        def __init__(self, relpath):
            self.relpath = relpath
            self.size = 0
            self.mtime = 0.0

    return Entry(relpath)


def test_extract_frames_follows_interval(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", seconds=3)
    duration = frames.probe_duration(video)
    assert duration and duration >= 2.5
    samples = frames.extract_frames(video, duration, 1.0, 10)
    assert len(samples) == 3
    assert [ts for ts, _ in samples] == [0.0, 1.0, 2.0]
    assert all(data.startswith(b"\xff\xd8") for _, data in samples)  # JPEG 魔数


def test_extract_frames_respects_limit(tmp_path):
    video = _make_video(tmp_path / "clip.mp4", seconds=4)
    samples = frames.extract_frames(video, 4.0, 1.0, 2)
    assert len(samples) == 2


def test_extract_frames_short_video_shorter_than_interval(tmp_path):
    """时长不足一个采样间隔时也要出首帧，否则短视频永远进不了检索。"""
    video = _make_video(tmp_path / "short.mp4", seconds=2)
    duration = frames.probe_duration(video)
    assert duration and duration < 10.0
    samples = frames.extract_frames(video, duration, 10.0, 10)
    assert len(samples) == 1
    assert samples[0][0] == 0.0


def test_scan_videos_persists_then_skips_unchanged(tmp_path, monkeypatch):
    root, store = _setup(monkeypatch, tmp_path)
    video = _make_video(root / "作者" / "作品" / "clip.mp4", seconds=3)

    first = frames.scan_videos("r", [_entry(video.relative_to(root).as_posix())])
    assert first["videos"] == 1 and first["videos_indexed"] == 1 and first["failed"] == 0
    assert first["frames"] == 3
    record = store.video_record("r", "作者/作品/clip.mp4")
    assert record["frame_count"] == 3 and record["duration"] >= 2.5
    rows = store.conn.execute("SELECT ts_seconds,emb FROM video_frames ORDER BY ts_seconds").fetchall()
    assert [row["ts_seconds"] for row in rows] == [0.0, 1.0, 2.0]
    assert all(row["emb"] for row in rows)
    saved = list((tmp_path / "frames" / "r").glob("*.jpg"))
    assert len(saved) == 3

    # 同样的文件再扫一次：摘要没变，一帧都不重抽
    second = frames.scan_videos("r", [_entry("作者/作品/clip.mp4")])
    assert second["videos_unchanged"] == 1
    assert second["videos_indexed"] == 0 and second["frames"] == 0


def test_scan_videos_rebuilds_when_content_changes(tmp_path, monkeypatch):
    root, store = _setup(monkeypatch, tmp_path)
    relpath = "clip.mp4"
    _make_video(root / relpath, seconds=3)
    frames.scan_videos("r", [_entry(relpath)])
    first_sha = store.video_record("r", relpath)["content_sha256"]

    _make_video(root / relpath, seconds=5)
    after = frames.scan_videos("r", [_entry(relpath)])
    assert after["videos_indexed"] == 1 and after["frames"] == 5
    assert store.video_record("r", relpath)["content_sha256"] != first_sha
    # 旧帧（0/1/2 秒）被同批新帧（0..4 秒）覆盖或删除，库里只剩新版本的 5 帧
    rows = store.conn.execute("SELECT ts_seconds FROM video_frames ORDER BY ts_seconds").fetchall()
    assert [row["ts_seconds"] for row in rows] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert len(list((tmp_path / "frames" / "r").glob("*.jpg"))) == 5


def test_scan_videos_skips_remote_root(tmp_path, monkeypatch):
    _root, store = _setup(monkeypatch, tmp_path, root_type="zspace")
    counts = frames.scan_videos("r", [_entry("clip.mp4")])
    assert counts["videos"] == 0 and counts["disabled"]
    assert store.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0


def test_scan_videos_keeps_going_after_broken_file(tmp_path, monkeypatch):
    root, store = _setup(monkeypatch, tmp_path)
    (root / "broken.mp4").write_bytes(b"not a video at all")
    _make_video(root / "good.mp4", seconds=2)
    counts = frames.scan_videos("r", [_entry("broken.mp4"), _entry("good.mp4")])
    assert counts["failed"] == 1
    assert counts["videos_indexed"] == 1
    assert counts["errors"][0]["path"] == "broken.mp4"
    assert store.video_record("r", "good.mp4") is not None
    assert store.video_record("r", "broken.mp4") is None


def test_frame_snapshot_tracks_rows(tmp_path, monkeypatch):
    root, store = _setup(monkeypatch, tmp_path)
    _make_video(root / "clip.mp4", seconds=2)
    frames.scan_videos("r", [_entry("clip.mp4")])
    records, matrix = store.frame_snapshot()
    assert len(records) == 2
    assert matrix.shape == (2, 512)
    assert matrix.flags.writeable is False

    store.drop_frames("r")
    records, matrix = store.frame_snapshot()
    assert records == [] and matrix.shape == (0, 512)
