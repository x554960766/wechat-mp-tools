"""media_host：媒体根继承、查询串转发、响应头过滤。

这三处都踩过坑，回归测试防止再退化：

- 宿主只注入 articles-full 会覆盖旧索引的根绑定，媒体服务直接
  ``root_binding_mismatch`` 起不来（旧索引整库作废）。
- 反代丢 query string，``/media/api/media/thumb?...&version=...`` 变 422。
- uvicorn 响应头是小写，按 ``Content-Type`` 精确匹配会把头全丢掉，
  浏览器把图片当 text/html。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import media_host as mh  # noqa: E402


def _write_index(db_path: Path, bindings: dict) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE index_meta (id INTEGER PRIMARY KEY, generation INTEGER, "
            "encoder_fingerprint TEXT, encoder_spec_json TEXT, root_bindings_json TEXT)"
        )
        conn.execute(
            "INSERT INTO index_meta VALUES (1, 0, 'fp', '{}', ?)",
            (json.dumps(bindings),),
        )
        conn.commit()


def test_media_root_env_inherits_indexed_roots(tmp_path):
    data_dir = tmp_path / "media"
    _write_index(
        data_dir / "index.sqlite",
        {
            "wx-archive": {"type": "zspace", "path": "/sata11/my/data/公众号归档", "exclude_paths": []},
            "iphone": {"type": "local", "path": "/Volumes/sata11/iphone", "exclude_paths": []},
        },
    )
    host = mh.MediaHost(port=9999, media_root=str(tmp_path), data_dir=str(data_dir))
    roots = {r["id"]: r for r in host._media_root_env()}

    # 旧索引绑定的根必须原样继承，否则 set_fingerprint 会拒绝启动
    assert roots["wx-archive"]["path"] == "/sata11/my/data/公众号归档"
    assert roots["wx-archive"]["type"] == "zspace"
    assert roots["wx-archive"]["writable"] is True
    assert roots["iphone"]["type"] == "local"
    # 无论旧索引如何，落图目录总是要有
    assert roots["articles-full"]["path"].endswith("data/articles_full")


def test_media_root_env_configured_roots_override(tmp_path):
    _write_index(tmp_path / "media" / "index.sqlite", {"wx-archive": {"type": "zspace", "path": "/old", "exclude_paths": []}})
    host = mh.MediaHost(port=9999, media_root=str(tmp_path), data_dir=str(tmp_path / "media"))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv(
        "AI_MEDIA_ROOTS",
        json.dumps([{"id": "wx-archive", "type": "local", "path": "/new"}], ensure_ascii=False),
    )
    try:
        roots = {r["id"]: r for r in host._media_root_env()}
    finally:
        monkeypatch.undo()
    assert roots["wx-archive"]["path"] == "/new"


def test_media_root_env_without_index(tmp_path):
    host = mh.MediaHost(port=9999, media_root=str(tmp_path), data_dir=str(tmp_path / "media"))
    roots = {r["id"]: r for r in host._media_root_env()}
    # 没有旧索引时：下载落图根 + NAS 归档根（作品归档固定写 wx-archive）
    assert list(roots) == ["articles-full", "wx-archive"]
    assert roots["wx-archive"]["type"] == "zspace"
    assert roots["wx-archive"]["writable"] is True
    # 本地下载落盘根由本应用管理，删除重复件要能落物理删除，必须可写
    assert roots["articles-full"]["type"] == "local"
    assert roots["articles-full"]["writable"] is True


def test_media_root_env_local_download_roots_are_writable(tmp_path):
    host = mh.MediaHost(port=9999, media_root=str(tmp_path), data_dir=str(tmp_path / "media"))
    # `_media_root_env` 里 repo_root 是 media_root 的父目录，下载目录挂在 repo_root/data/<subdir>
    repo_root = tmp_path.parent
    for subdir in ("douyin_downloads", "kuaishou_downloads", "bilibili_downloads", "xhs_downloads"):
        (repo_root / "data" / subdir).mkdir(parents=True, exist_ok=True)
    roots = {r["id"]: r for r in host._media_root_env()}
    for root_id in ("articles-full", "douyin", "kuaishou", "bilibili", "xhs"):
        assert roots[root_id]["type"] == "local"
        assert roots[root_id]["writable"] is True, f"{root_id} 应可写，否则重复件删除按钮不出现"


def test_media_root_env_nas_root_env_override(tmp_path):
    host = mh.MediaHost(port=9999, media_root=str(tmp_path), data_dir=str(tmp_path / "media"))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("WX_NAS_ROOT", "/sata99/custom/归档")
    try:
        roots = {r["id"]: r for r in host._media_root_env()}
    finally:
        monkeypatch.undo()
    assert roots["wx-archive"]["path"] == "/sata99/custom/归档"


def _orphan_host(tmp_path, *, healthy: bool, port_open: bool, killed: list, pid: int = 424242):
    host = mh.MediaHost(port=9998, media_root=str(tmp_path), data_dir=str(tmp_path / "media"))
    monkeypatch_targets = {
        "_read_pid_file": lambda: pid,
        "_pid_alive": lambda p: True,
        "_is_healthy": lambda: healthy,
        "_port_open": lambda: port_open,
        "_terminate_pid": lambda p: killed.append(p),
    }
    for name, fn in monkeypatch_targets.items():
        setattr(host, name, fn)
    return host


def test_adopt_orphan_does_not_kill_reused_pid(tmp_path):
    """PID 被系统复用给无关进程时，端口空着就不能按 pid 直接 kill。"""
    killed: list = []
    host = _orphan_host(tmp_path, healthy=False, port_open=False, killed=killed)
    assert host._adopt_orphan() is False
    assert killed == []


def test_adopt_orphan_reclaims_hung_process_holding_port(tmp_path):
    """端口仍被占着且健康检查失败：判定为卡死的媒体子进程，可以回收。"""
    killed: list = []
    host = _orphan_host(tmp_path, healthy=False, port_open=True, killed=killed)
    assert host._adopt_orphan() is False
    assert killed == [424242]


def test_adopt_orphan_reuses_healthy_process(tmp_path):
    killed: list = []
    host = _orphan_host(tmp_path, healthy=True, port_open=True, killed=killed)
    assert host._adopt_orphan() is True
    assert killed == []
    assert host._external_pid == 424242
    assert host._state == "running"


def test_filter_resp_headers_is_case_insensitive():
    headers = {
        "content-type": "image/webp",
        "content-length": "1078",
        "accept-ranges": "bytes",
        "server": "uvicorn",
        "x-other": "1",
    }
    out = mh._filter_resp_headers(headers)
    assert out["content-type"] == "image/webp"
    assert out["accept-ranges"] == "bytes"
    assert "server" not in out and "x-other" not in out


class _FakeResp:
    def __init__(self, status: int = 200, content: bytes = b"ok", headers: dict | None = None):
        self.status_code = status
        self.content = content
        self.headers = headers or {"content-type": "image/webp"}


def _build_client(monkeypatch, captured: dict):
    from flask import Flask

    host = mh.MediaHost(port=9999, media_root="/tmp", data_dir="/tmp")
    host._state = "running"
    host._healthy = True

    def fake_request(method, url, **kwargs):
        captured["url"] = url
        return _FakeResp()

    monkeypatch.setattr(mh.requests, "request", fake_request)

    app = Flask(__name__)
    app.register_blueprint(mh.init_blueprint(app, host=host))
    return app.test_client()


def test_proxy_forwards_query_string(monkeypatch):
    captured: dict = {}
    client = _build_client(monkeypatch, captured)
    resp = client.get("/media/api/media/thumb?root=articles-full&relpath=a/b.jpg&version=v1&size=360")
    assert resp.status_code == 200
    assert "version=v1" in captured["url"]
    assert "relpath=a%2Fb.jpg" in captured["url"] or "relpath=a/b.jpg" in captured["url"]
    assert resp.headers["Content-Type"].startswith("image/webp")


def test_wait_ready_accepts_healthy_port_owner_without_handle(tmp_path):
    """认领不到 pid、也没有 Popen 句柄时，端口上健康就算就绪。

    回归：_adopt_orphan 复用既有健康服务后 _external_pid 仍为空，旧
    _wait_ready 会立刻返回 False，导致「bind 失败 → 复用 → 判未就绪 → 再
    spawn」死循环，媒体状态永久在 starting/running 之间抖动。
    """
    host = mh.MediaHost(port=9997, media_root=str(tmp_path), data_dir=str(tmp_path / "media"))
    host._is_healthy = lambda: True
    host._external_pid = None
    assert host._wait_ready(timeout=0.5) is True
    assert host._state == "running"
    assert host._healthy is True


def test_wait_ready_reports_not_ready_without_handle_or_health(tmp_path):
    host = mh.MediaHost(port=9996, media_root=str(tmp_path), data_dir=str(tmp_path / "media"))
    host._is_healthy = lambda: False
    host._external_pid = None
    assert host._wait_ready(timeout=0.3) is False
