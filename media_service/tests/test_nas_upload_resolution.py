"""远端上传结果未知时，只有目标摘要一致才能确认。"""
import json

import pytest
from app import config, media, nas


def test_unknown_upload_outcome_requires_matching_remote_digest(monkeypatch, tmp_path):
    local = tmp_path / "candidate.png"
    local.write_bytes(b"expected")
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "nas", "type": "zspace", "path": "/archive", "writable": True},))
    monkeypatch.setattr(nas, "ensure_dir", lambda path: False)
    def timeout(*args, **kwargs):
        raise nas.NasError("timeout")
    monkeypatch.setattr(nas, "upload_file", timeout)
    monkeypatch.setattr(media, "read_bytes", lambda root, relpath: b"expected")
    assert media.write_new("nas", "article/x.png", local)["status"] == "skipped"
    monkeypatch.setattr(media, "read_bytes", lambda root, relpath: b"different")
    with pytest.raises(RuntimeError, match="target_conflict"):
        media.write_new("nas", "article/x.png", local)


def test_delete_remote_file_is_permanent_and_confirmed(monkeypatch):
    """永久删除必须传 skip_bin=1 与 --yes，避免误进回收站后仍占空间。"""
    calls = {}

    def fake_runner(args):
        calls["args"] = args
        return {"deleted": 1}

    out = nas.delete_remote_file("/archive/a.png", runner=fake_runner)

    assert out == {"deleted": 1}
    assert calls["args"][0:2] == ["file", "delete"]
    params = json.loads(calls["args"][calls["args"].index("--params") + 1])
    assert params == {"paths": ["/archive/a.png"], "skip_bin": "1"}
    assert "--yes" in calls["args"]


def test_delete_entry_zspace_targets_remote_path(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "nas", "type": "zspace", "path": "/archive", "writable": True},))
    seen = {}
    monkeypatch.setattr(nas, "delete_remote_file", lambda path, *, runner=None: seen.setdefault("path", path))

    out = media.delete_entry("nas", "article/a.png")

    assert out == {"existed": True, "root": "nas", "relpath": "article/a.png"}
    assert seen["path"] == "/archive/article/a.png"


def test_delete_entry_rejects_readonly_root(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "ro", "type": "local", "path": "/x", "writable": False},))
    with pytest.raises(PermissionError):
        media.delete_entry("ro", "a.png")

