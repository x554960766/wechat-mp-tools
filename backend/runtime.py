"""
Runtime helpers for source and PyInstaller builds.
"""

import os
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


def configure_runtime():
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
            return chromium.launch(**kwargs)
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
