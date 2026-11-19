"""作品级归档状态机：固定本机素材、不访问真实 NAS。

覆盖 Spec §4.2 七态流转与清理不变量：只有「作品全部文件及内容摘要与本次清单一致、
NAS 校验结果已持久化、本地文件仍属同一版本」时才允许清理本地。
"""
import hashlib
import io
import json

from PIL import Image

from app import config, media, work_archive


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _setup(monkeypatch, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    monkeypatch.setattr(
        config,
        "MEDIA_ROOTS",
        (
            {"id": "src-root", "type": "local", "path": str(src), "writable": False},
            {"id": "dst-root", "type": "local", "path": str(dst), "writable": True},
        ),
    )
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(work_archive, "_save", lambda job: None)
    return src, dst


def _write(src, rel: str, content: bytes) -> tuple[str, int]:
    path = src / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _digest(content), path.stat().st_size


def _work(source_root: str, files: list[dict]) -> dict:
    return {"id": "work-1", "platform": "wechat", "source_root": source_root, "files": files}


def _job(target_root: str, target_path: str, works: list[dict], target_dir: str = "archive") -> dict:
    return {
        "id": "job-1",
        "target_root": target_root,
        "target_path": target_path,
        "target_dir": target_dir,
        "works": works,
    }


def test_full_flow_archives_verifies_and_cleans(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"hello world")
    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])

    work_archive.run_work_archive(job)
    work = job["works"][0]
    assert job["status"] == "completed"
    assert work["status"] == work_archive.ARCHIVED
    assert work["cleaned"] is True
    # 目标完整持有同版本内容，且清单已持久化
    assert (dst / "archive" / "work-1" / "body.txt").read_bytes() == b"hello world"
    assert job.get("manifest_path") == "archive/_manifests/job-1.json"
    # 不变量成立后才清理本地
    assert not (src / "body.txt").exists()


def test_target_already_same_version_is_verified_and_cleans(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    content = b"hello world"
    digest, size = _write(src, "body.txt", content)
    target = dst / "archive" / "work-1" / "body.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    work_archive.run_work_archive(job)
    work = job["works"][0]
    assert work["status"] == work_archive.ARCHIVED
    assert (dst / "archive" / "work-1" / "body.txt").read_bytes() == content
    assert not (src / "body.txt").exists()


def test_target_conflict_keeps_both_sides(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"original")
    target = dst / "archive" / "work-1" / "body.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"different content")

    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    work_archive.run_work_archive(job)
    work = job["works"][0]
    assert work["status"] == work_archive.CONFLICT
    assert job["status"] == "failed"
    assert (src / "body.txt").read_bytes() == b"original"
    assert target.read_bytes() == b"different content"


def test_source_changed_after_manifest_keeps_local(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"v1")
    (src / "body.txt").write_bytes(b"v2")

    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    work_archive.run_work_archive(job)
    work = job["works"][0]
    assert work["status"] == work_archive.FAILED
    assert job["status"] == "failed"
    assert (src / "body.txt").read_bytes() == b"v2"


def test_nas_unreachable_fails_retryable_and_keeps_local(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"hello")

    def boom(root_id, relpath, local_file):
        raise RuntimeError("NasError: 连接失败")

    monkeypatch.setattr(media, "write_new", boom)
    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    work_archive.run_work_archive(job)
    work = job["works"][0]
    assert work["status"] == work_archive.FAILED
    assert (src / "body.txt").exists()


def test_illegal_relpath_fails_work_and_keeps_local(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    # 越界相对路径在读取前即被拒绝，本地不落任何写入
    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "../evil.txt", "digest": _digest(b"x"), "size": 1}])])
    work_archive.run_work_archive(job)
    work = job["works"][0]
    assert work["status"] == work_archive.FAILED
    assert not (dst / "archive" / "work-1" / ".." / "evil.txt").exists()


def test_cleanup_interrupted_stays_pending_cleanup(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"hello")

    def blocking_remove(path):
        raise OSError("disk busy")

    monkeypatch.setattr(work_archive.os, "remove", blocking_remove)
    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    work_archive.run_work_archive(job)
    work = job["works"][0]
    assert work["status"] == work_archive.PENDING_CLEANUP
    assert work["cleanup_blocked"]
    assert (src / "body.txt").exists()
    assert (dst / "archive" / "work-1" / "body.txt").read_bytes() == b"hello"


def test_retry_rechecks_target_and_keeps_local(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"v1")
    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    work_archive.run_work_archive(job)
    assert job["works"][0]["status"] == work_archive.ARCHIVED

    work = job["works"][0]
    work["status"] = work_archive.PENDING_CLEANUP
    work["manifest_confirmed"] = True
    work["cleaned"] = False
    (src / "body.txt").write_bytes(b"v1")  # 本地仍是本次清单版本
    (dst / "archive" / "work-1" / "body.txt").unlink()  # 目标已失效

    work_archive.run_work_archive(job)
    assert work["status"] == work_archive.PENDING_CLEANUP
    assert any("目标已失效" in item["reason"] for item in work["cleanup_blocked"])
    assert (src / "body.txt").read_bytes() == b"v1"


def test_cleanup_blocked_when_local_content_changed(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"v1")
    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    work_archive.run_work_archive(job)

    work = job["works"][0]
    work["status"] = work_archive.PENDING_CLEANUP
    work["manifest_confirmed"] = True
    work["cleaned"] = False
    (src / "body.txt").write_bytes(b"retouched")  # 本地已变化

    work_archive.run_work_archive(job)
    assert work["status"] == work_archive.PENDING_CLEANUP
    assert any("本地内容已变化" in item["reason"] for item in work["cleanup_blocked"])
    assert (src / "body.txt").read_bytes() == b"retouched"


def test_target_root_path_binding_is_enforced(monkeypatch, tmp_path):
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"hello")
    job = _job("dst-root", "/some/other/path", [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    try:
        work_archive.run_work_archive(job)
    except RuntimeError as exc:
        assert "目标 root 配置路径已变化" in str(exc)
    else:
        raise AssertionError("目标 root 配置路径变化时必须拒绝写入")


def test_cleanup_disabled_keeps_local_files(monkeypatch, tmp_path):
    """cleanup=False（下载即自动归档）：NAS 校验通过后仍保留本地文件。"""
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "body.txt", b"keep me")
    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "body.txt", "digest": digest, "size": size}])])
    job["cleanup"] = False

    work_archive.run_work_archive(job)
    work = job["works"][0]
    assert job["status"] == "completed"
    assert work["status"] == work_archive.ARCHIVED
    assert work["local_retained"] is True
    assert work.get("cleaned") is not True
    # 目标已有同版本内容，本地原文件必须还在
    assert (dst / "archive" / "work-1" / "body.txt").read_bytes() == b"keep me"
    assert (src / "body.txt").read_bytes() == b"keep me"
    assert job.get("manifest_path") == "archive/_manifests/job-1.json"


# ---------------------------------------------------------------------------
# 归档前图片筛选（filter.py 接入 work_archive）：被筛图不写 NAS、不入清单、本地保留
# ---------------------------------------------------------------------------


def _png_bytes(size=(300, 300), sigma=40) -> bytes:
    """生成一张能通过筛选的噪声 PNG（尺寸/面积/长宽比/对比度/透明度都过关）。"""
    buf = io.BytesIO()
    Image.effect_noise(size, sigma).save(buf, format="PNG")
    return buf.getvalue()


def _tiny_png_bytes() -> bytes:
    """100x100 缩略图，触发 filter 的 tiny 判定。"""
    return _png_bytes(size=(100, 100), sigma=40)


def test_all_images_filtered_fails_work_and_keeps_local(monkeypatch, tmp_path):
    """作品内文件全被筛掉：判 FAILED，本地保留，不写 NAS、不发清单。"""
    src, dst = _setup(monkeypatch, tmp_path)
    digest, size = _write(src, "tiny.png", _tiny_png_bytes())

    job = _job("dst-root", str(dst), [_work("src-root", [{"relpath": "tiny.png", "digest": digest, "size": size}])])
    work_archive.run_work_archive(job)
    work = job["works"][0]

    assert work["status"] == work_archive.FAILED
    assert work.get("filtered") == [{"relpath": "tiny.png", "reason": "tiny"}]
    assert job["status"] == "failed"
    # 本地保留，不写 NAS，未发清单
    assert (src / "tiny.png").exists()
    assert not (dst / "archive" / "work-1" / "tiny.png").exists()
    assert not job.get("manifest_path")


def test_filtered_image_skipped_from_manifest_and_cleanup(monkeypatch, tmp_path):
    """保留图正常归档清理，被筛图不写 NAS、不入清单、本地保留。"""
    src, dst = _setup(monkeypatch, tmp_path)
    good_digest, good_size = _write(src, "good.png", _png_bytes())
    tiny_digest, tiny_size = _write(src, "tiny.png", _tiny_png_bytes())

    job = _job("dst-root", str(dst), [_work("src-root", [
        {"relpath": "good.png", "digest": good_digest, "size": good_size},
        {"relpath": "tiny.png", "digest": tiny_digest, "size": tiny_size},
    ])])
    work_archive.run_work_archive(job)
    work = job["works"][0]

    assert work["status"] == work_archive.ARCHIVED
    assert work["filtered"] == [{"relpath": "tiny.png", "reason": "tiny"}]
    # 保留图已归档并清理本地
    assert (dst / "archive" / "work-1" / "good.png").exists()
    assert not (src / "good.png").exists()
    # 被筛图不写 NAS、本地保留
    assert not (dst / "archive" / "work-1" / "tiny.png").exists()
    assert (src / "tiny.png").exists()
    # 清单只含真正写入 NAS 的保留图
    manifest = json.loads((dst / "archive" / "_manifests" / "job-1.json").read_text(encoding="utf-8"))
    assert {f["relpath"] for f in manifest["files"]} == {"archive/work-1/good.png"}


def test_non_image_files_are_not_filtered(monkeypatch, tmp_path):
    """正文/字幕等非图片不受筛选影响，照常归档清理。"""
    src, dst = _setup(monkeypatch, tmp_path)
    body_digest, body_size = _write(src, "body.txt", b"hello")
    tiny_digest, tiny_size = _write(src, "tiny.png", _tiny_png_bytes())

    job = _job("dst-root", str(dst), [_work("src-root", [
        {"relpath": "body.txt", "digest": body_digest, "size": body_size},
        {"relpath": "tiny.png", "digest": tiny_digest, "size": tiny_size},
    ])])
    work_archive.run_work_archive(job)
    work = job["works"][0]

    assert work["status"] == work_archive.ARCHIVED
    assert work["filtered"] == [{"relpath": "tiny.png", "reason": "tiny"}]
    # 正文照常归档并清理
    assert (dst / "archive" / "work-1" / "body.txt").read_bytes() == b"hello"
    assert not (src / "body.txt").exists()
    # 被筛图本地保留
    assert (src / "tiny.png").exists()


def test_auto_archive_keeps_filtered_and_archived_images_local(monkeypatch, tmp_path):
    """cleanup=False（下载即自动归档）：保留图与被筛图本地都保留。"""
    src, dst = _setup(monkeypatch, tmp_path)
    good_digest, good_size = _write(src, "good.png", _png_bytes())
    tiny_digest, tiny_size = _write(src, "tiny.png", _tiny_png_bytes())

    job = _job("dst-root", str(dst), [_work("src-root", [
        {"relpath": "good.png", "digest": good_digest, "size": good_size},
        {"relpath": "tiny.png", "digest": tiny_digest, "size": tiny_size},
    ])])
    job["cleanup"] = False
    work_archive.run_work_archive(job)
    work = job["works"][0]

    assert job["status"] == "completed"
    assert work["status"] == work_archive.ARCHIVED
    assert work["local_retained"] is True
    # 保留图与被筛图本地都保留，被筛图不写 NAS
    assert (src / "good.png").exists()
    assert (src / "tiny.png").exists()
    assert not (dst / "archive" / "work-1" / "tiny.png").exists()
