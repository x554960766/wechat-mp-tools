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
