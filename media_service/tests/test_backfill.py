"""历史图片回填：改写 HTML、跳过小图、重复执行不再下载。"""
import http.server
import json
import sys
import threading
from io import BytesIO
from pathlib import Path

# 测试以 media_service 为 cwd 运行；补上仓库根，才能按包名导入命令行模块。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image
import pytest

from media_service import backfill


def _png(size, color):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class _Handler(http.server.BaseHTTPRequestHandler):
    bodies = {}
    hits = []

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        body = type(self).bodies.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _Handler.hits = []
    _Handler.bodies = {
        "/mmbiz/big.png": _png((640, 480), "blue"),
        "/mmbiz/icon.png": _png((16, 16), "red"),
    }
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _article(tmp_path: Path, base: str) -> Path:
    art = tmp_path / "acct" / "标题"
    art.mkdir(parents=True)
    (art / "metadata.json").write_text(
        json.dumps(
            {
                "title": "标题",
                "url": "https://mp.weixin.qq.com/s?__biz=MzA5&mid=265&idx=1",
                "time": "2026-01-01T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (art / "标题.html").write_text(
        '<div id="js_content">'
        f'<p><img data-src="{base}/mmbiz/big.png?a=1" src="{base}/mmbiz/big.png?a=1"></p>'
        f'<p><img data-src="{base}/mmbiz/icon.png" src="{base}/mmbiz/icon.png"></p>'
        "</div>",
        encoding="utf-8",
    )
    return art


def test_backfill_downloads_rewrites_and_skips_icons(tmp_path, server):
    art = _article(tmp_path, server)

    result = backfill.backfill_article(art, dry_run=False)

    assert result["status"] == "updated"
    assert result["saved"] == 1 and result["skipped"] == 1
    html = (art / "标题.html").read_text(encoding="utf-8")
    assert "media/img_001.png" in html
    assert f"{server}/mmbiz/big.png" not in html
    # 小图下载后删除，不留在 media/ 里，也不写进 HTML
    assert not (art / "media" / "img_002.png").exists()
    meta = json.loads((art / "metadata.json").read_text(encoding="utf-8"))
    assert meta["images"]["saved"] == 1
    assert meta["article_key"] == "MzA5:265:1"
    assert meta["backfilled_at"]


def test_backfill_is_idempotent(tmp_path, server):
    art = _article(tmp_path, server)
    backfill.backfill_article(art, dry_run=False)
    hits_after_first = len(_Handler.hits)

    second = backfill.backfill_article(art, dry_run=False)

    assert second["status"] == "clean"
    assert len(_Handler.hits) == hits_after_first


def test_dry_run_changes_nothing(tmp_path, server):
    art = _article(tmp_path, server)
    before = (art / "标题.html").read_text(encoding="utf-8")

    result = backfill.backfill_article(art, dry_run=True)

    assert result["status"] == "updated" and result["saved"] == 2
    assert not (art / "media").exists()
    assert (art / "标题.html").read_text(encoding="utf-8") == before
    assert _Handler.hits == []


def test_failed_download_keeps_original_html(tmp_path, server):
    art = _article(tmp_path, server)
    _Handler.bodies = {}  # 全部 404
    before = (art / "标题.html").read_text(encoding="utf-8")

    result = backfill.backfill_article(art, dry_run=False)

    assert result["failed"] == 2 and result["saved"] == 0
    assert (art / "标题.html").read_text(encoding="utf-8") == before


def test_missing_metadata_reports_bad_metadata(tmp_path):
    art = tmp_path / "acct" / "坏目录"
    art.mkdir(parents=True)
    (art / "metadata.json").write_text("{not json", encoding="utf-8")

    assert backfill.backfill_article(art, dry_run=False)["status"] == "bad_metadata"


def test_article_html_prefers_metadata_title(tmp_path, server):
    art = _article(tmp_path, server)
    (art / "另一个.html").write_text("<html></html>", encoding="utf-8")

    result = backfill.backfill_article(art, dry_run=False)

    assert result["status"] == "updated"
    assert "media/img_001.png" in (art / "标题.html").read_text(encoding="utf-8")


def test_icon_only_article_also_becomes_idempotent(tmp_path, server):
    art = tmp_path / "acct" / "只有图标"
    art.mkdir(parents=True)
    (art / "metadata.json").write_text(
        json.dumps({"title": "只有图标", "url": "https://mp.weixin.qq.com/s/AbCdEf"},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    (art / "只有图标.html").write_text(
        f'<div><img src="{server}/mmbiz/icon.png"></div>', encoding="utf-8"
    )

    first = backfill.backfill_article(art, dry_run=False)
    hits = len(_Handler.hits)
    second = backfill.backfill_article(art, dry_run=False)

    assert first["skipped"] == 1
    assert second["status"] == "clean"
    assert len(_Handler.hits) == hits
