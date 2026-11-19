"""NAS/媒体根离线时，已生成的预览仍可读取。"""
from io import BytesIO
import numpy as np
from PIL import Image

from app import config, thumbs
from app.store import Store


def test_cached_thumbnail_survives_source_disappearance(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "local", "type": "local", "path": str(root), "writable": False},))
    monkeypatch.setattr(config, "THUMB_DIR", tmp_path / "thumbs")
    source = root / "photo.png"
    image = Image.new("RGB", (500, 300), "blue")
    buf = BytesIO()
    image.save(buf, "PNG")
    source.write_bytes(buf.getvalue())
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr("app.store.STORE", store)
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    row = store.add_image("local", "photo.png", source.read_bytes(), size=source.stat().st_size,
                          mtime=source.stat().st_mtime, embedding=vector)
    preview, cached = thumbs.thumbnail("local", "photo.png", row["content_version"])
    assert preview and not cached
    source.unlink()
    again, cached = thumbs.thumbnail("local", "photo.png", row["content_version"])
    assert again == preview and cached
