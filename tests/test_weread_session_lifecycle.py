import json
import time
from types import SimpleNamespace

import backend.account_pool as pool_mod
import backend.weread_signer as signer


def test_refresh_access_token_uses_guest_token_and_device_name(monkeypatch):
    captured = {}

    class DummySession:
        trust_env = True

        def post(self, url, json=None, headers=None, proxies=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return SimpleNamespace(status_code=200, json=lambda: {"accessToken": "tok_new"}, text='{"accessToken":"tok_new"}')

    monkeypatch.setattr(signer.requests, "Session", lambda: DummySession())
    monkeypatch.setattr(signer, "gen_signature", lambda items: "|".join(items))
    signer.update_guest_token(signer.DEFAULT_GUEST_TOKEN)

    status, data = signer.refresh_access_token(
        "rf_123",
        device_id="dev_abc",
        device_name="Xiaomi 13",
        in_background=1,
    )
    assert status == 200
    assert data["accessToken"] == "tok_new"
    payload = captured["payload"]
    assert payload["deviceId"] == "dev_abc"
    assert payload["deviceName"] == "Xiaomi 13"
    assert payload["inBackground"] == 1
    assert payload["signature"].startswith(f"{signer.DEFAULT_GUEST_TOKEN}|dev_abc|")
    assert payload["signature"].endswith("|rf_123")


def test_needs_proactive_refresh():
    now = time.time()
    assert not signer.needs_proactive_refresh({"refreshToken": "rf", "refreshTokenExpired": True, "refreshedAt": now - 9999})
    assert not signer.needs_proactive_refresh({"refreshToken": "", "refreshedAt": now - 9999})
    assert not signer.needs_proactive_refresh({"refreshToken": "rf", "refreshedAt": now - 600})
    assert signer.needs_proactive_refresh({"refreshToken": "rf", "refreshedAt": now - signer.PROACTIVE_REFRESH_INTERVAL - 1})
    assert signer.needs_proactive_refresh({"refreshToken": "rf"})


def test_try_refresh_session_singleflight_guest_retry_and_2013(tmp_path, monkeypatch):
    sess_file = tmp_path / ".session.json"
    sess_file.write_text(json.dumps({
        "vid": "1001",
        "accessToken": "tok_1",
        "refreshToken": "rf_1",
        "deviceId": "dev_1",
    }), encoding="utf-8")

    # 1. expected_access_token 已不匹配当前文件中的 tok_1 -> 单飞直接复用，不调用上游
    calls = []
    monkeypatch.setattr(signer, "refresh_access_token", lambda *a, **kw: calls.append((a, kw)) or (200, {}))
    reused = signer.try_refresh_session(str(sess_file), expected_access_token="tok_0")
    assert reused is not None and reused["accessToken"] == "tok_1"
    assert len(calls) == 0

    # 2. 首次签名失败 + guest_token 同步变化 -> 重签重试一次并成功
    signer._LAST_REFRESH_META.clear()
    with signer._GUEST_TOKEN_LOCK:
        signer._GUEST_TOKEN_CACHE["token"] = "old_gt"
        signer._GUEST_TOKEN_CACHE["ts"] = 0.0

    monkeypatch.setattr(signer, "sync_guest_token", lambda **kw: signer.update_guest_token("fresh_gt"))

    def fake_refresh(rf, **kw):
        calls.append(kw.get("guest_token"))
        if kw.get("guest_token") == "old_gt":
            return 401, {"errcode": -2012, "errmsg": "invalid signature"}
        return 200, {"accessToken": "tok_2", "refreshToken": "rf_2"}

    monkeypatch.setattr(signer, "refresh_access_token", fake_refresh)
    ok_sess = signer.try_refresh_session(str(sess_file), expected_access_token="tok_1")
    assert ok_sess is not None and ok_sess["accessToken"] == "tok_2"
    assert calls == ["old_gt", "fresh_gt"]

    # 3. 10s 单飞冷却内无 expected_access_token 调用直接复用
    calls.clear()
    dedup_sess = signer.try_refresh_session(str(sess_file))
    assert dedup_sess is not None and dedup_sess["accessToken"] == "tok_2"
    assert len(calls) == 0

    # 4. -2013 带 forceAccessToken 时保留兜底 token 并标记 refreshTokenExpired=True
    signer._LAST_REFRESH_META.clear()
    monkeypatch.setattr(
        signer,
        "refresh_access_token",
        lambda rf, **kw: (200, {"errcode": -2013, "accessToken": "fallback_tok"}),
    )
    forced_sess = signer.try_refresh_session(str(sess_file), force=True, sync_feature_on_retry=False)
    assert forced_sess["accessToken"] == "fallback_tok"
    assert forced_sess["refreshTokenExpired"] is True
    # 已标记 refreshTokenExpired 后不再触发刷新
    assert signer.try_refresh_session(str(sess_file), sync_feature_on_retry=False) is None


def test_activate_terminal_session_feature_and_min_interval(monkeypatch):
    calls = []

    class DummySession:
        def post(self, url, json=None, headers=None, proxies=None, timeout=None):
            calls.append(("POST", url, json.get("inBackground")))
            return SimpleNamespace(status_code=200)

        def get(self, url, headers=None, proxies=None, timeout=None):
            calls.append(("GET", url, None))
            if "/feature" in url:
                return SimpleNamespace(status_code=200, json=lambda: {"guest_token": "gt_from_feature"})
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(signer, "pace_weread_request", lambda url: None)
    signer._LAST_ACTIVE_SYNC_AT.clear()

    ok = signer.activate_terminal_session(
        {"vid": "8899", "accessToken": "tok_x"},
        session=DummySession(),
        include_feature=True,
        in_background=1,
        min_interval=600,
    )
    assert ok is True
    assert signer.get_guest_token() == "gt_from_feature"
    assert any("mobileSync" in u and bg == 1 for m, u, bg in calls if m == "POST")
    assert any("/feature?synckey=0" in u for m, u, _ in calls if m == "GET")

    # 600s 节流窗内再次调用应直接命中节流跳过请求
    calls.clear()
    ok2 = signer.activate_terminal_session(
        {"vid": "8899", "accessToken": "tok_x"},
        session=DummySession(),
        include_feature=True,
        min_interval=600,
    )
    assert ok2 is True
    assert calls == []


def test_account_pool_proactive_and_singleflight_refresh(tmp_path, monkeypatch):
    pool_file = tmp_path / "account_pool.json"
    old_ts = time.time() - (signer.PROACTIVE_REFRESH_INTERVAL + 120)
    pool_file.write_text(json.dumps([{
        "id": "acc_weread_777",
        "vid": "777",
        "accessToken": "tok_old",
        "token": "tok_old",
        "refreshToken": "rf_777",
        "deviceId": "dev_777",
        "deviceName": "Xiaomi 13",
        "status": "active",
        "failures": 0,
        "save_time": old_ts,
        "refreshedAt": old_ts,
        "lastActiveAt": old_ts,
        "refreshTokenExpired": False,
    }]), encoding="utf-8")

    monkeypatch.setattr(pool_mod, "ACCOUNT_POOL_FILE", pool_file)
    monkeypatch.setattr(pool_mod, "get_proxies_dict", lambda: None)
    monkeypatch.setattr(pool_mod, "activate_terminal_session", lambda *a, **kw: True)
    monkeypatch.setattr("backend.waterwall.is_waterwall_active", lambda: False)
    signer.clear_rate_limit()

    refresh_calls = []

    def fake_refresh(rf, **kw):
        refresh_calls.append(kw)
        return 200, {"accessToken": "tok_fresh", "refreshToken": "rf_fresh", "skey": "s_fresh"}

    monkeypatch.setattr(pool_mod, "refresh_access_token", fake_refresh)
    pool = pool_mod.AccountPool()

    # 1. 主动续期触发
    pool.ensure_fresh_accounts_if_needed(in_background=1)
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["in_background"] == 1
    assert refresh_calls[0]["device_name"] == "Xiaomi 13"

    saved = json.loads(pool_file.read_text(encoding="utf-8"))
    assert saved[0]["accessToken"] == "tok_fresh"
    assert saved[0]["success_refreshes"] == 1

    # 2. 单飞去重：并发线程拿旧 tok_old 报 401，不再触发第二次 /login 请求
    pool.report("acc_weread_777", status_code=401, expected_access_token="tok_old")
    assert len(refresh_calls) == 1
