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
    if is_frozen():
        executable = Path(sys.executable).resolve()
        if sys.platform == "darwin" and len(executable.parents) >= 4:
            macos_dir = executable.parent
            contents_dir = macos_dir.parent
            bundle_dir = contents_dir.parent
            if macos_dir.name == "MacOS" and contents_dir.name == "Contents" and bundle_dir.suffix == ".app":
                return bundle_dir.parent
        return executable.parent
    return Path(__file__).resolve().parent.parent


def log_file() -> Path:
    return app_dir() / "wechat_mp_tools.log"


def configure_runtime():
    bundled_browsers = resource_dir() / "ms-playwright"
    if bundled_browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)


def write_startup_error(exc: BaseException):
    try:
        log_file().write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
    except Exception:
        pass
