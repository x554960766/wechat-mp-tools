"""图片下载：Referer、重试与小图过滤。"""
import http.server
import sys
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


class _Handler(http.server.BaseHTTPRequestHandler):
    hits = []
    fail_first = False
    body = PNG_1PX

    def do_GET(self):  # noqa: N802
        type(self).hits.append({k.lower(): v for k, v in self.headers.items()})
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
        self.patch = mock.patch.object(downloader, "get_proxies_dict", lambda: {})
        self.patch.start()
        self.tmp = Path(self.id().replace(".", "_") + ".tmp")
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        self.patch.stop()
        for path in self.tmp.glob("*"):
            path.unlink()
        self.tmp.rmdir()

    def _url(self):
        return f"http://127.0.0.1:{self.port}/img_001.jpg"

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


try:  # 小图判定依赖 Pillow；wmp 主环境不装，缺失时按「不判定」降级
    from PIL import Image as _Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class TinyImageTests(unittest.TestCase):
    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_small_icon_is_tiny(self):
        from io import BytesIO

        buffer = BytesIO()
        _Image.new("RGB", (32, 32), "red").save(buffer, "PNG")
        path = Path("tiny_test.png")
        path.write_bytes(buffer.getvalue())
        try:
            self.assertTrue(downloader._is_tiny_image(path))
        finally:
            path.unlink()

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_normal_photo_is_not_tiny(self):
        from io import BytesIO

        buffer = BytesIO()
        _Image.new("RGB", (640, 480), "blue").save(buffer, "PNG")
        path = Path("big_test.png")
        path.write_bytes(buffer.getvalue())
        try:
            self.assertFalse(downloader._is_tiny_image(path))
        finally:
            path.unlink()

    def test_unreadable_file_is_not_judged(self):
        path = Path("broken_test.png")
        path.write_bytes(b"not an image")
        try:
            self.assertFalse(downloader._is_tiny_image(path))
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
