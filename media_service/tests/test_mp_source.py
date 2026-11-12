"""旁挂 wechat-mp-tools HTTP 客户端与路由的单元测试。

不依赖旁挂进程：用 fake ``httpx.Client`` 返回预定响应体，覆盖 URL 构造、JSON
解析、错误体分类、from-mp 到 ``_create`` 的拼接、无链接 422 与计数截断。
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app import archive_routes, config, mp_routes, mp_source
from app.main import app


# —— 假 httpx.Client ——

class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    """记录调用参数的假客户端；``requests`` = [(method, path, params, json), ...]。

    ``by_path`` 用来让不同路径返回不同响应体（真实旁挂 /healthz 与
    /api/auth/status 的形状不同）。
    """

    def __init__(self, response, by_path: dict | None = None):
        self.response = response
        self.by_path = by_path or {}
        self.requests: list[tuple] = []

    def request(self, method, path, params=None, json=None):  # noqa: A002 - 对齐 httpx 参数名
        self.requests.append((method, path, params, json))
        if isinstance(self.response, httpx.HTTPError):
            raise self.response
        if path in self.by_path:
            resp = self.by_path[path]
            if isinstance(resp, httpx.HTTPError):
                raise resp
            return resp
        return self.response


def _install(monkeypatch, payload, status_code: int = 200, by_path: dict | None = None):
    # 传输错误（httpx.HTTPError 子类：timeout/connect/网络）发生在请求期，与真实
    # httpx 一致；其它异常发生在响应解析期（resp.json()）。JSON 形状错误同样在解析期。
    if isinstance(payload, httpx.HTTPError):
        client = FakeClient(payload, by_path)
    else:
        client = FakeClient(FakeResponse(payload, status_code), by_path)
    monkeypatch.setattr(mp_source.httpx, "Client", lambda **kwargs: client)
    return client


def _articles_payload(*links):
    """旁挂 ``/api/articles/list`` 的真实条目形状（无 account_name 字段）。"""
    return {"articles": [{"link": link, "title": f"t{i}", "author": "作者", "digest": "",
                          "cover": "", "update_time": 0, "is_original": False}
                         for i, link in enumerate(links)],
            "total": len(links), "can_msg_continue": False, "begin": 0, "count": len(links)}


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


# —— URL 构造 与 JSON 解析 ——

def test_search_posts_keyword_and_parses_results(monkeypatch):
    client = _install(monkeypatch, {"results": [{"fakeid": "@f", "nickname": "号", "bookId": "1",
                                                "alias": "备注", "round_head_img": "img"}],
                                    "total": 1})
    items = mp_source.search("机器")
    assert client.requests == [("POST", "/api/accounts/search", None, {"keyword": "机器"})]
    assert [item.fakeid for item in items] == ["@f"]
    assert items[0].book_id == "1" and items[0].cover == "img"
    assert items[0].as_dict() == {"fakeid": "@f", "nickname": "号", "book_id": "1",
                                  "signature": "备注", "cover": "img"}


def test_listing_reads_accounts_key(monkeypatch):
    client = _install(monkeypatch, {"accounts": [{"fakeid": "@a", "nickname": "n"}], "total": 1})
    assert [item.fakeid for item in mp_source.listing()] == ["@a"]
    assert client.requests == [("GET", "/api/accounts", None, None)]


def test_user_mps_reads_mps_key(monkeypatch):
    client = _install(monkeypatch, {"mps": [{"fakeid": "@u"}], "total": 1})
    assert [item.fakeid for item in mp_source.user_mps()] == ["@u"]
    assert client.requests == [("GET", "/api/accounts/user-mps", None, None)]


def test_accounts_returns_following_when_library_empty(monkeypatch):
    """实测旁挂库内号 0 个、已关注号 45 个：只读库内号会让 /api/mp/accounts 恒空。"""
    _install(monkeypatch, {}, by_path={
        "/api/accounts/user-mps": FakeResponse({"mps": [{"fakeid": "@u1"}], "total": 1}),
        "/api/accounts": FakeResponse({"accounts": [], "total": 0}),
    })
    assert [item.fakeid for item in mp_source.accounts()] == ["@u1"]


def test_accounts_merges_following_first_and_dedupes(monkeypatch):
    client = _install(monkeypatch, {}, by_path={
        "/api/accounts/user-mps": FakeResponse({"mps": [{"fakeid": "@u1"}, {"fakeid": "@u2"}], "total": 2}),
        "/api/accounts": FakeResponse({"accounts": [{"fakeid": "@u2"}, {"fakeid": "@l1"}], "total": 2}),
    })
    assert [item.fakeid for item in mp_source.accounts()] == ["@u1", "@u2", "@l1"]
    assert [call[1] for call in client.requests] == ["/api/accounts/user-mps", "/api/accounts"]


def test_accounts_falls_back_to_library_when_following_unauthorized(monkeypatch):
    _install(monkeypatch, {}, by_path={
        "/api/accounts/user-mps": FakeResponse({"error": "请先在『账号池』页面扫码登录"}, 401),
        "/api/accounts": FakeResponse({"accounts": [{"fakeid": "@l1"}], "total": 1}),
    })
    assert [item.fakeid for item in mp_source.accounts()] == ["@l1"]


def test_accounts_raises_unauthorized_when_both_empty_and_not_logged_in(monkeypatch):
    _install(monkeypatch, {}, by_path={
        "/api/accounts/user-mps": FakeResponse({"error": "请先在『账号池』页面扫码登录"}, 401),
        "/api/accounts": FakeResponse({"accounts": [], "total": 0}),
    })
    with pytest.raises(mp_source.MPSourceError) as exc:
        mp_source.accounts()
    assert exc.value.code == "unauthorized"


def test_articles_passes_begin_count_and_keyword(monkeypatch):
    client = _install(monkeypatch, _articles_payload("https://mp.weixin.qq.com/s/A",
                                                     "https://mp.weixin.qq.com/s/B"))
    items = mp_source.articles("@fake", 5, 20, "机器")
    assert client.requests == [("GET", "/api/articles/list/@fake",
                                {"begin": 5, "count": 20, "keyword": "机器"}, None)]
    assert [item.link for item in items] == ["https://mp.weixin.qq.com/s/A",
                                             "https://mp.weixin.qq.com/s/B"]


def test_articles_omits_keyword_when_absent(monkeypatch):
    client = _install(monkeypatch, _articles_payload("https://mp.weixin.qq.com/s/A"))
    mp_source.articles("@fake", 0, 10)
    assert client.requests[0][2] == {"begin": 0, "count": 10}


def test_articles_dedupes_links_and_drops_empty(monkeypatch):
    payload = _articles_payload("https://mp.weixin.qq.com/s/A", "https://mp.weixin.qq.com/s/A", "")
    _install(monkeypatch, payload)
    assert [item.link for item in mp_source.articles("@fake", 0, 10)] == ["https://mp.weixin.qq.com/s/A"]


def test_health_reads_version_and_module_count(monkeypatch):
    client = _install(monkeypatch, {"status": "ok", "version": "9.9", "modules": ["a", "b"]})
    info = mp_source.health()
    assert info["status"] == "ok" and info["version"] == "9.9" and info["modules"] == 2
    assert client.requests == [("GET", "/healthz", None, None),
                               ("GET", "/api/auth/status", None, None)]


def test_health_accepts_integer_module_count(monkeypatch):
    """真实旁挂 /healthz 返回 {"modules": 5, ...}（整数），不是列表。"""
    _install(monkeypatch, {"status": "ok", "version": "2.3.6", "modules": 5})
    assert mp_source.health()["modules"] == 5


def test_health_reports_login_state(monkeypatch):
    _install(monkeypatch, {"status": "ok", "version": "2.3.6", "modules": 5},
             by_path={"/api/auth/status": FakeResponse(
                 {"logged_in": False, "message": "未登录，请到账号池扫码", "login_state": {},
                  "pool_summary": {"total": 0}})})
    info = mp_source.health()
    assert info["logged_in"] is False
    assert info["login_message"] == "未登录，请到账号池扫码"


def test_health_login_state_unknown_when_auth_probe_fails(monkeypatch):
    """探活失败不能冒充「未登录」，否则会把故障说成登录问题。"""
    _install(monkeypatch, {"status": "ok", "version": "2.3.6", "modules": 5},
             by_path={"/api/auth/status": httpx.ConnectError("boom")})
    info = mp_source.health()
    assert info["logged_in"] is None and info["login_message"] == ""


# —— 错误体分类 ——

@pytest.mark.parametrize("needle", ["-2041", "-2014", "-2012", "微信读书未登录", "哦哦"])
def test_error_body_classification(monkeypatch, needle):
    _install(monkeypatch, {"error": f"{needle} 情况", "articles": [], "total": 0, "count": 0})
    expected = {"-2041": "captcha_required", "-2014": "rate_limited", "-2012": "gateway_timeout",
                "微信读书未登录": "unauthorized"}.get(needle, "upstream_error")
    with pytest.raises(mp_source.MPSourceError) as state:
        mp_source.articles("@fake", 0, 10)
    assert state.value.code == expected


def test_non_200_status_becomes_http_status(monkeypatch):
    _install(monkeypatch, "ignored", status_code=500)
    with pytest.raises(mp_source.MPSourceError) as state:
        mp_source.listing()
    assert state.value.code == "http_status"


def test_non_200_with_error_body_is_classified_not_http_status(monkeypatch):
    """旁挂未登录时是 401 + {"error": "请先在『账号池』页面扫码登录"}；
    只看状态码会丢掉「去登录」这个可行动信息。"""
    _install(monkeypatch, {"error": "微信读书账号池中暂无可用账号，请先在『账号池』页面扫码登录"},
              status_code=401)
    with pytest.raises(mp_source.MPSourceError) as state:
        mp_source.listing()
    assert state.value.code == "unauthorized"


@pytest.mark.parametrize("needle", ["请先在『账号池』页面扫码登录", "未登录", "登录"])
def test_login_word_marks_unauthorized(monkeypatch, needle):
    _install(monkeypatch, {"error": needle}, status_code=200)
    with pytest.raises(mp_source.MPSourceError) as state:
        mp_source.listing()
    assert state.value.code == "unauthorized"


def test_non_json_body_becomes_bad_response(monkeypatch):
    _install(monkeypatch, ValueError("not json"), status_code=200)
    with pytest.raises(mp_source.MPSourceError) as state:
        mp_source.listing()
    assert state.value.code == "bad_response"


def test_non_object_payload_becomes_bad_response(monkeypatch):
    _install(monkeypatch, [1, 2, 3])
    with pytest.raises(mp_source.MPSourceError) as state:
        mp_source.listing()
    assert state.value.code == "bad_response"


@pytest.mark.parametrize("exc, code", [
    (httpx.TimeoutException("boom"), "timeout"),
    (httpx.ConnectError("boom"), "unreachable"),
    (httpx.HTTPError("boom"), "network"),
])
def test_transport_failures_are_classified(monkeypatch, exc, code):
    _install(monkeypatch, exc)
    with pytest.raises(mp_source.MPSourceError) as state:
        mp_source.listing()
    assert state.value.code == code


# —— from-mp 路由到 _create 的拼接 ——

def _captured_create(monkeypatch, captured: dict):
    def fake_create(text, root_id, target_dir):
        captured.update(text=text, root_id=root_id, target_dir=target_dir)
        return {"id": "job-1", "status": "pending"}
    monkeypatch.setattr(archive_routes, "_create", fake_create)


def test_from_mp_creates_job_from_joined_links(monkeypatch, client):
    links = ["https://mp.weixin.qq.com/s/A", "https://mp.weixin.qq.com/s/B"]
    monkeypatch.setattr(mp_source, "articles", lambda *a, **k: [
        mp_source.MPArticle(link=link, title="t") for link in links])
    captured: dict = {}
    _captured_create(monkeypatch, captured)

    body = client.post("/api/archive/from-mp",
                       json={"fakeid": "@fake", "count": 2, "target_root": "wx-archive",
                             "target_dir": "m"})
    assert body.status_code == 202
    assert body.json() == {"id": "job-1", "status": "pending", "article_count": 2, "total": 2}
    assert captured["text"] == "\n".join(links)
    assert captured["root_id"] == "wx-archive" and captured["target_dir"] == "m"


def test_from_mp_rejects_missing_fakeid(client):
    assert client.post("/api/archive/from-mp", json={"count": 2}).status_code == 422


def test_from_mp_no_urls_is_422_no_mp_urls(monkeypatch, client):
    monkeypatch.setattr(mp_source, "articles", lambda *a, **k: [])
    monkeypatch.setattr(mp_source, "health", lambda: {"logged_in": True})
    body = client.post("/api/archive/from-mp", json={"fakeid": "@fake"})
    assert body.status_code == 422
    assert body.json()["error"]["code"] == "no_mp_urls"


def test_from_mp_empty_while_not_logged_in_is_503(monkeypatch, client):
    """旁挂未登录时文章列表返回 200 + 空列表，必须报「去登录」而不是「没链接」。"""
    monkeypatch.setattr(mp_source, "articles", lambda *a, **k: [])
    monkeypatch.setattr(mp_source, "health",
                        lambda: {"logged_in": False, "login_message": "请先在『账号池』页面扫码登录"})
    body = client.post("/api/archive/from-mp", json={"fakeid": "@fake"})
    assert body.status_code == 503
    assert body.json()["error"]["code"] == "mp_unavailable"
    assert "完成登录" in body.json()["error"]["message"]


def test_from_mp_truncates_links_to_max_urls_per_job(monkeypatch, client):
    links = [f"https://mp.weixin.qq.com/s/{i}" for i in range(config.MAX_URLS_PER_JOB + 100)]
    monkeypatch.setattr(mp_source, "articles", lambda *a, **k: [
        mp_source.MPArticle(link=link) for link in links])
    captured: dict = {}
    _captured_create(monkeypatch, captured)

    body = client.post("/api/archive/from-mp", json={"fakeid": "@fake"})
    assert body.status_code == 202
    assert body.json()["article_count"] == config.MAX_URLS_PER_JOB
    assert body.json()["total"] == len(links)
    captured_links = captured["text"].split("\n")
    assert len(captured_links) == config.MAX_URLS_PER_JOB
    assert captured_links[0] == links[0]


def test_from_mp_upstream_unauthorized_becomes_503(monkeypatch, client):
    def boom(*_a, **_k):
        raise mp_source.MPSourceError("unauthorized", "微信读书未登录")
    monkeypatch.setattr(mp_source, "articles", boom)
    body = client.post("/api/archive/from-mp", json={"fakeid": "@fake"})
    assert body.status_code == 503
    assert body.json()["error"]["code"] == "mp_unavailable"
    assert "完成登录" in body.json()["error"]["message"]


# —— 只读端点 ——

def test_mp_health_surfaces_sidecar_state(monkeypatch, client):
    monkeypatch.setattr(mp_source, "health", lambda: {"status": "ok", "version": "9.9", "modules": 2})
    body = client.get("/api/mp/health")
    assert body.status_code == 200
    payload = body.json()
    assert payload["status"] == "ok" and payload["version"] == "9.9" and payload["base"] == config.MP_TOOLS_BASE


def test_mp_health_reports_unavailable_without_raising(monkeypatch, client):
    def boom():
        raise mp_source.MPSourceError("unreachable", "旁挂不可达")
    monkeypatch.setattr(mp_source, "health", boom)
    body = client.get("/api/mp/health")
    assert body.status_code == 200
    payload = body.json()
    assert payload["status"] == "unavailable" and payload["code"] == "unreachable"


def test_mp_accounts_passthrough(monkeypatch, client):
    monkeypatch.setattr(mp_source, "accounts", lambda: [
        mp_source.MPAccount(fakeid="@f", nickname="号")])
    body = client.get("/api/mp/accounts")
    assert body.status_code == 200
    assert body.json() == {"items": [{"fakeid": "@f", "nickname": "号", "book_id": "",
                                      "signature": "", "cover": ""}], "total": 1}


def test_mp_search_requires_keyword(client):
    assert client.post("/api/mp/search", json={"keyword": "  "}).status_code == 422


def test_mp_search_passthrough(monkeypatch, client):
    monkeypatch.setattr(mp_source, "search", lambda kw: [mp_source.MPAccount(fakeid="@f", nickname=kw)])
    body = client.post("/api/mp/search", json={"keyword": "机器"})
    assert body.status_code == 200
    assert body.json() == {"results": [{"fakeid": "@f", "nickname": "机器", "book_id": "",
                                        "signature": "", "cover": ""}], "total": 1}


def test_app_health_includes_mp_source_segment(monkeypatch, client):
    monkeypatch.setattr(mp_source, "health",
                        lambda: {"status": "ok", "version": "9.9", "modules": 2})
    payload = client.get("/api/health").json()
    assert set(payload) == {"model", "index", "roots", "mp_source"}
    assert payload["mp_source"]["status"] == "ok" and payload["mp_source"]["version"] == "9.9"
