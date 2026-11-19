import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from backend import articles as articles_module
from backend.db import (
    get_article_list_cache,
    init_db,
    migrate_article_cache_json,
    save_article_list_cache,
)


class ArticleListCacheDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "wechat_tools.db"
        self.env_patcher = patch.dict(
            os.environ,
            {"DB_TYPE": "sqlite", "DB_SQLITE_PATH": str(self.db_path)},
        )
        self.env_patcher.start()
        self.data_patcher = patch("backend.db.DATA_DIR", self.root)
        self.data_patcher.start()
        self.assertTrue(init_db())

    def tearDown(self):
        self.data_patcher.stop()
        self.env_patcher.stop()
        self.tmp.cleanup()

    def test_init_db_creates_article_cache_table(self):
        conn = sqlite3.connect(str(self.db_path))
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        self.assertIn("article_list_cache", names)

    def test_save_get_and_update_cache(self):
        ok = save_article_list_cache(
            "fake-a",
            "",
            articles=[{"id": "1", "title": "one"}],
            total=1,
            buffer_can_continue=False,
        )
        self.assertTrue(ok)
        first = get_article_list_cache("fake-a", "")
        self.assertIsNotNone(first)
        self.assertEqual(len(first["articles"]), 1)
        self.assertEqual(first["source"], "upstream")
        save_article_list_cache(
            "fake-a",
            "",
            articles=[{"id": "1", "title": "one"}, {"id": "2", "title": "two"}],
            total=2,
            buffer_can_continue=False,
        )
        second = get_article_list_cache("fake-a", "")
        self.assertEqual(len(second["articles"]), 2)

        save_article_list_cache(
            "fake-b",
            "",
            articles=[{"id": "1"}],
        )
        self.assertEqual(len(get_article_list_cache("fake-b", "")["articles"]), 1)

    def test_migrate_legacy_json_without_overwriting_existing(self):
        cache_dir = self.root / "article_cache"
        cache_dir.mkdir(exist_ok=True)
        payload = {
            "version": 1,
            "exported_at": "2026-11-19T17:51:10+08:00",
            "source": "http://127.0.0.1:5200/#articles",
            "fakeid": "legacy-fakeid",
            "account_name": "测试公众号",
            "keyword": "",
            "total": 3,
            "bufferBegin": 0,
            "bufferCanContinue": False,
            "articles": [{"id": "1"}, {"id": "2"}, {"id": "2"}],
        }
        path = cache_dir / "legacy.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        self.assertEqual(migrate_article_cache_json(), 1)
        migrated = get_article_list_cache("legacy-fakeid", "")
        self.assertEqual(migrated["source"], "legacy_json")
        self.assertEqual(migrated["updated_at"], "2026-11-19T17:51:10+08:00")
        self.assertEqual(len(migrated["articles"]), 2)
        self.assertEqual(migrated["total"], 2)

        payload["articles"] = [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}]
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(migrate_article_cache_json(), 0)
        self.assertEqual(len(get_article_list_cache("legacy-fakeid", "")["articles"]), 2)


class ArticleListCacheApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(articles_module.articles_bp)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        self.patchers = [
            patch("backend.waterwall.is_waterwall_active", return_value=False),
            patch("backend.weread_signer.get_rate_limit_status", return_value=(False, 0)),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()

    def _cache(self, articles, can_continue=False, source="db_cache"):
        return {
            "cache_key": "fake|",
            "fakeid": "fake",
            "keyword": "",
            "account_name": "测试公众号",
            "articles": articles,
            "total": len(articles),
            "buffer_begin": 0,
            "buffer_can_continue": can_continue,
            "source": source,
            "updated_at": "2026-11-19T00:00:00+08:00",
        }

    def test_cache_miss_rebuilds_and_saves_all_articles(self):
        fetch = MagicMock(side_effect=[
            ([{"id": "1"}, {"id": "2"}], 0, 1, 1),
            ([{"id": "3"}], 0, 0, 1),
        ])
        with patch.object(articles_module, "get_article_list_cache", return_value=None), \
             patch.object(articles_module, "save_article_list_cache", return_value=True) as save, \
             patch.object(articles_module, "_fetch_articles_accumulated", fetch):
            resp = self.client.get("/api/articles/list/fake?begin=0&count=2")

        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([a["id"] for a in data["articles"]], ["1", "2"])
        self.assertFalse(data["cache"]["hit"])
        self.assertEqual(data["cache"]["source"], "upstream")
        self.assertEqual(data["cache"]["cached_count"], 3)
        self.assertEqual(data["can_msg_continue"], 1)
        self.assertEqual(fetch.call_count, 2)
        save.assert_called_once()
        self.assertEqual(len(save.call_args.kwargs["articles"]), 3)

    def test_cache_hit_serves_slices_without_upstream_call(self):
        cache = self._cache([{"id": str(i)} for i in range(1, 4)])
        fetch = MagicMock()
        with patch.object(articles_module, "get_article_list_cache", return_value=cache), \
             patch.object(articles_module, "_fetch_articles_accumulated", fetch):
            first = self.client.get("/api/articles/list/fake?begin=0&count=2").get_json()
            second = self.client.get("/api/articles/list/fake?begin=2&count=2").get_json()

        self.assertTrue(first["cache"]["hit"])
        self.assertEqual([a["id"] for a in first["articles"]], ["1", "2"])
        self.assertEqual(first["can_msg_continue"], 1)
        self.assertEqual([a["id"] for a in second["articles"]], ["3"])
        self.assertEqual(second["can_msg_continue"], 0)
        fetch.assert_not_called()

    def test_refresh_incrementally_merges_new_articles(self):
        old_cache = self._cache([{"id": "old"}], source="db_cache")
        new_batch = [{"id": "new-1"}, {"id": "new-2"}]
        fetch = MagicMock(return_value=(new_batch, 0, 0, 1))
        with patch.object(articles_module, "get_article_list_cache", return_value=old_cache), \
             patch.object(articles_module, "save_article_list_cache", return_value=True) as save, \
             patch.object(articles_module, "_fetch_articles_accumulated", fetch):
            data = self.client.get(
                "/api/articles/list/fake?begin=0&count=2&refresh=1&name=%E4%B8%81%E5%B8%88%E5%85%84%E5%A4%A7%E6%A8%A1%E5%9E%8B"
            ).get_json()

        self.assertFalse(data["cache"]["hit"])
        self.assertEqual([a["id"] for a in data["articles"]], ["new-1", "new-2"])
        self.assertEqual(save.call_args.kwargs["account_name"], "丁师兄大模型")
        self.assertEqual(fetch.call_args.kwargs["account_name"], "丁师兄大模型")
        self.assertEqual(
            [a["id"] for a in save.call_args.kwargs["articles"]],
            ["new-1", "new-2", "old"],
        )
        self.assertEqual(data["cache"]["refreshed_count"], 2)
        self.assertFalse(data["cache"]["stopped_at_existing"])
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[2], 20)

    def test_refresh_stops_when_existing_article_found(self):
        cache = self._cache([{"id": "1"}, {"id": "2"}])
        batch = [{"id": "new"}, {"id": "1", "title": "updated"}]
        fetch = MagicMock(return_value=(batch, 0, 1, 1))
        with patch.object(articles_module, "get_article_list_cache", return_value=cache), \
             patch.object(articles_module, "save_article_list_cache", return_value=True) as save, \
             patch.object(articles_module, "_fetch_articles_accumulated", fetch):
            data = self.client.get("/api/articles/list/fake?begin=0&count=2&refresh=1").get_json()

        self.assertTrue(data["cache"]["stopped_at_existing"])
        self.assertEqual(data["cache"]["refreshed_count"], 2)
        merged = save.call_args.kwargs["articles"]
        self.assertEqual([a["id"] for a in merged], ["new", "1", "2"])
        self.assertEqual(merged[1]["title"], "updated")
        fetch.assert_called_once()

    def test_refresh_stops_at_scan_cap(self):
        cache = self._cache([{"id": "old"}])
        def make_batch(fakeid, begin, count, keyword="", account_name="", from_article_id=""):
            return (
                [{"id": f"new-{begin + i}"} for i in range(20)],
                0,
                1,
                1,
            )
        fetch = MagicMock(side_effect=make_batch)
        with patch.object(articles_module, "get_article_list_cache", return_value=cache), \
             patch.object(articles_module, "save_article_list_cache", return_value=True) as save, \
             patch.object(articles_module, "_fetch_articles_accumulated", fetch):
            data = self.client.get("/api/articles/list/fake?begin=0&count=2&refresh=1").get_json()

        self.assertEqual(fetch.call_count, 25)
        self.assertEqual(data["cache"]["refreshed_count"], 500)
        self.assertFalse(data["cache"]["stopped_at_existing"])
        self.assertEqual(len(save.call_args.kwargs["articles"]), 501)

    def test_cache_extension_fetches_and_persists_next_batch(self):
        cache = self._cache([{"id": "1"}, {"id": "2"}], can_continue=True)
        fetch = MagicMock(return_value=([{"id": "3"}], 0, 0, 1))
        with patch.object(articles_module, "get_article_list_cache", return_value=cache), \
             patch.object(articles_module, "save_article_list_cache", return_value=True) as save, \
             patch.object(articles_module, "_fetch_articles_accumulated", fetch):
            data = self.client.get("/api/articles/list/fake?begin=2&count=2").get_json()

        self.assertEqual([a["id"] for a in data["articles"]], ["3"])
        self.assertEqual(data["can_msg_continue"], 0)
        self.assertEqual(len(save.call_args.kwargs["articles"]), 3)
        fetch.assert_called_once()

    def test_upstream_error_falls_back_to_stale_cache(self):
        cache = self._cache([{"id": "1"}, {"id": "2"}], source="db_cache")
        get_cache = MagicMock(side_effect=[None, cache])
        fetch = MagicMock(side_effect=RuntimeError("upstream down"))
        with patch.object(articles_module, "get_article_list_cache", get_cache), \
             patch.object(articles_module, "save_article_list_cache", return_value=True) as save, \
             patch.object(articles_module, "_fetch_articles_accumulated", fetch):
            resp = self.client.get("/api/articles/list/fake?begin=0&count=2")

        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([a["id"] for a in data["articles"]], ["1", "2"])
        self.assertTrue(data["cache"]["stale"])
        self.assertEqual(data["cache"]["source"], "stale_cache")
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
