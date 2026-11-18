"""图片下载：Referer、重试、并发落盘、质量过滤与统计。"""
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import downloader

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

try:
    from PIL import Image as _Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class _Handler(http.server.BaseHTTPRequestHandler):
    hits = []
    fail_first = False
    body = PNG_1PX
    routes = {}

    def do_GET(self):  # noqa: N802
        type(self).hits.append({
            "path": self.path,
            **{k.lower(): v for k, v in self.headers.items()},
        })
        if self.path in type(self).routes:
            payload = type(self).routes[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if type(self).fail_first and len(type(self).hits) == 1:
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(type(self).body)))
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *args):  # noqa: D401
        pass


class DownloadResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.hits = []
        _Handler.fail_first = False
        _Handler.body = PNG_1PX
        _Handler.routes = {}
        self.patch = mock.patch.object(downloader, "get_proxies_dict", lambda: {})
        self.patch.start()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self.patch.stop()
        self._tmpdir.cleanup()

    def _url(self, path="/img_001.jpg"):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_sends_referer_when_asked(self):
        name = downloader.download_resource(
            self._url(), self.tmp / "img_001.jpg", referer=downloader.IMAGE_REFERER
        )
        self.assertEqual(name, "img_001.png")  # 后缀按真实字节判定，不信 URL
        self.assertEqual(_Handler.hits[0]["referer"], "https://mp.weixin.qq.com/")

    def test_no_referer_by_default(self):
        downloader.download_resource(self._url(), self.tmp / "img_001.jpg")
        self.assertNotIn("referer", _Handler.hits[0])

    def test_retries_after_server_error(self):
        _Handler.fail_first = True
        name = downloader.download_resource(
            self._url(), self.tmp / "img_001.jpg", retries=1
        )
        self.assertIsNotNone(name)
        self.assertEqual(len(_Handler.hits), 2)

    def test_gives_up_after_retry_budget(self):
        original = _Handler.do_GET

        def always_fail(handler):
            type(handler).hits.append({})
            handler.send_response(503)
            handler.end_headers()

        _Handler.do_GET = always_fail
        try:
            self.assertIsNone(
                downloader.download_resource(self._url(), self.tmp / "img_001.jpg", retries=2)
            )
            # 传输层自身也有重试，这里只断言「有限次数后放弃」而不是精确次数
            self.assertGreaterEqual(len(_Handler.hits), 3)
        finally:
            _Handler.do_GET = original

    def test_no_partial_file_left_behind(self):
        _Handler.body = b""
        self.assertIsNone(downloader.download_resource(self._url(), self.tmp / "img_001.jpg"))
        self.assertEqual(list(self.tmp.glob("*.part")), [])

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_article_downloads_concurrently_and_filters_body_images(self):
        def _png_bytes(image):
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()

        good_bytes = _png_bytes(_Image.frombytes("RGB", (640, 480), os.urandom(640 * 480 * 3)))
        tiny_bytes = _png_bytes(_Image.new("RGB", (32, 32), "red"))

        _Handler.routes = {
            "/mmbiz_good_1.png": good_bytes,
            "/mmbiz_good_2.png": good_bytes,  # 内容重复，应被 dup 过滤
            "/mmbiz_tiny.png": tiny_bytes,    # 小图标，应被 tiny 过滤
        }
        article_html = (
            '<!DOCTYPE html><html><head>'
            '<meta property="og:title" content="配图并发与过滤测试">'
            '</head><body>'
            '<div id="js_content">'
            f'<img data-src="{self._url("/mmbiz_good_1.png")}" />'
            f'<img data-src="{self._url("/mmbiz_good_2.png")}" />'
            f'<img data-src="{self._url("/mmbiz_tiny.png")}" />'
            '</div></body></html>'
        )
        out_dir = self.tmp / "out"
        with mock.patch.object(
            downloader, "fetch_wechat_article_html",
            return_value=(article_html, "https://mp.weixin.qq.com/s/test_img_filter", ""),
        ), mock.patch.object(
            downloader, "get_settings",
            return_value={
                "auto_save_images": True,
                "auto_save_videos": False,
                "save_markdown": False,
                "save_pdf": False,
            },
        ), mock.patch.object(downloader, "report_proxy_status"):
            result = downloader.download_single_article(
                "https://mp.weixin.qq.com/s/test_img_filter", out_dir
            )

        self.assertTrue(result["success"])
        stats = result["images"]
        self.assertEqual(stats["expected"], 3)
        self.assertEqual(stats["saved"], 1)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["filtered_reasons"], {"dup": 1, "tiny": 1})

        art_dir = Path(result["path"])
        media_files = sorted(p.name for p in (art_dir / "media").iterdir())
        self.assertEqual(media_files, ["img_001.png"])

        meta = json.loads((art_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["images"], stats)


class SavedImageJudgeTests(unittest.TestCase):
    """下载落盘后按保守口径筛选无用图片（坏图/图标/长条/纯色/透明/重复）。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.seen = []

    def tearDown(self):
        self._tmpdir.cleanup()

    def _save(self, name, image):
        path = self.tmp / name
        image.save(path)
        return path

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_icon_is_rejected_as_tiny(self):
        path = self._save("tiny.png", _Image.new("RGB", (32, 32), "red"))
        self.assertEqual(downloader._judge_saved_image(path, self.seen), "tiny")
        self.assertEqual(self.seen, [])

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_solid_photo_is_rejected_as_flat(self):
        path = self._save("flat.png", _Image.new("RGB", (640, 480), "blue"))
        self.assertEqual(downloader._judge_saved_image(path, self.seen), "flat")

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_extreme_strip_is_rejected(self):
        path = self._save("strip.png", _Image.new("RGB", (3000, 200), "gray"))
        self.assertEqual(downloader._judge_saved_image(path, self.seen), "strip")

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_mostly_transparent_layer_is_rejected(self):
        image = _Image.frombytes("RGBA", (640, 480), os.urandom(640 * 480 * 4))
        image.putalpha(0)
        path = self._save("alpha.png", image)
        self.assertEqual(downloader._judge_saved_image(path, self.seen), "transparent")

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_normal_photo_is_kept_and_registered(self):
        image = _Image.frombytes("RGB", (640, 480), os.urandom(640 * 480 * 3))
        path = self._save("photo.png", image)
        self.assertEqual(downloader._judge_saved_image(path, self.seen), "")
        self.assertEqual(len(self.seen), 1)

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_duplicate_is_rejected_by_content_hash(self):
        image = _Image.frombytes("RGB", (640, 480), os.urandom(640 * 480 * 3))
        first = self._save("a.png", image)
        second = self._save("b.png", image)
        self.assertEqual(downloader._judge_saved_image(first, self.seen), "")
        self.assertEqual(downloader._judge_saved_image(second, self.seen), "dup")

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_broken_file_is_rejected(self):
        path = self.tmp / "broken.png"
        path.write_bytes(b"not an image")
        self.assertEqual(downloader._judge_saved_image(path, self.seen), "broken")

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_switch_off_keeps_everything(self):
        path = self._save("tiny.png", _Image.new("RGB", (32, 32), "red"))
        with mock.patch.dict(os.environ, {"WX_DOWNLOAD_IMAGE_FILTER": "0"}):
            self.assertEqual(downloader._judge_saved_image(path, self.seen), "")


if __name__ == "__main__":
    unittest.main()
