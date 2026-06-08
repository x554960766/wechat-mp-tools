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
