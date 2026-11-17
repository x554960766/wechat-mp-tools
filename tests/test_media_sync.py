"""媒体服务通知层：关联键归一与扫描防抖。

这里不连真实媒体服务：``_trigger_scan`` 用桩函数替换，验证的是「通知如何
合并」「宿主缺席时是否安静」「依赖状态的判断」三件事。
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import media_sync


class NormalizeArticleUrlTests(unittest.TestCase):
    def test_prefers_biz_mid_idx_over_tracking_params(self):
        url = (
            "https://mp.weixin.qq.com/s?__biz=MzA5MTI3NDU2Mw==&mid=2650000001"
            "&idx=2&sn=abcdef&chksm=deadbeef&scene=126#rd"
        )
        self.assertEqual(
            media_sync.normalize_article_url(url), "MzA5MTI3NDU2Mw==:2650000001:2"
        )

    def test_same_article_with_different_tracking_still_matches(self):
        a = "https://mp.weixin.qq.com/s?__biz=MzA5&mid=1&idx=1&scene=21"
        b = "https://mp.weixin.qq.com/s?__biz=MzA5&mid=1&idx=1&from=timeline&isappinstalled=0"
        self.assertEqual(
            media_sync.normalize_article_url(a), media_sync.normalize_article_url(b)
        )

    def test_short_link_path_falls_back_to_sn(self):
        self.assertEqual(
            media_sync.normalize_article_url("https://mp.weixin.qq.com/s/AbCdEf"),
            "sn:AbCdEf",
        )
        self.assertEqual(
            media_sync.normalize_article_url("https://mp.weixin.qq.com/s?sn=AbCdEf&scene=1"),
            "sn:AbCdEf",
        )

    def test_unknown_shape_falls_back_to_normalized_url(self):
        key = media_sync.normalize_article_url(
            "https://Example.com/Article/1?utm_source=x&__biz=abc#frag"
        )
        self.assertTrue(key.startswith("url:https://example.com/article/1"))
        self.assertNotIn("utm_source", key)
        self.assertNotIn("frag", key)

    def test_empty_input_returns_empty_key(self):
        self.assertEqual(media_sync.normalize_article_url(""), "")
        self.assertEqual(media_sync.normalize_article_url(None), "")

    def test_article_id_is_stable_sha256(self):
        key = "MzA5:1:1"
        first = media_sync.article_id_for_key(key)
        self.assertEqual(first, media_sync.article_id_for_key(key))
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, media_sync.article_id_for_key("MzA5:1:2"))


class NotifyScanTests(unittest.TestCase):
    def tearDown(self):
        media_sync.stop_notifier()
        media_sync.set_host_provider(None)

    def test_burst_of_notifications_triggers_one_scan(self):
        calls = []
        with mock.patch.object(media_sync, "_trigger_scan", lambda: calls.append(1) or True), \
             mock.patch.object(media_sync, "DEBOUNCE_SECONDS", 0.05):
            for _ in range(5):
                media_sync.notify_scan("batch")
            deadline = time.monotonic() + 2.0
            while not calls and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)  # 再等一个窗口，确认没有第二次
        self.assertEqual(len(calls), 1)

    def test_notification_never_raises_when_scan_fails(self):
        def boom():
            raise RuntimeError("subprocess gone")

        with mock.patch.object(media_sync, "_trigger_scan", boom), \
             mock.patch.object(media_sync, "DEBOUNCE_SECONDS", 0.02):
            media_sync.notify_scan("x")  # 不应抛到调用方
            time.sleep(0.2)

    def test_scan_skipped_when_media_host_not_ready(self):
        class Host:
            port = 8765

            def status(self):
                return {"ready": False, "state": "starting"}

        media_sync.set_host_provider(lambda: Host())
        # 未就绪时不发请求，也不报错
        self.assertFalse(media_sync._trigger_scan())

    def test_flush_pending_returns_false_without_queued_work(self):
        self.assertFalse(media_sync.flush_pending())


if __name__ == "__main__":
    unittest.main()
