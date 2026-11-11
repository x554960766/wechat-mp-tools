"""本机单服务入口及请求边界。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import config

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时清掉任务暂存区的崩溃残留，再交给请求处理。"""
    from .archive_maintenance import sweep_temp_files
    sweep_temp_files()
    yield


app = FastAPI(title="ai-media", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        payload = exc.detail
    else:
        codes = {400: "bad_request", 403: "forbidden", 404: "not_found", 409: "conflict",
                 413: "too_large", 422: "invalid_input", 503: "unavailable"}
        payload = {"error": {"code": codes.get(exc.status_code, "request_failed"),
                             "message": str(exc.detail), "details": None}}
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return _error(422, "invalid_input", "请求字段格式或范围无效")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": None}})


@app.middleware("http")
async def local_boundary(request: Request, call_next):
    host = request.headers.get("host", "")
    allowed_hosts = {f"localhost:{config.WX_PORT}", f"127.0.0.1:{config.WX_PORT}"}
    if host not in allowed_hosts:
        return _error(403, "host_forbidden", "只允许本机配置端口访问")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or parsed.netloc not in allowed_hosts or parsed.path or parsed.query or parsed.fragment:
                return _error(403, "origin_forbidden", "只允许同源写入")
        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > config.MAX_BODY_BYTES:
                    return _error(413, "body_too_large", "请求体超过容量限制")
            except ValueError:
                return _error(400, "invalid_content_length", "Content-Length 无效")
        consumed = 0
        chunks = []
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                return _error(400, "client_disconnected", "客户端已断开")
            body = message.get("body", b"")
            consumed += len(body)
            if consumed > config.MAX_BODY_BYTES:
                return _error(413, "body_too_large", "请求体超过容量限制")
            chunks.append(body)
            if not message.get("more_body", False):
                break
        request._body = b"".join(chunks)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", media_type="text/html")


@app.get("/api/health")
def health():
    from datetime import datetime, timezone
    roots = []
    for root in config.MEDIA_ROOTS:
        reachable = Path(root["path"]).exists() if root["type"] == "local" else None
        roots.append({"id": root["id"], "type": root["type"], "reachable": reachable,
                      "checked_at": datetime.now(timezone.utc).isoformat() if reachable is not None else None})
    index_state = "unloaded"
    index_error = None
    if config.INDEX_DB.exists():
        try:
            with sqlite3.connect(config.INDEX_DB) as db:
                db.execute("PRAGMA schema_version").fetchone()
            index_state = "ready"
        except sqlite3.Error as exc:
            index_state, index_error = "failed", str(exc)
    try:
        from .encoder import status as encoder_status
        model = encoder_status()
    except (ImportError, AttributeError):
        model = {"state": "unloaded", "error": None}
    mp_source_state = "unavailable"
    mp_source_info = {"code": "upstream_error", "message": "旁挂探活未完成"}
    try:
        from . import mp_source
        info = mp_source.health()
        mp_source_info = {"code": "ok", "version": info.get("version"),
                          "modules": info.get("modules"),
                          "logged_in": info.get("logged_in"),
                          "login_message": info.get("login_message")}
        mp_source_state = "ok" if info.get("status") == "ok" else "unavailable"
    except Exception as exc:  # noqa: BLE001 - 旁挂不可达不能拖垮健康检查
        mp_source_info = {"code": "unreachable", "message": f"旁挂探活失败：{exc}"}
    return {"model": model, "index": {"state": index_state, "error": index_error},
            "roots": roots, "mp_source": {"status": mp_source_state, "base": config.MP_TOOLS_BASE, **mp_source_info}}


from .archive_routes import router as archive_router  # noqa: E402
from .media_routes import router as media_router  # noqa: E402
from .mp_routes import router as mp_router  # noqa: E402
app.include_router(archive_router)
app.include_router(media_router)
app.include_router(mp_router)
