"""检索边界：首次入库日期在排序前筛选，来源不由路径猜测。"""
import numpy as np
from app import config, search


def test_date_filter_applies_before_top_k(monkeypatch):
    rows = [
        {"root": "r", "relpath": "old.png", "content_version": "v1", "ingested_at": "2026-11-06T15:59:59+00:00", "shot_at": None, "origin": "scan", "article_id": None},
        {"root": "r", "relpath": "new.png", "content_version": "v2", "ingested_at": "2026-11-06T16:00:00+00:00", "shot_at": "2020-01-01", "origin": "scan", "article_id": None},
    ]
    vector = np.zeros((2, 512), dtype=np.float32)
    vector[:, 0] = [1.0, 0.5]
    monkeypatch.setattr(search.STORE, "snapshot", lambda: (1, rows, vector))
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "r", "type": "local", "path": "/tmp", "writable": False},))
    query = np.zeros(512, dtype=np.float32)
    query[0] = 1
    items = search._rank(query, ["r"], "2026-11-07", "2026-11-07", 1)
    assert [x["relpath"] for x in items] == ["new.png"]
    assert items[0]["article"] is None


def test_decorating_empty_results_keeps_success_response():
    from app.media_routes import _checked_search
    assert _checked_search([], False) == {"items": [], "query_review": None}
