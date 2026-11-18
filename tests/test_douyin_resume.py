# -*- coding: utf-8 -*-
"""抖音批量下载断点续传逻辑回归测试（无网络，download_file 使用桩替换）"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

from backend import douyin

# 将进度文件与历史记录重定向到临时目录，避免污染真实 data/
# （历史教训：只重定向 PROGRESS_FILE 时，download_media 会把测试文件写进真实 douyin_history.json，
#  在前端归到「未知来源」分组，2026-11-19 实测污染 114 条）
tmp = Path(tempfile.mkdtemp(prefix="dy_resume_test_"))
douyin.PROGRESS_FILE = tmp / "douyin_progress.json"
douyin.HISTORY_FILE = tmp / "douyin_history.json"
# NAS 归档登记用桩替换：测试文件不在媒体根内，本就会被跳过，这里显式短路以防配置变更后被误登记
douyin.nas_archive = SimpleNamespace(submit_path=lambda *args, **kwargs: False)
# 媒体扫描通知同样短路：测试不该真的去敲本机媒体服务
douyin.media_sync = SimpleNamespace(notify_scan=lambda *args, **kwargs: None)

target = tmp / "downloads"
target.mkdir(parents=True, exist_ok=True)

# ── 用桩替换 download_file，模拟网络下载（记录调用次数）──
stub_calls = {"n": 0}


def fake_download_file(url, save_path):
    stub_calls["n"] += 1
    save_path.write_bytes(b"x" * 1024)
    return save_path.stat().st_size


douyin.download_file = fake_download_file

# ── 1. 视频断点续传 ──
video_info = {
    "type": "video", "title": "测试视频", "urls": ["http://example.com/v.mp4"],
    "aweme_id": "7300000000000000001", "nickname": "测试博主",
}
r1 = douyin.download_media(video_info, target, custom_dir=target / "陈杰森资本棋局",
                           skip_existing=True, progress_key="post:sec_test")
assert not r1.get("skipped"), "首次下载不应跳过"
assert stub_calls["n"] == 1

# 同一作品再次批量下载 → 应跳过且不发网络请求
r2 = douyin.download_media(video_info, target, custom_dir=target / "陈杰森资本棋局",
                           skip_existing=True, progress_key="post:sec_test")
assert r2.get("skipped") is True, "已存在的视频应被跳过"
assert stub_calls["n"] == 1, f"跳过时不应有网络请求，实际 stub 调用 {stub_calls['n']} 次"
print("PASS 视频已下载跳过，且进度记录已写入")

# skip_existing=False（单条下载场景）→ 允许覆盖重下
r3 = douyin.download_media(video_info, target, custom_dir=target / "陈杰森资本棋局")
assert not r3.get("skipped") and stub_calls["n"] == 2, "单条下载应保持覆盖重新下载"
print("PASS 单条下载不受影响（覆盖重下）")

# ── 2. 图集部分断点续传 ──
img_info = {
    "type": "image", "title": "测试图集", "urls": [f"http://example.com/{i}.jpeg" for i in range(1, 4)],
    "live_photo_urls": ["", "", ""], "aweme_id": "7300000000000000002",
    "nickname": "测试博主", "music_url": "",
}
stub_calls["n"] = 0
ri1 = douyin.download_media(img_info, target, custom_dir=target / "陈杰森资本棋局",
                            skip_existing=True, progress_key="post:sec_test")
assert stub_calls["n"] == 3 and not ri1.get("skipped")

# 模拟中断：删掉第 3 张后重新批量下载 → 只补下缺失的 1 张
folder = target / "陈杰森资本棋局" / "7300000000000000002_测试图集"
(folder / "3.jpeg").unlink()
stub_calls["n"] = 0
ri2 = douyin.download_media(img_info, target, custom_dir=target / "陈杰森资本棋局",
                            skip_existing=True, progress_key="post:sec_test")
assert stub_calls["n"] == 1, f"图集中断续传应只补下 1 张，实际 {stub_calls['n']} 张"
assert not ri2.get("skipped")
print("PASS 图集中断续传（只补缺失图片）")

# 完整图集再次批量下载 → 整体跳过
stub_calls["n"] = 0
ri3 = douyin.download_media(img_info, target, custom_dir=target / "陈杰森资本棋局",
                            skip_existing=True, progress_key="post:sec_test")
assert ri3.get("skipped") is True and stub_calls["n"] == 0
print("PASS 完整图集整体跳过")

# ── 3. 进度记录持久化 ──
data = json.loads(douyin.PROGRESS_FILE.read_text(encoding="utf-8"))
entry = data.get("post:sec_test") or {}
ids = set(entry.get("downloaded") or [])
assert "7300000000000000001" in ids and "7300000000000000002" in ids, f"进度记录缺失: {ids}"
print("PASS 进度记录持久化:", json.dumps(data, ensure_ascii=False)[:120], "...")

# ── 4. 断点续传日志统计 ──
items = [{"aweme_id": "7300000000000000001"}, {"aweme_id": "7300000000000000002"},
         {"aweme_id": "7300000000000000099"}]
douyin._log_resume_status("测试任务", items, "post:sec_test")
logs = [l for l in douyin._task_state["logs"] if "历史下载进度" in l]
assert logs and "2 个作品已下载过" in logs[-1], f"日志异常: {logs[-1] if logs else '无'}"
print("PASS 断点检测日志:", logs[-1])

# ── 5. 进度记录存在但文件被手动删除 → 应重新下载而非跳过 ──
stub_calls["n"] = 0
(target / "陈杰森资本棋局" / "7300000000000000001_测试视频.mp4").unlink()
rv = douyin.download_media(video_info, target, custom_dir=target / "陈杰森资本棋局",
                           skip_existing=True, progress_key="post:sec_test")
assert stub_calls["n"] == 1 and not rv.get("skipped"), "文件删除后应重新下载"
print("PASS 进度记录存在但文件被删时仍会重新下载（磁盘为准）")

# ── 6. 历史记录去重：同一路径覆盖重下只保留最新一条 ──
dedup_hist = tmp / "douyin_history_dedup.json"
saved_hist = douyin.HISTORY_FILE
douyin.HISTORY_FILE = dedup_hist
dedup_path = target / "陈杰森资本棋局" / "7300000000000000001_测试视频.mp4"
douyin.add_history_item("测试视频", "视频", dedup_path, 1 * 1024 * 1024)
douyin.add_history_item("测试视频", "视频", dedup_path, 2 * 1024 * 1024)
records = json.loads(dedup_hist.read_text(encoding="utf-8"))
assert len(records) == 1, f"同路径应去重为 1 条，实际 {len(records)} 条"
assert records[0]["size"] == "2.00 MB", f"应保留最新记录，实际 {records[0]['size']}"
douyin.HISTORY_FILE = saved_hist
print("PASS 历史记录同路径去重，且保留最新一次")

print("\nALL TESTS PASSED ✅")
