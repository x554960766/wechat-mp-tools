"""Windows 微信窗口发现层：纯 Win32 API (ctypes) 枚举窗口、识别进程、定位关键窗口。

不依赖任何第三方库。仅在 Windows 平台可用（非 Windows 平台 import 时抛 ImportError，
调用方 scripts/auto_refresh_pc_wechat.py 只在 platform == "Windows" 时才会导入）。
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field

logger = logging.getLogger("wechat_auto_windows")

if os.name != "nt":
    raise ImportError("windows.win_window 仅支持 Windows 平台 (os.name == 'nt')")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ── Win32 常量 ─────────────────────────────────────────
SW_RESTORE = 9
SW_SHOW = 5
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 微信相关进程名（3.x 为 WeChat.exe + WeChatApp.exe/WeChatAppEx.exe；4.x 为 Weixin.exe + WeixinAppEx.exe 等）
MAIN_PROCESS_NAMES = {"wechat.exe", "weixin.exe"}
WEBVIEW_PROCESS_NAMES = {
    "wechatapp.exe", "wechatappex.exe", "wechatappex_he.exe",
    "weixinapp.exe", "weixinappex.exe", "weixinappex_he.exe"
}
ALL_WECHAT_PROCESS_NAMES = MAIN_PROCESS_NAMES | WEBVIEW_PROCESS_NAMES

# 微信主窗口类名/标题特征（3.x 主窗口类名 WeChatMainWndForPC；4.x 为 WeixinMainWndForPC / mmui::MainWindow 等）
MAIN_WINDOW_CLASSES = {
    "WeChatMainWndForPC", "WeChatLoginWndForPC",
    "WeixinMainWndForPC", "WeixinLoginWndForPC",
    "mmui::MainWindow"
}
MAIN_WINDOW_TITLES = {"微信", "WeChat", "Weixin"}

# 文章/公众号 webview 窗口标题关键词（文章窗口标题通常为文章名或公众号名，
# 搜一搜窗口标题为 "搜一搜"，订阅号消息窗口标题为 "订阅号消息"）
WEBVIEW_TITLE_HINTS = ("微信", "WeChat", "公众号", "订阅号", "文章", "搜一搜", "朋友圈")


@dataclass
class WinWindow:
    """一个可见顶层窗口的快照信息。"""
    hwnd: int
    title: str = ""
    cls: str = ""
    pid: int = 0
    process_name: str = ""
    rect: tuple = field(default_factory=lambda: (0, 0, 0, 0))  # (left, top, right, bottom)

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def exe_lower(self) -> str:
        return (self.process_name or "").lower()

    def __repr__(self) -> str:  # 便于日志排查
        return (f"WinWindow(hwnd={self.hwnd}, title={self.title[:20]!r}, "
                f"cls={self.cls[:28]!r}, exe={self.exe_lower}, size={self.width}x{self.height})")


# ── ctypes 原型绑定 ────────────────────────────────────
def _bind_apis():
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL

    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL


_bind_apis()


def _pid_to_process_name(pid: int) -> str:
    """通过 pid 查询进程可执行文件名，失败返回空字符串。"""
    if not pid:
        return ""
    try:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _get_window_text(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _get_class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def enum_visible_windows() -> list:
    """枚举当前所有可见顶层窗口，返回 WinWindow 列表（已过滤空窗口）。"""
    results = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = _get_window_text(hwnd)
            cls = _get_class_name(hwnd)
            if not title and not cls:
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            results.append(WinWindow(
                hwnd=int(hwnd),
                title=title,
                cls=cls,
                pid=pid.value,
                process_name=_pid_to_process_name(pid.value),
                rect=(rect.left, rect.top, rect.right, rect.bottom),
            ))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception as e:
        logger.debug("EnumWindows 执行异常: %s", e)
    return results


def find_wechat_windows() -> list:
    """返回所有属于微信进程（主进程或 webview 进程）的可见顶层窗口。"""
    wins = [w for w in enum_visible_windows() if w.exe_lower in ALL_WECHAT_PROCESS_NAMES]
    return wins


def find_main_window(windows: list = None) -> WinWindow | None:
    """定位微信主窗口（聊天列表窗口）。
    判定优先级：类名特征 > 标题特征 > 主进程中面积最大的窗口。"""
    wins = windows if windows is not None else find_wechat_windows()
    main_proc_wins = [w for w in wins if w.exe_lower in MAIN_PROCESS_NAMES]
    if not main_proc_wins:
        return None

    for w in main_proc_wins:
        if w.cls in MAIN_WINDOW_CLASSES and w.cls != "WeChatLoginWndForPC":
            return w
    for w in main_proc_wins:
        if w.title in MAIN_WINDOW_TITLES or w.title.startswith("微信"):
            return w
    # 兜底：主进程中客户区面积最大的窗口（登录窗通常很小）
    return max(main_proc_wins, key=lambda w: w.area, default=None)


def find_webview_windows(windows: list = None, title_hints: tuple = None) -> list:
    """定位微信 webview 窗口（文章页/公众号主页/搜一搜/订阅号消息，均为独立窗口）。
    这些窗口由 WeChatApp.exe / WeChatAppEx.exe 承载，F5 刷新或点击后会触发带凭证的请求。"""
    wins = windows if windows is not None else find_wechat_windows()
    webview_wins = [w for w in wins if w.exe_lower in WEBVIEW_PROCESS_NAMES]
    if title_hints:
        webview_wins = [w for w in webview_wins if any(h in w.title for h in title_hints)]
    # 面积太小的窗口（下拉联想小窗等）排除
    webview_wins = [w for w in webview_wins if w.width >= 300 and w.height >= 300]
    # 按面积降序，优先操作最大的（通常是完整页面窗口而非弹层）
    webview_wins.sort(key=lambda w: w.area, reverse=True)
    return webview_wins


def find_article_windows(windows: list = None) -> list:
    """定位疑似文章详情窗口：webview 窗口中排除搜一搜/订阅号消息/朋友圈等聚合页。"""
    exclude = ("搜一搜", "订阅号消息", "朋友圈", "视频号", "小程序")
    wins = find_webview_windows(windows)
    return [w for w in wins if not any(k in w.title for k in exclude)]


def activate_window(hwnd: int, timeout: float = 2.0) -> bool:
    """把窗口真正带到系统最前台（绕过 Windows 前台锁定限制，还原最小化）。"""
    try:
        if not user32.IsWindow(hwnd):
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.15)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)

        # 穿透 Windows 前台锁定限制：通过附加线程输入状态夺取前台激活权
        cur_tid = kernel32.GetCurrentThreadId()
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        if cur_tid != target_tid:
            user32.AttachThreadInput(cur_tid, target_tid, True)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
            user32.AttachThreadInput(cur_tid, target_tid, False)
        else:
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        time.sleep(0.2)
        return True
    except Exception as e:
        logger.debug("activate_window(%s) 异常: %s", hwnd, e)
        return False


def close_window(hwnd: int) -> bool:
    """向窗口投递 WM_CLOSE（等价 Ctrl+W，但不依赖焦点与键盘注入）。"""
    try:
        if not user32.IsWindow(hwnd):
            return False
        return bool(user32.PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE
    except Exception as e:
        logger.debug("close_window(%s) 异常: %s", hwnd, e)
        return False


def is_wechat_running_windows() -> bool:
    """检测微信主进程是否有可见窗口（登录后才存在主窗口）。"""
    return find_main_window() is not None


def launch_wechat_windows(wait_seconds: float = 12.0) -> bool:
    """尝试启动微信客户端并在超时内等待主窗口出现。"""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Tencent\WeChat\WeChat.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Tencent\WeChat\WeChat.exe"),
        os.path.expandvars(r"%LocalAppData%\Programs\Tencent\WeChat\WeChat.exe"),
        # 微信 4.x
        os.path.expandvars(r"%ProgramFiles%\Tencent\Weixin\Weixin.exe"),
        os.path.expandvars(r"%LocalAppData%\Programs\Tencent\Weixin\Weixin.exe"),
    ]
    exe = next((p for p in candidates if os.path.exists(p)), None)
    if not exe:
        logger.warning("⚠️ 未找到微信安装路径，请手动启动微信后重试。")
        return False
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0
        subprocess.Popen([exe], close_fds=True, creationflags=flags)
    except Exception as e:
        logger.warning("⚠️ 启动微信失败: %s", e)
        return False
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_wechat_running_windows():
            return True
        time.sleep(1.0)
    return False


def is_portal_window_windows(win: WinWindow) -> bool:
    """判定一个窗口是否为批量授权聚合页窗口（绝对受保护，不可误关）。"""
    title = win.title or ""
    return any(k in title for k in ("公众号批量授权", "批量授权", "聚合", "5200", "batch-portal", "mp-batch"))


def find_portal_window_windows(windows: list = None) -> WinWindow | None:
    """查找屏幕上已打开的公众号批量授权聚合页窗口（兼容 Windows 微信 4.x 内嵌三栏与 3.x 独立弹窗）。"""
    wins = windows if windows is not None else find_wechat_windows()
    # 1. 独立窗口模式 (3.x 或独立网页窗口)
    for w in wins:
        if is_portal_window_windows(w):
            return w
    for w in wins:
        if w.exe_lower in WEBVIEW_PROCESS_NAMES and is_portal_window_windows(w):
            return w

    # 2. 检测微信 4.x 主窗口内嵌的第三栏分栏模式 (主窗口宽度 >= 1000)
    main = find_main_window(wins)
    if main and main.width >= 1000:
        try:
            import uiautomation as auto
            ctrl = auto.ControlFromHandle(main.hwnd)
            if ctrl:
                for c, _ in auto.WalkControl(ctrl, maxDepth=6):
                    name = c.Name or ""
                    if any(k in name for k in ("公众号批量授权", "批量授权", "5200", "mp-batch-portal")):
                        return WinWindow(
                            hwnd=main.hwnd,
                            title="公众号批量授权聚合中心(内嵌)",
                            cls=main.cls,
                            pid=main.pid,
                            process_name=main.process_name,
                            rect=main.rect
                        )
        except Exception:
            pass
    return None


def close_native_account_windows_windows(exclude_hwnds: set[int] | None = None) -> int:
    """精准关闭 Windows 微信原生公众号主页/名片弹窗，绝不误伤聚合页与主窗口。"""
    exclude = set(exclude_hwnds or set())
    main_win = find_main_window()
    if main_win:
        exclude.add(main_win.hwnd)
    portal_win = find_portal_window_windows()
    if portal_win:
        exclude.add(portal_win.hwnd)

    closed_count = 0
    all_wins = find_wechat_windows()
    for w in all_wins:
        if w.hwnd in exclude:
            continue
        if is_portal_window_windows(w):
            continue
        # 原生公众号名片特征：标题为 '公众号' 或属于独立 webview 且非主窗口与聚合页
        if w.title == "公众号" or (w.exe_lower in WEBVIEW_PROCESS_NAMES and 450 <= w.width <= 750 and 600 <= w.height <= 950):
            if close_window(w.hwnd):
                closed_count += 1
                logger.info("✅ 已安全关闭 Windows 原生公众号名片窗口: %s", w)
    return closed_count
