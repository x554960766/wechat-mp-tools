"""本机归档主链路：固定浏览器素材、不访问真实微信或 NAS。"""
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from app import archive_runner, config, media
from app.store import Store


def test_archive_content_identity_and_index(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "local-test", "type": "local", "path": str(root), "writable": True},))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(archive_runner, "STORE", store)
    monkeypatch.setattr("app.encoder.encode_image", lambda _: np.eye(1, 512, dtype=np.float32)[0])
    @contextmanager
    def browser():
        yield object(), object()
    monkeypatch.setattr(archive_runner, "open_browser", browser)
    monkeypatch.setattr(archive_runner, "capture", lambda page, url: {"status": "ok", "title": "文章", "contentImgs": ["a", "b"]})
    picture = Image.new("RGB", (400, 400), "red")
    out = BytesIO()
    picture.save(out, "PNG")
    monkeypatch.setattr(archive_runner, "download_image", lambda ctx, url: out.getvalue())
    monkeypatch.setattr(archive_runner, "judge", lambda info, seen: "dup" if seen else "")
    monkeypatch.setattr(archive_runner, "_save", lambda job: None)
    job = {"id": "job1", "target_root": "local-test", "target_path": str(root), "target_dir": "", "urls": ["https://mp.weixin.qq.com/s/a"], "articles": []}
    archive_runner.run_archive(job)
    assert job["status"] == "completed"
    assert len(job["articles"][0]["files"]) == 1
    assert store.stats()["total"] == 1
    archive_runner.run_archive(job)
    assert store.stats()["total"] == 1


def test_retry_first_download_failure_keeps_two_distinct_images(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "local-test", "type": "local", "path": str(root), "writable": True},))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(archive_runner, "STORE", store)
    monkeypatch.setattr("app.encoder.encode_image", lambda _: np.eye(1, 512, dtype=np.float32)[0])
    @contextmanager
    def browser():
        yield object(), object()
    monkeypatch.setattr(archive_runner, "open_browser", browser)
    monkeypatch.setattr(archive_runner, "capture", lambda page, url: {"status": "ok", "title": "文章", "contentImgs": ["a", "b"]})
    def picture(color):
        image = Image.new("RGB", (400, 400), color)
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue()
    downloads = {"a": None, "b": picture("blue")}
    monkeypatch.setattr(archive_runner, "download_image", lambda ctx, url: downloads[url])
    monkeypatch.setattr(archive_runner, "judge", lambda info, seen: "")
    monkeypatch.setattr(archive_runner, "_save", lambda job: None)
    job = {"id": "job2", "target_root": "local-test", "target_path": str(root), "target_dir": "", "urls": ["https://mp.weixin.qq.com/s/a"], "articles": []}
    archive_runner.run_archive(job)
    assert job["status"] == "partial" and store.stats()["total"] == 1
    downloads["a"] = picture("red")
    import copy
    resumed = {**job, "id": "job2-retry", "articles": copy.deepcopy(job["articles"]), "errors": [], "parent_id": job["id"]}
    archive_runner.run_archive(resumed)
    assert resumed["status"] == "completed" and store.stats()["total"] == 2
    assert len({f["sha256"] for f in resumed["articles"][0]["files"]}) == 2


def test_existing_target_with_wrong_bytes_is_not_indexed(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "local-test", "type": "local", "path": str(root), "writable": True},))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(archive_runner, "STORE", store)
    monkeypatch.setattr("app.encoder.encode_image", lambda _: np.eye(1, 512, dtype=np.float32)[0])
    @contextmanager
    def browser():
        yield object(), object()
    monkeypatch.setattr(archive_runner, "open_browser", browser)
    monkeypatch.setattr(archive_runner, "capture", lambda page, url: {"status": "ok", "title": "文章", "contentImgs": ["a"]})
    picture = Image.new("RGB", (400, 400), "green")
    out = BytesIO()
    picture.save(out, "PNG")
    monkeypatch.setattr(archive_runner, "download_image", lambda ctx, url: out.getvalue())
    monkeypatch.setattr(archive_runner, "judge", lambda info, seen: "")
    monkeypatch.setattr(archive_runner, "_save", lambda job: None)
    from app.archive import article_id, to_png
    from hashlib import sha256
    aid = article_id("https://mp.weixin.qq.com/s/a")
    digest = sha256(to_png(out.getvalue())).hexdigest()
    target = root / aid / (digest + ".png")
    target.parent.mkdir()
    target.write_bytes(b"wrong")
    job = {"id": "job3", "target_root": "local-test", "target_path": str(root), "target_dir": "", "urls": ["https://mp.weixin.qq.com/s/a"], "articles": []}
    archive_runner.run_archive(job)
    assert job["status"] == "partial"
    assert "target_conflict" in str(job["articles"][0]["errors"])
    assert target.read_bytes() == b"wrong"
    assert store.stats()["total"] == 0


def test_normal_image_filter_does_not_mark_archive_partial(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "local-test", "type": "local", "path": str(root), "writable": True},))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    @contextmanager
    def browser():
        yield object(), object()
    monkeypatch.setattr(archive_runner, "open_browser", browser)
    monkeypatch.setattr(archive_runner, "capture", lambda page, url: {"status": "ok", "title": "文章", "contentImgs": ["small"]})
    picture = Image.new("RGB", (40, 40), "blue")
    out = BytesIO()
    picture.save(out, "PNG")
    monkeypatch.setattr(archive_runner, "download_image", lambda ctx, url: out.getvalue())
    monkeypatch.setattr(archive_runner, "_save", lambda job: None)
    job = {"id": "job4", "target_root": "local-test", "target_path": str(root), "target_dir": "", "urls": ["https://mp.weixin.qq.com/s/a"], "articles": []}
    archive_runner.run_archive(job)
    assert job["status"] == "completed"
    assert job["articles"][0]["errors"] == []
    assert [x["reason"] for x in job["articles"][0]["filtered"]] == ["tiny"]


def test_browser_gone_mid_batch_restarts_and_finishes(monkeypatch, tmp_path):
    """浏览器在批次中途被回收时，重建并续跑，不把剩余篇数带崩。"""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "local-test", "type": "local", "path": str(root), "writable": True},))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(archive_runner, "STORE", store)
    monkeypatch.setattr("app.encoder.encode_image", lambda _: np.eye(1, 512, dtype=np.float32)[0])

    generations = {"n": 0}

    @contextmanager
    def browser():
        # 每次 open_browser 开一代新浏览器，用 page 上的 gen 区分
        generations["n"] += 1
        yield object(), {"gen": generations["n"]}

    def capture(page, url):
        # 第一代浏览器访问第一篇时“被回收”
        if page["gen"] == 1 and url == "https://mp.weixin.qq.com/s/a":
            raise RuntimeError("Page.goto: Target page, context or browser has been closed")
        return {"status": "ok", "title": "文章", "contentImgs": ["img-" + url[-1]]}

    def picture(color):
        image = Image.new("RGB", (400, 400), color)
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    monkeypatch.setattr(archive_runner, "open_browser", browser)
    monkeypatch.setattr(archive_runner, "capture", capture)
    monkeypatch.setattr(archive_runner, "download_image", lambda ctx, url: picture("blue"))
    monkeypatch.setattr(archive_runner, "judge", lambda info, seen: "")
    monkeypatch.setattr(archive_runner, "_save", lambda job: None)

    job = {"id": "job-restart", "target_root": "local-test", "target_path": str(root),
           "target_dir": "", "urls": ["https://mp.weixin.qq.com/s/a", "https://mp.weixin.qq.com/s/b"],
           "articles": []}
    archive_runner.run_archive(job)
    # 浏览器只应该重启一次，就能把 a、b 两篇都做完
    assert generations["n"] == 2
    assert job["status"] == "completed"
    assert all(article["status"] == "completed" for article in job["articles"])
    assert store.stats()["total"] == 2
