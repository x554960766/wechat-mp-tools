"""扫描失败不会把已索引图片误判为删除。"""
from io import BytesIO
import numpy as np
from PIL import Image
import pytest

from app import config, media, scan
from app.store import Store


def test_incomplete_listing_preserves_index(monkeypatch, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "test", "type": "local", "path": str(root), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(scan, "STORE", store)
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    store.add_image("test", "a.png", b"image", embedding=vector)
    monkeypatch.setattr(media, "list_entries", lambda root: ([], False))
    with pytest.raises(OSError):
        scan.scan_root("test")
    assert store.stats()["total"] == 1


def test_local_scan_excludes_rejected_and_staging_dirs(monkeypatch, tmp_path):
    root = tmp_path / "root"
    (root / "_rejected").mkdir(parents=True)
    (root / "_manifests").mkdir()
    (root / "article").mkdir()
    for path in (root / "_rejected" / "bad.png", root / "_manifests" / "cover.png", root / "article" / "good.png"):
        path.write_bytes(b"x")
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "r", "type": "local", "path": str(root), "writable": True},))
    entries, complete = media.list_entries("r")
    assert complete and [entry.relpath for entry in entries] == ["article/good.png"]


def test_local_walk_error_aborts_complete_listing(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "r", "type": "local", "path": str(root), "writable": True},))
    def broken_walk(base, onerror, followlinks):
        onerror(PermissionError("denied subdir"))
        yield base, [], []
    monkeypatch.setattr(media.os, "walk", broken_walk)
    with pytest.raises(PermissionError, match="denied subdir"):
        media.list_entries("r")


def test_volume_root_excludes_iphone_subtree(monkeypatch, tmp_path):
    root = tmp_path / "volume"
    (root / "iphone").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "iphone" / "same.png").write_bytes(b"x")
    (root / "other" / "unique.png").write_bytes(b"x")
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "volume", "type": "local", "path": str(root),
                                                  "writable": False, "exclude_paths": ["iphone"]},))
    entries, complete = media.list_entries("volume")
    assert complete and [entry.relpath for entry in entries] == ["other/unique.png"]


def test_incremental_scan_tombstones_missing_files(monkeypatch, tmp_path):
    """源文件消失时增量扫描要标成 deleted，且连续两次都能跑通。

    这里守两个坑：单参数绑定写成裸 int 会让 sqlite3 抛
    ``parameters are of unsupported type``；失败后不回滚会把连接留在事务里，
    下一次扫描直接报 ``cannot start a transaction within a transaction``。
    """
    root = tmp_path / "images"
    (root / "article").mkdir(parents=True)
    png = BytesIO()
    Image.new("RGB", (120, 120), (200, 30, 30)).save(png, format="PNG")
    (root / "article" / "keep.png").write_bytes(png.getvalue())
    (root / "article" / "gone.png").write_bytes(png.getvalue())

    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "test", "type": "local", "path": str(root), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(scan, "STORE", store)
    monkeypatch.setattr(scan.encoder, "encode_image", lambda data: np.eye(512, dtype=np.float32)[0])

    first = scan.scan_root("test")
    assert first["status"] == "completed" and first["indexed_new"] == 2

    (root / "article" / "gone.png").unlink()
    second = scan.scan_root("test")
    assert second["status"] == "completed"
    assert second["deleted"] == 1

    third = scan.scan_root("test")
    assert third["status"] == "completed"
    assert third["deleted"] == 0
    assert store.stats()["total"] == 1

