"""旁挂 wechat-mp-tools 的路由。

透传类端点（查询账号、搜号、进程存活）只读；``POST /api/archive/from-mp`` 把
旁挂列出的文章 URL 拼成规范文本后，复用既有 ``POST /api/archive/jobs`` 的归档
任务形状建任务 —— 正文抓取、去重与落盘全部走 ai-media 自己的链路。

旁挂失败一律折叠成 503 ``mp_unavailable``（连未登录也归到这一档，因为对调用方
来说「旁挂没准备好」与「旁挂挂了」处理方式相同：稍后重试或人工介入）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import config, mp_source

router = APIRouter()


def _upstream_failure(exc: Exception) -> HTTPException:
    code, hint = getattr(exc, "code", "unavailable"), str(exc)
    if code == "unauthorized":
        message = f"旁挂未登录，请到 {config.MP_TOOLS_BASE} 完成登录：{hint}"
    elif code == "captcha_required":
        message = f"旁挂需要人机验证，请到 {config.MP_TOOLS_BASE} 完成验证：{hint}"
    elif code == "unreachable":
        message = f"旁挂不可达，请先启动 wechat-mp-tools（{config.MP_TOOLS_BASE}）：{hint}"
    else:
        message = f"旁挂调用失败（{code}）：{hint}"
    raise HTTPException(503, {"error": {"code": "mp_unavailable", "message": message, "details": None}})


def _link_text(articles: list[mp_source.MPArticle]) -> str:
    """按 spec 约定把文章列表压成一行一个 URL 的文本。"""
    return "\n".join(article.link for article in articles if article.link)


@router.post("/api/archive/from-mp", status_code=202)
def archive_from_mp(payload: dict):
    fakeid = payload.get("fakeid")
    if not isinstance(fakeid, str) or not fakeid.strip():
        raise HTTPException(422, "fakeid 必须是字符串")
    count = payload.get("count", 10)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= config.MAX_URLS_PER_JOB:
        raise HTTPException(422, "count 必须是 1..500 的整数")
    keyword = payload.get("keyword")
    if keyword is not None and not isinstance(keyword, str):
        raise HTTPException(422, "keyword 必须是字符串")
    root_id = payload.get("target_root", "wx-archive")
    target_dir = payload.get("target_dir", "")
    if not isinstance(root_id, str) or not isinstance(target_dir, str):
        raise HTTPException(422, "target_root/target_dir 必须是字符串")

    try:
        articles = mp_source.articles(fakeid.strip(), 0, count, keyword)
    except Exception as exc:  # noqa: BLE001 - 旁挂的失败面必须全部折叠成 503
        _upstream_failure(exc)
    if not articles:
        # 旁挂的文章列表在未登录时返回 200 + 空列表（不带 error），所以空结果
        # 要先分清「没登录」和「这个号真的没文章」，否则用户看到的是误导性的
        # 「未返回任何链接」而不是「请先去登录」。
        try:
            info = mp_source.health()
        except Exception:  # noqa: BLE001 - 探活失败时按「原因未知」处理
            info = {}
        if info.get("logged_in") is False:
            hint = info.get("login_message") or "账号池无可用账号"
            raise HTTPException(503, {"error": {
                "code": "mp_unavailable",
                "message": f"旁挂未登录，请到 {config.MP_TOOLS_BASE} 完成登录：{hint}",
                "details": None}})
        raise HTTPException(422, {"error": {"code": "no_mp_urls",
                                            "message": "旁挂未返回任何公众号文章链接", "details": None}})

    # 旁挂可能返回的条数超过本任务上限；先截断再交出去，
    # 否则复用的 _create 会在 extract_urls 之后报「单任务链接超过 500 条」。
    links = [article.link for article in articles][: config.MAX_URLS_PER_JOB]
    text = "\n".join(links)
    from .archive_routes import _create  # 延迟导入，避免两个 router 互相牵连

    job = _create(text, root_id, target_dir)
    return {"id": job["id"], "status": job["status"],
            "article_count": len(links), "total": len(articles)}


@router.get("/api/mp/health")
def mp_health():
    try:
        info = mp_source.health()
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", "unavailable")
        return {"status": "unavailable", "base": config.MP_TOOLS_BASE,
                "code": code, "message": str(exc)}
    return {"status": "ok", "base": config.MP_TOOLS_BASE, **info}


@router.get("/api/mp/accounts")
def mp_accounts():
    try:
        items = mp_source.accounts()
    except Exception as exc:  # noqa: BLE001
        _upstream_failure(exc)
    return {"items": [item.as_dict() for item in items], "total": len(items)}


@router.post("/api/mp/search")
def mp_search(payload: dict):
    keyword = payload.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        raise HTTPException(422, "keyword 必须是字符串")
    try:
        items = mp_source.search(keyword)
    except Exception as exc:  # noqa: BLE001
        _upstream_failure(exc)
    return {"results": [item.as_dict() for item in items], "total": len(items)}
