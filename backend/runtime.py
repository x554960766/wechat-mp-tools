"""
Runtime helpers for source and PyInstaller builds.
"""

import os
import shutil
import sys
import traceback
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """Return the writable directory for user data (config, logs, downloads).

    On macOS (frozen), we use ~/Library/Application Support/WeChat MP Tools
    to avoid App Translocation read-only filesystem errors that occur when
    the .app is launched from a DMG or unsigned download location.

    On Windows (frozen), data is stored next to the executable.
    In dev mode, data is stored in the project root.
    """
    if is_frozen():
        if sys.platform == "darwin":
            # macOS: always use the standard Application Support directory.
            # This is writable regardless of App Translocation or Gatekeeper.
            support = Path.home() / "Library" / "Application Support" / "WeChat MP Tools"
            support.mkdir(parents=True, exist_ok=True)
            return support
        # Windows / Linux: keep data next to the executable
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def log_file() -> Path:
    return app_dir() / "wechat_mp_tools.log"


def desktop_available() -> bool:
    """当前环境是否有可用的桌面文件管理器。

    容器里没有 xdg-open，调用会抛 FileNotFoundError；这里提前判断，
    让「打开文件夹」能给出可读提示，而不是 errno 报错。
    可用环境变量 WECHAT_MP_DESKTOP=0/1 强制覆盖。
    """
    override = os.environ.get("WECHAT_MP_DESKTOP")
    if override is not None and override.strip() != "":
        return override.strip().lower() not in ("0", "false", "no", "off")

    if sys.platform in ("darwin", "win32"):
        return True

    # Linux / 容器
    if os.path.exists("/.dockerenv"):
        return False
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return shutil.which("xdg-open") is not None


def host_path_hint(path) -> str:
    """把容器内路径映射成宿主机上能直接找到的路径，用于提示文案。

    容器里 /app/data 对应宿主机 ./data，通过 HOST_DATA_DIR 告诉服务端。
    桌面模式下没有该环境变量，原样返回。
    """
    try:
        container_data = str((app_dir() / "data").resolve())
    except OSError:
        return str(path)

    target = str(path)
    host_data = os.environ.get("HOST_DATA_DIR", "").strip()
    if host_data and target.startswith(container_data):
        return host_data.rstrip("/") + target[len(container_data):]
    return target


def configure_runtime():
    # 限制 Linux glibc ptmalloc 的线程内存池数量，防止多线程并发时产生大量碎片与虚增内存占用
    os.environ.setdefault("MALLOC_ARENA_MAX", "2")

    # 修复在守护进程或终端关闭后 stdout/stderr 写入导致的 [Errno 5] Input/output error
    class _SafeStream:
        def __init__(self, stream):
            self._stream = stream
        def write(self, data):
            try:
                return self._stream.write(data)
            except OSError:
                pass
        def flush(self):
            try:
                return self._stream.flush()
            except OSError:
                pass
        def __getattr__(self, name):
            return getattr(self._stream, name)

    if sys.stdout and not isinstance(sys.stdout, _SafeStream):
        sys.stdout = _SafeStream(sys.stdout)
    if sys.stderr and not isinstance(sys.stderr, _SafeStream):
        sys.stderr = _SafeStream(sys.stderr)

    # 修复在 Windows / 临时解压目录（如 360zip / PyInstaller）运行时，
    # 环境变量 REQUESTS_CA_BUNDLE / SSL_CERT_FILE / CURL_CA_BUNDLE 指向已失效临时路径导致的 TLS 证书报错
    for env_var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        val = os.environ.get(env_var)
        if val and not os.path.exists(val):
            os.environ.pop(env_var, None)

    try:
        import certifi
        ca_path = certifi.where()
        if ca_path and not os.path.exists(ca_path):
            res_dir = resource_dir()
            for candidate in (
                res_dir / "_internal" / "certifi" / "cacert.pem",
                res_dir / "certifi" / "cacert.pem",
            ):
                if candidate.exists():
                    os.environ["REQUESTS_CA_BUNDLE"] = str(candidate)
                    os.environ["SSL_CERT_FILE"] = str(candidate)
                    break
    except Exception:
        pass

    bundled_browsers = resource_dir() / "ms-playwright"
    if bundled_browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)

    # Ensure common executable search paths (like Homebrew / usr/local/bin) are in PATH.
    # On macOS, GUI apps / environment might miss /opt/homebrew/bin and /usr/local/bin,
    # causing subprocess calls to ffmpeg/ffprobe to fail even if they are installed.
    # Also, dynamically prepend bundled ffmpeg directories as a fallback.
    path_env = os.environ.get("PATH", "")
    paths = path_env.split(os.pathsep) if path_env else []
    
    extra_paths = []
    if sys.platform != "win32":
        # System common paths for homebrew/mac/linux
        for p in ["/opt/homebrew/bin", "/usr/local/bin"]:
            if p not in paths and os.path.exists(p):
                extra_paths.append(p)
                
    # Detect user-downloaded ffmpeg folder (created by one-click installer)
    downloaded_ffmpeg_dir = app_dir() / "ffmpeg"
    if downloaded_ffmpeg_dir.exists():
        downloaded_path_str = str(downloaded_ffmpeg_dir.resolve())
        if downloaded_path_str not in paths:
            extra_paths.append(downloaded_path_str)

    # Detect bundled ffmpeg folder in subtitle_remover (if it exists)
    ffmpeg_base = Path(__file__).resolve().parent / "subtitle_remover" / "backend" / "ffmpeg"
    bundled_ffmpeg_dir = None
    if sys.platform == "win32":
        bundled_ffmpeg_dir = ffmpeg_base / "win_x64"
    elif sys.platform == "darwin":
        bundled_ffmpeg_dir = ffmpeg_base / "macos"
    else:
        bundled_ffmpeg_dir = ffmpeg_base / "linux_x64"

    if bundled_ffmpeg_dir.exists():
        bundled_path_str = str(bundled_ffmpeg_dir.resolve())
        if bundled_path_str not in paths:
            extra_paths.append(bundled_path_str)

    if extra_paths:
        os.environ["PATH"] = os.pathsep.join(extra_paths + paths)


def bundled_browsers_available() -> bool:
    return (resource_dir() / "ms-playwright").exists()


def _system_browser_channels() -> list[str]:
    if sys.platform == "win32":
        return ["msedge", "chrome"]
    return ["chrome", "msedge"]


def activate_macos_browser():
    """在 macOS 下将新启动的浏览器窗口置顶激活，避免后台启动让用户以为卡住"""
    if sys.platform != "darwin":
        return
    import threading
    def _run():
        import subprocess
        import time
        time.sleep(0.5)
        try:
            script = '''
            tell application "System Events"
                set pList to every process whose name contains "Chrome" or name contains "Chromium" or name contains "Edge"
                if (count of pList) > 0 then
                    set frontmost of item 1 of pList to true
                end if
            end tell
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def launch_chromium(chromium, **launch_kwargs):
    """Launch bundled Chromium when present, otherwise fall back to system browsers."""
    attempts = []
    if "channel" in launch_kwargs:
        attempts.append({})
    elif bundled_browsers_available():
        if launch_kwargs.get("headless"):
            attempts.append({"channel": "chromium"})
        else:
            attempts.append({})
        attempts.extend({"channel": channel} for channel in _system_browser_channels())
        attempts.append({})
    else:
        attempts.extend({"channel": channel} for channel in _system_browser_channels())
        attempts.append({})

    seen = set()
    last_error = None
    for override in attempts:
        key = tuple(sorted(override.items()))
        if key in seen:
            continue
        seen.add(key)
        kwargs = launch_kwargs.copy()
        kwargs.update(override)
        try:
            browser = chromium.launch(**kwargs)
            if not kwargs.get("headless", False):
                activate_macos_browser()
            return browser
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "未检测到可用浏览器。请安装 Google Chrome / Microsoft Edge，"
        "或使用内置 Chromium 的完整版安装包。"
    ) from last_error


def launch_persistent_context(chromium, user_data_dir, **launch_kwargs):
    """Launch persistent browser context when present, otherwise fall back to system browsers."""
    attempts = []
    if "channel" in launch_kwargs:
        attempts.append({})
    elif bundled_browsers_available():
        if launch_kwargs.get("headless"):
            attempts.append({"channel": "chromium"})
        else:
            attempts.append({})
        attempts.extend({"channel": channel} for channel in _system_browser_channels())
        attempts.append({})
    else:
        attempts.extend({"channel": channel} for channel in _system_browser_channels())
        attempts.append({})

    seen = set()
    last_error = None
    for override in attempts:
        key = tuple(sorted(override.items()))
        if key in seen:
            continue
        seen.add(key)
        kwargs = launch_kwargs.copy()
        kwargs.update(override)
        try:
            ctx = chromium.launch_persistent_context(str(user_data_dir), **kwargs)
            if not kwargs.get("headless", False):
                activate_macos_browser()
            return ctx
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "未检测到可用浏览器。请安装 Google Chrome / Microsoft Edge，"
        "或使用内置 Chromium 的完整版安装包。"
    ) from last_error


def write_startup_error(exc: BaseException):
    try:
        target = log_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
    except Exception:
        pass


def release_memory():
    """主动触发垃圾回收并向操作系统归还未使用的堆内存（针对 Linux 容器环境的 glibc 碎片化）"""
    import gc
    try:
        gc.collect()
        if sys.platform.startswith("linux"):
            import ctypes
            try:
                libc = ctypes.CDLL("libc.so.6")
                if hasattr(libc, "malloc_trim"):
                    libc.malloc_trim(0)
            except Exception:
                pass
    except Exception:
        pass
