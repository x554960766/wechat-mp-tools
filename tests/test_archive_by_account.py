"""按号归档适配层：平台矩阵、账号归一、启动/进度/自动扫描。

适配层最容易错的地方不是下载（下载全部复用各平台原生线程），而是
这四件事，所以测试只盯它们：

- 账号归一：各平台收藏表字段不同，快手 url 还可能为空（此时得回退到主页链接）。
- 范围换算：快手线程只吃页数，只能按 ceil(N/20) 近似，不能悄悄改语义。
- 互斥：同一平台同时只允许一个归档任务，B站/快手还要让原生 ``_task_state`` 挡住。
- 自动扫描：下载终态后扫一次对应媒体根，且只扫一次（页面反复轮询不能重复提交）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import archive_by_account as aba  # noqa: E402


class FakeHost:
    """媒体子服务句柄：只提供 scan 触发所需的 ready/state/port。"""

    port = 59999

    def __init__(self, ready: bool = True, state: str = "running"):
        self.info = {"ready": ready, "state": state, "port": self.port}

    def status(self):
        return dict(self.info)


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    from backend import articles, bilibili, kuaishou, xiaohongshu

    # 台账重定向到临时文件：所有用例都不得写入真实 data/archive_tasks.json
    monkeypatch.setattr(aba, "ARCHIVE_TASKS_FILE", tmp_path / "archive_tasks.json")
    aba._tasks.clear()
    aba._active.clear()
    aba.configure(None, ("wechat", "xhs", "bilibili", "kuaishou"))
    articles._download_tasks.clear()
    xiaohongshu._download_tasks.clear()
    bilibili._set_task_state(status="idle", total=0, downloaded_count=0, failed_count=0)
    kuaishou._set_task_state(status="idle", total=0, downloaded_count=0, failed_count=0)
    yield
    aba._tasks.clear()
    aba._active.clear()


@pytest.fixture
def scan_calls(monkeypatch):
    """拦截扫描提交，记录每次 POST 的 root。"""
    calls = []

    class FakeResp:
        status_code = 202

        def __init__(self, job_id):
            self.job_id = job_id

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": self.job_id, "status": "running"}

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "root": (json or {}).get("root")})
        return FakeResp(f"job{len(calls) - 1}")

    monkeypatch.setattr(aba.requests, "post", fake_post)
    aba.configure(lambda: FakeHost())
    return calls


# ---------------------------------------------------------------- 平台矩阵

def test_platform_matrix_follows_configured_platforms():
    aba.configure(None, ("wechat",))
    assert [p["id"] for p in aba.platform_matrix()["platforms"]] == ["wechat"]

    aba.configure(None, ("wechat", "xhs", "bilibili", "kuaishou"))
    platforms = aba.platform_matrix()["platforms"]
    assert [p["id"] for p in platforms] == ["wechat", "xhs", "bilibili", "kuaishou"]
    assert [p["root"] for p in platforms] == ["articles-full", "xhs", "bilibili", "kuaishou"]
    assert all(p["enabled"] for p in platforms)


def test_platform_matrix_marks_kuaishou_page_approximation():
    aba.configure(None, ("kuaishou",))
    kuaishou = aba.platform_matrix()["platforms"][0]
    assert kuaishou["limit_unit"] == "page"
    assert kuaishou["allow_link"] is True
    assert "可能多于" in kuaishou["note"]


# ---------------------------------------------------------------- 账号归一

def test_accounts_normalize_wechat(monkeypatch):
    from backend import config

    monkeypatch.setattr(config, "load_json", lambda path, default=None: [
        {"fakeid": "f1", "nickname": "公众号甲"},
        {"fakeid": "", "nickname": "没有 fakeid"},
        {"nickname": "缺字段"},
    ])
    data = aba.list_accounts("wechat")
    assert data["total"] == 1
    assert data["accounts"][0] == {"id": "f1", "name": "公众号甲", "sub": "公众号", "url": ""}


def test_accounts_normalize_kuaishou_falls_back_to_profile_url(monkeypatch):
    from backend import kuaishou

    monkeypatch.setattr(kuaishou, "load_json", lambda path, default=None: [
        {"user_id": "u1", "nickname": "老铁", "url": ""},
        {"user_id": "u2", "nickname": "有链接", "url": "https://v.kuaishou.com/abc"},
    ])
    accounts = aba.list_accounts("kuaishou")["accounts"]
    assert accounts[0]["url"] == "https://www.kuaishou.com/profile/u1"
    assert accounts[1]["url"] == "https://v.kuaishou.com/abc"


def test_accounts_normalize_bilibili_and_xhs(monkeypatch):
    from backend import bilibili, xiaohongshu

    monkeypatch.setattr(bilibili, "load_json", lambda path, default=None: [
        {"mid": 42, "nickname": "UP主"},
    ])
    monkeypatch.setattr(xiaohongshu, "load_json", lambda path, default=None: [
        {"user_id": "x1", "nickname": "博主", "url": ""},
    ])
    bili = aba.list_accounts("bilibili")["accounts"][0]
    xhs = aba.list_accounts("xhs")["accounts"][0]
    assert bili == {
        "id": "42", "name": "UP主", "sub": "B站 UP主",
        "url": "https://space.bilibili.com/42",
    }
    assert xhs["url"] == "https://www.xiaohongshu.com/user/profile/x1"


# ---------------------------------------------------------------- 公众号

def test_wechat_start_status_scans_once(monkeypatch, scan_calls):
    from backend import articles

    monkeypatch.setattr(articles, "_do_collect_latest", lambda *a, **k: None)

    started = aba.start("wechat", account_id="f1", account_name="甲", limit=3)
    assert started["task_id"] and started["root"] == "articles-full"
    assert started["requested"] == 3

    native = articles._download_tasks[started["native_task_id"]]
    native.update({"status": "running", "total": 3, "completed": 1})
    running = aba.task_status("wechat", started["task_id"])
    assert running["status"] == "running"
    assert (running["total"], running["completed"]) == (3, 1)
    assert scan_calls == []

    native.update({"status": "completed", "completed": 3})
    done = aba.task_status("wechat", started["task_id"])
    assert done["status"] == "completed"
    assert done["scan_job_id"] == "job0"
    assert scan_calls == [{"url": "http://127.0.0.1:59999/api/media/scan",
                           "root": "articles-full"}]

    # 反复轮询不能再提交一次扫描
    again = aba.task_status("wechat", started["task_id"])
    assert again["scan_job_id"] == "job0"
    assert len(scan_calls) == 1


def test_wechat_second_start_rejected_while_running(monkeypatch):
    from backend import articles

    monkeypatch.setattr(articles, "_do_collect_latest", lambda *a, **k: None)
    aba.start("wechat", account_id="f1", account_name="甲", limit=2)

    with pytest.raises(aba.ArchiveError) as excinfo:
        aba.start("wechat", account_id="f2", account_name="乙", limit=2)
    assert excinfo.value.status == 409
    assert "已有归档任务" in excinfo.value.message


def test_terminal_task_releases_platform_slot(monkeypatch, scan_calls):
    from backend import articles

    monkeypatch.setattr(articles, "_do_collect_latest", lambda *a, **k: None)
    first = aba.start("wechat", account_id="f1", account_name="甲", limit=2)
    articles._download_tasks[first["native_task_id"]]["status"] = "completed"
    aba.task_status("wechat", first["task_id"])

    second = aba.start("wechat", account_id="f1", account_name="甲", limit=2)
    assert second["task_id"] != first["task_id"]


def test_scan_error_recorded_when_media_not_ready(monkeypatch):
    from backend import articles

    monkeypatch.setattr(articles, "_do_collect_latest", lambda *a, **k: None)
    monkeypatch.setattr(
        aba, "_probe_media_health", lambda port: False
    )
    aba.configure(lambda: FakeHost(ready=False, state="stopped"))

    started = aba.start("wechat", account_id="f1", account_name="甲", limit=1)
    articles._download_tasks[started["native_task_id"]]["status"] = "completed"
    done = aba.task_status("wechat", started["task_id"])
    assert done["scan_job_id"] == ""
    assert "未就绪" in done["scan_error"]


# ---------------------------------------------------------------- 参数校验

@pytest.mark.parametrize("limit", [0, 51, -1])
def test_limit_out_of_range_rejected(limit):
    with pytest.raises(aba.ArchiveError) as excinfo:
        aba.start("wechat", account_id="f1", limit=limit)
    assert excinfo.value.status == 400


def test_unknown_platform_rejected():
    with pytest.raises(aba.ArchiveError) as excinfo:
        aba.start("douyin", account_id="x")
    assert excinfo.value.status == 400
    assert "未启用" in excinfo.value.message


# ---------------------------------------------------------------- B站

def test_bilibili_collects_items_across_pages(monkeypatch):
    from backend import bilibili

    pages = {
        1: [{"bvid": "BV1", "title": "一"}, {"bvid": "BV2", "title": "二"}],
        2: [{"bvid": "BV3", "title": "三"}],
    }
    fake_client = type("FakeClient", (), {
        "get_user_videos": lambda self, mid, page=1, page_size=30: (pages.get(page, []), 3),
    })
    captured = {}
    monkeypatch.setattr(bilibili, "BilibiliClient", fake_client)
    monkeypatch.setattr(bilibili, "ensure_bili_dirs", lambda: None)
    monkeypatch.setattr(bilibili, "_run_batch_download_thread",
                        lambda items, target_dir, quality=None: captured.setdefault("items", items))

    started = aba.start("bilibili", account_id="42", account_name="UP主", limit=3)
    assert started["root"] == "bilibili"
    assert [item["bvid"] for item in captured["items"]] == ["BV1", "BV2", "BV3"]
    assert all(item["page_num"] == 1 for item in captured["items"])


def test_bilibili_rejected_when_native_batch_running(monkeypatch):
    from backend import bilibili

    bilibili._set_task_state(status="running")
    with pytest.raises(aba.ArchiveError) as excinfo:
        aba.start("bilibili", account_id="42", limit=3)
    assert excinfo.value.status == 409


# ---------------------------------------------------------------- 快手

def test_kuaishou_pages_rounded_up(monkeypatch):
    from backend import kuaishou as ks

    class FakeClient:
        def resolve_share_url(self, url):
            return url

        @staticmethod
        def extract_user_id(url):
            return "u1"

    captured = {}
    monkeypatch.setattr(ks, "KuaishouClient", FakeClient)
    monkeypatch.setattr(ks, "ensure_kuaishou_dirs", lambda: None)
    monkeypatch.setattr(ks, "_run_profile_download_task",
                        lambda user_id, max_pages, target_dir, profile_url="": captured.update(
                            user_id=user_id, max_pages=max_pages))

    started = aba.start("kuaishou", account_id="u1", limit=41)
    assert started["root"] == "kuaishou"
    # ceil(41/20) = 3 页，等价于「可能多于 41 条」
    assert captured["max_pages"] == 3
    assert captured["user_id"] == "u1"
    assert "可能多于 41" in started["note"]


def test_kuaishou_accepts_pasted_profile_url(monkeypatch):
    from backend import kuaishou as ks

    class FakeClient:
        def resolve_share_url(self, url):
            return "https://www.kuaishou.com/profile/u9"

        @staticmethod
        def extract_user_id(url):
            return "u9" if "u9" in url else ""

    monkeypatch.setattr(ks, "KuaishouClient", FakeClient)
    monkeypatch.setattr(ks, "ensure_kuaishou_dirs", lambda: None)
    monkeypatch.setattr(ks, "_run_profile_download_task", lambda *a, **k: None)

    started = aba.start("kuaishou", profile_url="https://v.kuaishou.com/xyz", limit=20)
    assert started["task_id"]


# ---------------------------------------------------------------- 小红书

def test_xhs_truncates_notes_to_limit(monkeypatch):
    from backend import xiaohongshu as xhs

    class FakeClient:
        def get_user_profile(self, url):
            return {
                "user_id": "x1",
                "nickname": "博主",
                "has_more": False,
                "notes": [
                    {"note_id": "n1", "xsec_token": "t1"},
                    {"note_id": "n2", "xsec_token": "t2"},
                    {"note_id": "n3", "xsec_token": "t3"},
                ],
            }

    captured = {}
    monkeypatch.setattr(xhs, "XhsClient", FakeClient)
    monkeypatch.setattr(xhs, "_do_xhs_download_thread",
                        lambda task_id, urls, account_name: captured.update(urls=urls))

    started = aba.start("xhs", account_id="x1", limit=2,
                       profile_url="https://www.xiaohongshu.com/user/profile/x1")
    assert started["root"] == "xhs"
    assert len(captured["urls"]) == 2
    assert captured["urls"][0].startswith("https://www.xiaohongshu.com/explore/n1")
    assert "xsec_token=t1" in captured["urls"][0]
    assert aba.task_status("xhs", started["task_id"])["total"] == 2


def test_scan_proceeds_when_state_stale_but_port_healthy(monkeypatch):
    """宿主状态机滞后（starting/stopped）但端口答得出健康检查时仍提交扫描。

    回归：孤儿媒体进程占端口期间，宿主状态在 starting/running 间抖动，
    旧逻辑直接放弃入库，下载完成却永远不进索引。
    """
    from backend import articles

    monkeypatch.setattr(articles, "_do_collect_latest", lambda *a, **k: None)
    monkeypatch.setattr(aba, "_probe_media_health", lambda port: True)

    class _Resp:
        status_code = 202

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "scanjob-1"}

    posted = []

    def _fake_post(url, json=None, timeout=None):
        posted.append((url, json, timeout))
        return _Resp()

    monkeypatch.setattr(aba.requests, "post", _fake_post)
    aba.configure(lambda: FakeHost(ready=False, state="starting"))

    started = aba.start("wechat", account_id="f1", account_name="甲", limit=1)
    articles._download_tasks[started["native_task_id"]]["status"] = "completed"
    done = aba.task_status("wechat", started["task_id"])
    assert done["scan_error"] == ""
    assert done["scan_job_id"] == "scanjob-1"
    assert posted and posted[0][0] == "http://127.0.0.1:59999/api/media/scan"
    assert posted[0][1] == {"root": "articles-full"}


# ---------------------------------------------------------------- 下载记录台账

def test_start_records_account_id_in_ledger(monkeypatch, scan_calls):
    """发起下载后台账落一条记录，含账号溯源字段。"""
    from backend import articles

    monkeypatch.setattr(articles, "_do_collect_latest", lambda *a, **k: None)

    started = aba.start("wechat", account_id="f1", account_name="甲号", limit=2)
    assert started["account_id"] == "f1"

    rows = aba._ledger_load()
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == started["task_id"]
    assert row["account_id"] == "f1"
    assert row["account_name"] == "甲号"
    assert row["platform"] == "wechat"
    assert row["platform_label"] == "微信公众号"
    assert row["created_at"] > 0
    assert row["status"] == "running"
    assert row["finished_at"] == ""


def test_task_status_persists_terminal_state(monkeypatch, scan_calls):
    """下载进入终态后台账更新状态并写入 finished_at。"""
    from backend import articles

    monkeypatch.setattr(articles, "_do_collect_latest", lambda *a, **k: None)

    started = aba.start("wechat", account_id="f2", account_name="乙号", limit=1)
    native = articles._download_tasks[started["native_task_id"]]
    native["status"] = "completed"
    native["total"] = 1
    native["completed"] = 1

    done = aba.task_status("wechat", started["task_id"])
    assert done["status"] == "completed"

    row = aba._ledger_load()[0]
    assert row["status"] == "completed"
    assert row["completed"] == 1
    assert row["finished_at"] > 0
    assert row["account_id"] == "f2"


def test_ledger_upsert_dedupes_by_task_id():
    """同一 task_id 重复写入只保留一条，避免轮询刷屏。"""
    record = {"task_id": "t1", "platform": "wechat", "root": "articles-full",
              "requested": 3, "account_id": "f1", "account_name": "甲",
              "native_task_id": "n1", "created_at": time.time(), "scan_job_id": "",
              "scan_error": "", "scan_requested": False, "note": ""}
    aba._ledger_upsert(record, {"status": "running", "total": 3, "completed": 1,
                                "failed": 0, "skipped": 0, "current": "", "reason": ""})
    aba._ledger_upsert(record, {"status": "completed", "total": 3, "completed": 3,
                                "failed": 0, "skipped": 0, "current": "", "reason": ""})
    rows = aba._ledger_load()
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


def test_list_tasks_filters_platform_and_sorts_desc():
    now = time.time()
    aba._ledger_save([
        {"task_id": "a", "platform": "wechat", "created_at": now - 100},
        {"task_id": "b", "platform": "xhs", "created_at": now - 50},
        {"task_id": "c", "platform": "wechat", "created_at": now - 10},
    ])
    all_tasks = aba.list_tasks()
    assert [t["task_id"] for t in all_tasks["tasks"]] == ["c", "b", "a"]
    assert all_tasks["total"] == 3

    wechat_only = aba.list_tasks(platform="wechat")
    assert [t["task_id"] for t in wechat_only["tasks"]] == ["c", "a"]
    assert wechat_only["total"] == 2


def test_list_tasks_limit_capped():
    now = time.time()
    aba._ledger_save([
        {"task_id": f"t{i}", "platform": "wechat", "created_at": now - i}
        for i in range(10)
    ])
    assert len(aba.list_tasks(limit=3)["tasks"]) == 3
    assert aba.list_tasks(limit=3)["total"] == 10
    # 非法 limit 与越界 limit 都回退到默认值，返回全部（仅 10 条时）
    assert len(aba.list_tasks(limit="bad")["tasks"]) == 10
    assert len(aba.list_tasks(limit=9999)["tasks"]) == 10


def test_ledger_retention_caps_count_and_age():
    now = time.time()
    rows = [{"task_id": f"t{i}", "created_at": now - i * 60} for i in range(250)]
    pruned = aba._prune_ledger(rows)
    assert len(pruned) == aba.ARCHIVE_TASKS_MAX_COUNT
    assert pruned[0]["task_id"] == "t0"  # 最新的在最前

    too_old = [{"task_id": "old", "created_at": now - 8 * 24 * 3600}]
    assert aba._prune_ledger(too_old) == []


def test_ledger_write_failure_does_not_break_start(monkeypatch, scan_calls):
    """台账写失败只记日志，不能让下载启动失败。"""
    from backend import articles

    monkeypatch.setattr(articles, "_do_collect_latest", lambda *a, **k: None)

    def boom(filepath, data):
        raise OSError("disk full")

    monkeypatch.setattr(aba, "save_json", boom)
    started = aba.start("wechat", account_id="f3", account_name="丙号", limit=1)
    assert started["task_id"]
    assert started["account_id"] == "f3"


def test_accounts_endpoint_rejects_missing_platform():
    """缺 platform 不能静默按 wechat 查，否则跨平台账号会被错配给公众号。"""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(aba.init_blueprint(app, ("wechat", "xhs")))
    client = app.test_client()

    assert client.get("/api/archive/accounts?platform=").status_code == 400
    assert client.get("/api/archive/accounts").status_code == 400
