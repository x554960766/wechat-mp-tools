"""远端上传结果未知时，只有目标摘要一致才能确认。"""
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
