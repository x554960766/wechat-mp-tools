"""AI 媒体服务宿主进程管理器与 /media/* 反向代理层。

参考 Spec: ``datasets/20 开发规格（specs）/ai-media/wmp-hosted-media-service.md`` §3.

设计要点
--------
- wmp 启动时异步 spawn uvicorn 子进程，不阻塞 Flask 启动。
- ``/media/<rest>`` 剥前缀路由到 ``http://127.0.0.1:{port}/<rest}``。
- 必须重写 Host 与 origin 头，否则媒体 ``local_boundary`` 中间件会 403。
- 流式转发 ``/media/media/file`` 与 ``/media/archive/jobs/{id}/download``，
  其余响应体全量透传。
- 错误码：503 media_unavailable / 409 ip_whitelist / 400 path_invalid /
  413 body_too_large / 404 proxy_404。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.parse

import requests
from flask import Blueprint, Response, request

__all__ = ["MediaHost", "init_blueprint", "MEDIA_PORT"]

MEDIA_PORT = int(os.getenv("MEDIA_PORT", "8765"))
LOCALHOST_ORIGIN = f"http://127.0.0.1:{MEDIA_PORT}"

# 媒体服务种子数据目录（宿主注入 AI_MEDIA_DATA_DIR 覆盖）
_DEFAULT_DATA_DIR = os.path.join(os.getcwd(), "data", "media")

# 进程退出退避序列
_BACKOFF = (2, 5, 15)

log = logging.getLogger("media_host")


class MediaHost:
    """管理媒体服务子进程生命周期的状态机。

    状态：not_installed / stopped / starting / running / unavailable / crashed
    """

    def __init__(
        self,
        port: int = MEDIA_PORT,
        media_root: str | None = None,
        venv_python: str | None = None,
        cmd: list[str] | None = None,
        data_dir: str | None = None,
        timeout: float = 120.0,
        autostart: bool | None = None,
        max_restarts: int = 3,
    ) -> None:
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.media_root = media_root or os.path.join(os.getcwd(), "media_service")
        self.venv_python = venv_python or os.path.join(self.media_root, ".venv", "bin", "python")
        self.cmd = cmd or [
            self.venv_python, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port),
        ]
        self.data_dir = data_dir or os.path.join(os.getcwd(), "data", "media")
        self.timeout = timeout
        self.autostart = autostart if autostart is not None else os.getenv("MEDIA_AUTOSTART", "1") != "0"
        self.max_restarts = max_restarts

        self._proc: subprocess.Popen | None = None
        self._log_file: object | None = None
        self._log_path = os.path.join(tempfile.gettempdir(), f"wmp-media-{port}.log")
        # 上一次运行残留、但仍在跑的媒体服务 pid（跨进程重启时复用/清理）
        self._external_pid: int | None = None
        self._lock = threading.Lock()
        self._state = "stopped"
        self._restarts = 0
        self._last_error: str | None = None
        self._poller: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._healthy: bool = False
        self._stopping: bool = False

    # ── 状态与生命周期 ────────────────────────────────────

    def status(self) -> dict:
        """供 /healthz 使用的精简状态快照。"""
        with self._lock:
            return {
                "state": self._state,
                "pid": self._proc.pid if self._proc else None,
                "port": self.port,
                "ready": self._healthy,
                "restarts": self._restarts,
                "last_error": self._last_error,
            }

    def start_in_thread(self) -> None:
        """后台 spawn，不阻塞 Flask 启动。"""
        if not self.autostart:
            self._state = "stopped"
            return
        if self._is_venv_ready() is False:
            self._state = "not_installed"
            self._last_error = f"venv 缺失: {self.venv_python}"
            return
        self._poller = threading.Thread(target=self._spawn_and_wait, daemon=True)
        self._poller.start()

    def start(self) -> bool:
        """同步启动并轮询到就绪（用于测试或手动拉起）。"""
        self._spawn()
        return self._wait_ready()

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
            if self._external_pid:
                # 复用的是上一次 wmp 进程留下的子进程，退出时一并收掉，
                # 否则它占着端口，下次启动只能不断 bind 失败。
                self._terminate_pid(self._external_pid)
                self._external_pid = None
            self._close_log()
            self._remove_pid_file()
            self._state = "stopped"
            self._healthy = False

    def _start_watchdog(self) -> None:
        """启动 watchdog：监控已启动进程，异常退出时按退避重启。"""
        if self._watchdog is not None:
            return
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

    def _watchdog_loop(self) -> None:
        """周期性检查子进程存活，异常退出时触发退避重启。"""
        while True:
            time.sleep(2)
            with self._lock:
                if self._stopping:
                    return
                proc = self._proc
            if proc is None:
                return
            ret = proc.poll()
            if ret is not None:
                # 进程异常退出
                self._last_error = f"进程退出 (code={ret}){self._stderr_tail()}"
                with self._lock:
                    self._healthy = False
                    self._state = "crashed"
                self._restart_with_backoff()
                # 退避重启用尽后 _proc 仍是那具死进程；不退出会每 2 秒再重启一轮，
                # restarts 无限增长。状态已经是 unavailable，交给下次显式启动。
                with self._lock:
                    if self._state == "unavailable":
                        self._proc = None
                        return

    def _restart_with_backoff(self) -> None:
        """按退避序列重启崩溃的进程，最多 max_restarts 次。"""
        for delay in _BACKOFF[: self.max_restarts]:
            time.sleep(delay)
            with self._lock:
                if self._stopping:
                    self._state = "stopped"
                    self._healthy = False
                    self._proc = None
                    return
                # 清除旧进程
                if self._proc and self._proc.poll() is not None:
                    self._proc = None
            # 重新拉起
            self._spawn()
            if self._wait_ready():
                with self._lock:
                    self._state = "running"
                # 重启成功，恢复 watchdog
                self._start_watchdog()
                return
        with self._lock:
            self._state = "unavailable"
            self._last_error = "重启次数耗尽，媒体服务不可用"

    # ── 私有 ──────────────────────────────────────────────

    def _is_venv_ready(self) -> bool:
        if not os.path.isfile(self.venv_python):
            return False
        try:
            r = subprocess.run(
                [self.venv_python, "-c", "import fastapi, uvicorn; print('ok')"],
                capture_output=True, text=True, timeout=15,
            )
            return r.returncode == 0 and "ok" in r.stdout
        except Exception:
            return False

    def _media_root_env(self) -> list[dict]:
        """构造注入子进程的 AI_MEDIA_ROOTS（JSON 数组）。

        根按三层合并，同 id 后者覆盖前者：
        1. 已有索引的 ``index_meta.root_bindings_json``——升级用户手里
           ``data/media/index.sqlite`` 是用旧根（wx-archive / iphone /
           volume-archive）建的，只换根会让媒体服务抛
           ``root_binding_mismatch`` 起不来，继承旧根才能继续用旧索引；
        2. 外部显式配置的 ``AI_MEDIA_ROOTS``；
        3. wmp 下载落图目录 articles_full（与 backend/config.py 的
           OUTPUT_DIR = <repo>/data/articles_full 对齐）。
        """
        repo_root = os.path.dirname(os.path.abspath(self.media_root))
        articles_root = {
            "id": "articles-full",
            "type": "local",
            "path": os.path.join(repo_root, "data", "articles_full"),
        }
        merged: dict[str, dict] = {}
        for root in self._indexed_roots() + self._configured_roots():
            root_id = root.get("id")
            if root_id:
                merged[root_id] = root
        merged.setdefault("articles-full", articles_root)
        return list(merged.values())

    def _indexed_roots(self) -> list[dict]:
        """只读已有索引里记录的根绑定，仅取 id/type/path/exclude_paths。"""
        db_path = os.path.join(self.data_dir, "index.sqlite")
        if not os.path.isfile(db_path):
            return []
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as conn:
                row = conn.execute(
                    "SELECT root_bindings_json FROM index_meta WHERE id=1"
                ).fetchone()
        except sqlite3.Error as exc:
            log.warning("读取已有索引根绑定失败（忽略）: %s", exc)
            return []
        if not row or not row[0]:
            return []
        try:
            bindings = json.loads(row[0])
        except json.JSONDecodeError:
            return []
        roots: list[dict] = []
        for root_id, binding in (bindings or {}).items():
            if not isinstance(binding, dict) or not binding.get("path"):
                continue
            root: dict = {
                "id": root_id,
                "type": binding.get("type", "local"),
                "path": binding["path"],
            }
            if binding.get("exclude_paths"):
                root["exclude_paths"] = binding["exclude_paths"]
            # 旧索引不记 writable；zspace 根是归档写入位，其余按只读处理。
            if root["type"] == "zspace":
                root["writable"] = True
            roots.append(root)
        return roots

    @staticmethod
    def _configured_roots() -> list[dict]:
        """读取外部显式配置的 AI_MEDIA_ROOTS；非法 JSON 视为未配置。"""
        raw = os.environ.get("AI_MEDIA_ROOTS")
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("AI_MEDIA_ROOTS 不是合法 JSON，已忽略")
            return []
        if not isinstance(parsed, list):
            return []
        return [r for r in parsed if isinstance(r, dict) and r.get("id")]

    def _spawn(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            if self._adopt_orphan():
                return
            env = dict(os.environ)
            env["WX_PORT"] = str(self.port)
            env["AI_MEDIA_DATA_DIR"] = self.data_dir
            env["MP_TOOLS_BASE"] = os.getenv("MP_TOOLS_BASE", "http://127.0.0.1:5200")
            env["AI_MEDIA_ROOTS"] = json.dumps(self._media_root_env(), ensure_ascii=False)
            # 子进程启动失败（如索引指纹不匹配）只在 stderr 里说原因，
            # 丢掉 stderr 会让状态里只剩一句无信息量的「进程已退出 (code=1)」。
            try:
                self._close_log()
                self._log_file = open(self._log_path, "wb")
            except OSError:
                self._log_file = None
            self._proc = subprocess.Popen(
                self.cmd,
                cwd=self.media_root,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=self._log_file if self._log_file else subprocess.DEVNULL,
            )
            self._state = "starting"
            self._restarts += 1
            self._last_error = None
            self._write_pid_file(self._proc.pid)

    def _pid_file(self) -> str:
        return os.path.join(self.data_dir, "media.pid")

    def _read_pid_file(self) -> int | None:
        try:
            with open(self._pid_file(), "r", encoding="utf-8") as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    def _write_pid_file(self, pid: int) -> None:
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self._pid_file(), "w", encoding="utf-8") as fh:
                fh.write(str(pid))
        except OSError as exc:
            log.warning("写媒体服务 pid 文件失败（忽略）: %s", exc)

    def _remove_pid_file(self) -> None:
        try:
            os.remove(self._pid_file())
        except OSError:
            pass

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _port_open(self, timeout: float = 0.5) -> bool:
        """端口上是否有人监听。

        用于确认「pid 文件里的进程」是否还占着媒体端口——这是判断它
        是不是我们上次遗留的子进程的唯一可靠依据（不依赖 ps）。
        """
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def _adopt_orphan(self) -> bool:
        """复用上次 wmp 进程留下的媒体子进程。

        wmp 被强杀时子进程不会跟着退出，端口被占住，新宿主一 spawn 就
        ``address already in use``，重试多少轮都没用。这里按 pid 文件认领：
        pid 还活着且健康就接管（退出时一并收掉），否则杀掉重起。

        身份判断不查 ``ps``（沙箱里可能被禁），而是「端口上答得出 /api/health」
        ——这个端口本来就是媒体服务的专属端口。
        """
        pid = self._read_pid_file()
        if pid and self._pid_alive(pid):
            if self._is_healthy():
                self._external_pid = pid
                self._state = "running"
                self._healthy = True
                self._last_error = None
                log.warning("复用已在运行的媒体服务 pid=%s（上次 wmp 退出时未清理）", pid)
                return True
            # pid 文件可能指向已被系统复用给无关进程的 PID：端口没人监听时
            # 不能按 pid 直接 kill，否则会误伤。只有端口仍被占用，才认定它
            # 是上次遗留、卡死不响应的媒体子进程。
            if self._port_open():
                self._terminate_pid(pid)
            self._remove_pid_file()
            return False
        if pid:
            self._remove_pid_file()
        if self._is_healthy():
            # pid 文件丢了但端口上确有媒体服务：可用就先复用，退出时管不了它。
            self._state = "running"
            self._healthy = True
            self._last_error = None
            log.warning("端口 %s 上已有媒体服务在运行，直接复用", self.port)
            return True
        return False

    @staticmethod
    def _terminate_pid(pid: int, timeout: float = 5.0) -> None:
        try:
            os.kill(pid, 15)
        except OSError:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.2)
        try:
            os.kill(pid, 9)
        except OSError:
            pass

    def _spawn_and_wait(self) -> None:
        self._spawn()
        if self._wait_ready():
            self._start_watchdog()
            return
        # 退避重启
        for delay in _BACKOFF[: self.max_restarts]:
            time.sleep(delay)
            with self._lock:
                if self._proc and self._proc.poll() is not None:
                    # 进程已退出，重新拉起
                    self._proc = None
            self._spawn()
            if self._wait_ready():
                self._start_watchdog()
                return
        with self._lock:
            self._state = "unavailable"
            self._last_error = "重试次数耗尽或进程持续重启失败"

    def _wait_ready(self, timeout: float | None = None) -> bool:
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            if self._proc is None and self._external_pid:
                # 复用上一次运行留下的子进程：没有 Popen 句柄，只能探健康。
                if self._is_healthy():
                    with self._lock:
                        self._state = "running"
                        self._healthy = True
                    return True
                time.sleep(0.3)
                continue
            pid = self._proc.pid if self._proc else None
            if pid is None:
                return False
            proc = self._proc
            if proc and proc.poll() is not None:
                self._last_error = (
                    f"进程已退出 (code={proc.returncode}){self._stderr_tail()}"
                )
                return False
            if self._is_healthy():
                with self._lock:
                    self._state = "running"
                    self._healthy = True
                return True
            time.sleep(0.3)
        with self._lock:
            self._state = "unavailable"
            self._last_error = f"健康检查超时 ({self.timeout}s)"
        return False

    def _stderr_tail(self, max_lines: int = 6, max_chars: int = 600) -> str:
        """取子进程 stderr 末尾几行，用于 last_error 诊断。

        子进程被 SIGKILL 时可能还没来得及 flush，读不到就返回空串。
        """
        try:
            if self._log_file:
                self._log_file.flush()
            with open(self._log_path, "rb") as fh:
                data = fh.read()[-8192:]
        except OSError:
            return ""
        text = data.decode("utf-8", "replace").strip()
        if not text:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        tail = " | ".join(lines[-max_lines:])
        return f": {tail[:max_chars]}"

    def _close_log(self) -> None:
        try:
            if self._log_file:
                self._log_file.close()
        except Exception:
            pass
        self._log_file = None

    def _is_healthy(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/health", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass


# ── Flask 蓝图 ────────────────────────────────────────────

_ALLOWED_METHODS = ("GET", "POST", "DELETE", "PUT")

# 触发流式转发的路径片段（GET 无 Content-Length）
_STREAM_PREFIXES = (
    "/media/file",
    "/archive/jobs/",
)


def _is_stream_path(rest: str) -> bool:
    """/media/api/media/file 和 /media/archive/jobs/*/download 需要流式。"""
    norm = rest.lstrip("/")
    return (
        norm.startswith("media/file")
        or norm.startswith("api/media/file")
        or ("/archive/jobs/" in norm and "download" in norm)
    )


def init_blueprint(app, host: MediaHost | None = None) -> Blueprint:
    """注册 /media/* 反向代理蓝图。

    必须在 SPA 兜底 ``/<path:path>`` 之前调用。

    若 app 上已有 ``MEDIA_HOST_OBJ``，复用之；否则 lazy 创建一个。
    """
    if host is None:
        host = getattr(app, "config", {}).get("MEDIA_HOST_OBJ")
    if host is None:
        host = MediaHost()
        app.config["MEDIA_HOST_OBJ"] = host

    # 下载线程落盘后靠这个回调触发扫描，不能依赖 Flask 请求上下文。
    from backend import media_sync

    media_sync.set_host_provider(lambda: host)
    # 只注册一次退出清理；放在 before_app_request 里会对每个请求重复注册。
    atexit.register(host.stop)

    bp = Blueprint("media", __name__, url_prefix="/media")

    @bp.route("/", methods=list(_ALLOWED_METHODS))
    @bp.route("/<path:path>", methods=list(_ALLOWED_METHODS))
    def proxy(path: str = ""):
        st = host.status()
        if st["state"] in ("stopped", "unavailable", "crashed", "not_installed") or not st["ready"]:
            return Response(
                json_error("media_unavailable", "媒体服务未就绪"),
                status=503,
                content_type="application/json",
            )

        # 重组上游 URL（剥 /media 前缀）。查询串必须原样带上：
        # /media/api/media/thumb?root=..&relpath=..&version=..&size=360 里的
        # version/size 是媒体服务的必填参数，丢了就是 422 invalid_input。
        upstream = f"{host.base_url}/{path}"
        if request.query_string:
            # query_string 已是百分号编码的字节串，latin-1 解码可保持原字节不变。
            upstream = f"{upstream}?{request.query_string.decode('latin-1')}"

        # 只认同 TCP 对端地址：X-Forwarded-For 是调用方可伪造的，不能作为白名单依据。
        if not _is_local_ip(request.remote_addr or ""):
            return Response(json_error("ip_whitelist", "非本地客户端"), status=409, content_type="application/json")

        # 校验 body 大小（媒体历史上限 32MB）
        cl_header = request.headers.get("Content-Length")
        if cl_header and request.method in ("POST", "PUT"):
            try:
                if int(cl_header) > 32 * 1024 * 1024:
                    return Response(json_error("body_too_large", "请求体超限"), status=413, content_type="application/json")
            except ValueError:
                pass

        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "origin", "content-length")
        }
        headers["Host"] = f"127.0.0.1:{host.port}"
        headers["Origin"] = f"http://127.0.0.1:{host.port}"

        try:
            if _is_stream_path(path) and request.method == "GET":
                return _stream_proxy(request, upstream, headers, host)
            r = requests.request(
                request.method, upstream,
                headers=headers,
                data=request.get_data(),
                stream=False,
                timeout=(10, 60),
                allow_redirects=False,
            )
            resp_headers = _filter_resp_headers(r.headers)
            return Response(r.content, status=r.status_code, headers=resp_headers)
        except requests.exceptions.ConnectionError:
            return Response(json_error("media_unavailable", "媒体服务连接失败"), status=503, content_type="application/json")
        except Exception as exc:
            host._last_error = str(exc)
            with host._lock:
                host._state = "crashed"
            return Response(json_error("proxy_failed", str(exc)), status=502, content_type="application/json")

    if hasattr(app, "register_blueprint"):
        # 延迟到 register_blueprint 阶段由调用方完成，此处返回蓝图
        pass
    return bp


def _is_local_ip(addr: str | None) -> bool:
    if not addr:
        return False
    host = (addr.split(",")[0] if "," in addr else addr).strip()
    try:
        return ipaddress_ip(host)
    except Exception:
        return host in ("127.0.0.1", "localhost", "::1")


def ipaddress_ip(host: str) -> bool:
    import ipaddress
    return ipaddress.ip_address(host).is_loopback


def _filter_resp_headers(h) -> dict[str, str]:
    # uvicorn 的响应头是小写的（content-type），不能按原始大小写做成员判断，
    # 否则会把 Content-Type 丢掉，图片被浏览器当成 text/html。
    keep = {"content-type", "content-length", "content-range", "accept-ranges", "etag"}
    out = {}
    for k, v in h.items():
        if k.lower() in keep:
            out[k] = v
    return out


def _stream_proxy(req, upstream: str, headers: dict, host: MediaHost) -> Response:
    """流式转发：requests(stream=True) + werkzeug 生成器。"""
    r = requests.request(
        req.method, upstream,
        headers=headers,
        stream=True,
        timeout=(10, 120),
        allow_redirects=False,
    )

    def generate():
        try:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            r.close()

    resp_headers = _filter_resp_headers(r.headers)
    # werkzeug Response 直接传入生成器
    return Response(generate(), status=r.status_code, headers=resp_headers)


def json_error(code: str, msg: str) -> str:
    return f'{{"error": "{code}", "message": "{msg}"}}'


# 模块级单例兜底（供 app.py 直接调用 init_blueprint 时使用）
_default_host = None
