"""模型版本改变时禁止混合已有向量空间。"""
import numpy as np
import pytest
from app.store import Store
from app import config


def test_fingerprint_change_rejected_when_images_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "r", "type": "local", "path": str(tmp_path), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    store.add_image("r", "a.png", b"a", embedding=vector)
    with pytest.raises(RuntimeError, match="encoder_mismatch"):
        store.set_fingerprint("different")
    assert store.stats()["total"] == 1


def test_fingerprint_change_allowed_for_empty_index(tmp_path):
    store = Store(tmp_path / "index.sqlite")
    store.set_fingerprint("different")
    assert store.conn.execute("SELECT encoder_fingerprint FROM index_meta").fetchone()[0] == "different"


def test_adding_read_only_root_keeps_existing_index(monkeypatch, tmp_path):
    initial = {"id": "first", "type": "local", "path": str(tmp_path / "first"), "writable": True}
    monkeypatch.setattr(config, "MEDIA_ROOTS", (initial,))
    store = Store(tmp_path / "index.sqlite")
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    store.add_image("first", "a.png", b"a", embedding=vector)
    second = {"id": "other", "type": "local", "path": str(tmp_path / "other"), "writable": False,
              "exclude_paths": ["iphone"]}
    monkeypatch.setattr(config, "MEDIA_ROOTS", (initial, second))
    reopened = Store(tmp_path / "index.sqlite")
    assert reopened.stats()["total"] == 1
    assert "other" in reopened.conn.execute("SELECT root_bindings_json FROM index_meta").fetchone()[0]
