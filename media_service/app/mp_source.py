"""旁挂 wechat-mp-tools 的 HTTP 客户端。

旁挂（wechat-mp-tools）以独立 Flask 进程跑在 ``MP_TOOLS_BASE``（默认
``http://127.0.0.1:5200``），自己持有微信读书登录态、逆向签名与验证码流程。
本模块只做薄封装：把旁挂的账号／文章列表接口翻译成 ai-media 内部的数据形状
（``MPAccount`` / ``MPArticle``），并把旁挂的失败折叠成本地 ``MPSourceError``。

旁挂失败语义的两个坑，代码里都按事实处理：

* 列表接口即使整条链路失败也**返回 HTTP 200**，体里带 ``error`` 字段，
  所以不能只看状态码；
* -2012 是网关超时（非风控），-2014 是频控退避，-2041 是人机验证。

**本模块不做任何机器绕过**：-2041 只把原始错误透出，由调用方提示用户到
旁挂界面人工完成验证。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from . import config

# 旁挂带 "error" 字段的错误体里，按错误码子串归类，供上层决定提示文案。
_CODE_ORDER = (
    ("-2041", "captcha_required"),
    ("-2014", "rate_limited"),
    ("-2012", "gateway_timeout"),
)


class MPSourceError(RuntimeError):
    """旁挂调用失败。``code`` 是归类码，``message`` 是给人看的原文。

    归类码取值：``timeout`` / ``unreachable`` / ``http_status`` /
    ``bad_response`` / ``captcha_required`` / ``rate_limited`` /
    ``gateway_timeout`` / ``unauthorized`` / ``upstream_error``。
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class MPAccount:
    """旁挂侧的一篇公众号账号。"""

    fakeid: str
    nickname: str = ""
    book_id: str = ""
    signature: str = ""
    cover: str = ""

    def as_dict(self) -> dict:
        return {"fakeid": self.fakeid, "nickname": self.nickname, "book_id": self.book_id,
                "signature": self.signature, "cover": self.cover}


@dataclass(slots=True)
class MPArticle:
    """旁挂侧的一篇公众号文章（只保留 URL 与标题）。

    旁挂 ``/api/articles/list`` 不返回公众号名（条目里的 ``author`` 是文章作者，
    缺失时才回退成账号名，不能当账号名用），所以这里不设 ``account_name``。
    """

    link: str
    title: str = ""


def _classify(text: str) -> str:
    for needle, code in _CODE_ORDER:
        if needle in text:
            return code
    # 旁挂未登录时返回的是「请先在『账号池』页面扫码登录」，不含「未登录」三字，
    # 所以按「登录」判；错误码 -400 是微信读书侧的未授权。
    if "登录" in text or "-400" in text:
        return "unauthorized"
    return "upstream_error"


def _client() -> httpx.Client:
    """构造指向旁挂的客户端。单次请求建一个短连接，避免进程退出/重启时
    残留的连接池状态（旁挂是外部进程，本项目不做它的生命周期管理）。

    ``trust_env=False`` 是刻意的：旁挂按设计跑在本机或内网（默认
    ``127.0.0.1:5200``），不该走代理；而本机 ``all_proxy`` 指向 socks5 且
    ``NO_PROXY`` 含 ``[::1]``，httpx 会在 ``Client()`` 构造期解析这些条目，
    先因缺 socksio 报 ImportError、装了 socksio 又因 ``[::1]`` 报
    ``InvalidURL: Invalid port: ':1]'``。关掉 trust_env 后两条都不再触发。
    """
    return httpx.Client(
        base_url=config.MP_TOOLS_BASE.rstrip("/"),
        timeout=config.MP_TOOLS_TIMEOUT,
        headers={"Accept": "application/json"},
        trust_env=False,
    )


def _call(client: httpx.Client, method: str, path: str, *, params: dict | None = None,
          json: dict | None = None) -> dict:
    """发一次请求并解 JSON；任何一步失败都折叠成 ``MPSourceError``。"""
    try:
        resp = client.request(method, path, params=params, json=json)
    except httpx.TimeoutException as exc:
        raise MPSourceError("timeout", f"旁挂响应超时（{config.MP_TOOLS_TIMEOUT:g}s）") from exc
    except httpx.ConnectError as exc:
        raise MPSourceError("unreachable", f"旁挂不可达：{config.MP_TOOLS_BASE}") from exc
    except httpx.HTTPError as exc:
        raise MPSourceError("network", f"旁挂请求失败：{exc}") from exc

    # 先解体再判状态：旁挂的 401 也带 {"error": "..."}，只看状态码会把「未登录」
    # 误报成 http_status，丢掉可提示用户的信息。
    try:
        payload = resp.json()
    except ValueError as exc:
        raise MPSourceError("bad_response", f"旁挂返回体不是合法 JSON（HTTP {resp.status_code}）") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise MPSourceError(_classify(str(payload["error"])), str(payload["error"]))
    if resp.status_code != 200:
        raise MPSourceError("http_status", f"旁挂返回 HTTP {resp.status_code}")
    if not isinstance(payload, dict):
        raise MPSourceError("bad_response", f"旁挂返回体不是对象：{payload!r}")
    return payload


def _accounts_from(items: list) -> list[MPAccount]:
    out: list[MPAccount] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        fakeid = str(raw.get("fakeid") or "").strip()
        if not fakeid:
            continue
        out.append(MPAccount(
            fakeid=fakeid,
            nickname=str(raw.get("nickname") or ""),
            book_id=str(raw.get("bookId") or raw.get("book_id") or ""),
            signature=str(raw.get("signature") or raw.get("alias") or ""),
            cover=str(raw.get("round_head_img") or raw.get("cover") or ""),
        ))
    return out


def _articles_from(items: list) -> list[MPArticle]:
    """按 ``link`` 去重保序；空链接直接丢（旁挂偶发不完整的条目）。"""
    out: list[MPArticle] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        link = str(raw.get("link") or "").strip()
        if not link or link in seen:
            continue
        seen.add(link)
        out.append(MPArticle(
            link=link,
            title=str(raw.get("title") or ""),
        ))
    return out


def health() -> dict:
    """旁挂进程存活探针：``GET /healthz``，并带上登录态 ``GET /api/auth/status``。

    两个细节按旁挂真实响应处理：

    * ``modules`` 旁挂给的是**整数**（``{"modules": 5, ...}``），旧版本可能是列表，
      两种都归一成数量；
    * 登录态单独探一次。``/healthz`` 不含登录信息，而 ``/api/articles/list`` 在
      未登录时返回 ``200 + 空列表``（不带 ``error``），只看列表分辨不出「没登录」
      和「这个号没文章」。``logged_in`` 在探活失败时为 ``None``（未知），不冒充
      ``False``。
    """
    client = _client()
    payload = _call(client, "GET", "/healthz")
    modules = payload.get("modules")
    if isinstance(modules, list):
        modules = len(modules)
    try:
        auth = _call(client, "GET", "/api/auth/status")
        logged_in = auth.get("logged_in")
        login_message = str(auth.get("message") or "")
    except MPSourceError:
        logged_in, login_message = None, ""
    return {
        "status": str(payload.get("status") or "ok"),
        "version": payload.get("version"),
        "modules": modules,
        "logged_in": logged_in,
        "login_message": login_message,
    }


def search(keyword: str) -> list[MPAccount]:
    """旁挂搜号：``POST /api/accounts/search {keyword}``。"""
    client = _client()
    payload = _call(client, "POST", "/api/accounts/search", json={"keyword": keyword})
    return _accounts_from(payload.get("results") or [])


def listing() -> list[MPAccount]:
    """旁挂收藏库里的账号：``GET /api/accounts``。"""
    client = _client()
    payload = _call(client, "GET", "/api/accounts")
    return _accounts_from(payload.get("accounts") or [])


def user_mps() -> list[MPAccount]:
    """旁挂当前登录用户已关注的账号：``GET /api/accounts/user-mps``。"""
    client = _client()
    payload = _call(client, "GET", "/api/accounts/user-mps")
    return _accounts_from(payload.get("mps") or [])


def accounts() -> list[MPAccount]:
    """旁挂真正可用的公众号：已关注号优先，库内号补后，按 ``fakeid`` 去重保序。

    只读库内号（``GET /api/accounts``）不够：旁挂新装时它是 0 个，而当前登录用户
    已关注的号（``GET /api/accounts/user-mps``）实测 45 个，真正能取文章的正是后者。
    只读前者会让 ``/api/mp/accounts`` 恒空，调用方拿不到 ``fakeid``，``from-mp``
    无从下手。

    未登录时 ``user-mps`` 返回 401 而库内号仍可读（旁挂不要求登录），所以这里只
    吞掉 ``unauthorized`` 并回退库内号；两者都空且确实未登录时再抛出，避免把
    「没登录」答成「没账号」。
    """
    following: list[MPAccount] = []
    unauthorized: MPSourceError | None = None
    try:
        following = user_mps()
    except MPSourceError as exc:
        if exc.code != "unauthorized":
            raise
        unauthorized = exc

    out: list[MPAccount] = []
    seen: set[str] = set()
    for item in [*following, *listing()]:
        if item.fakeid in seen:
            continue
        seen.add(item.fakeid)
        out.append(item)
    if not out and unauthorized is not None:
        raise unauthorized
    return out


def articles(fakeid: str, begin: int = 0, count: int = 10, keyword: str | None = None) -> list[MPArticle]:
    """旁挂某公众号的文章列表：``GET /api/articles/list/<fakeid>``。

    ``begin``/``count`` 是旁挂自己的分页游标，原样透传；返回的 ``link``
    就是公众号文章 URL，可直接喂 ``parse.extract_urls``。
    """
    params = {"begin": begin, "count": count}
    if keyword:
        params["keyword"] = keyword
    client = _client()
    payload = _call(client, "GET", f"/api/articles/list/{fakeid}", params=params)
    return _articles_from(payload.get("articles") or [])
