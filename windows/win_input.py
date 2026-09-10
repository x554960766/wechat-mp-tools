"""Windows 合成输入层：PostMessage 合成鼠标点击 / 按键，不占用真实鼠标、不抢用户焦点。

实现参考 Access_wechat_article 的 article_clicker.py：
- 进入 Per-Monitor-V2 DPI 上下文，保证 WindowFromPoint / ScreenToClient 在物理像素域工作；
- 点击消息只发送给目标窗口或其子窗口，防止误点到其他应用；
- Chromium 内核异步处理消息，按键/抬起后需留出导航时间。
"""
from __future__ import annotations

import ctypes
import logging
import os
import random
import time
from ctypes import wintypes

logger = logging.getLogger("wechat_auto_windows")

if os.name != "nt":
    raise ImportError("windows.win_input 仅支持 Windows 平台 (os.name == 'nt')")

user32 = ctypes.WinDLL("user32", use_last_error=True)

# ── 消息 / 虚拟键常量 ─────────────────────────────────
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
MK_LBUTTON = 0x0001
WM_CLOSE = 0x0010

VK_F5 = 0x74
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
VK_UP = 0x26
VK_DOWN = 0x28
VK_KEY_W = 0x57
VK_KEY_F = 0x46
VK_KEY_V = 0x56
VK_KEY_R = 0x52

KEYEVENTF_KEYUP = 0x0002

user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None

def release_all_modifiers():
    """安全强制释放所有修饰键（Ctrl, Shift, Alt, Win），防止按键粘连锁死用户键盘鼠标。"""
    for vk in (0x11, 0x10, 0x12, 0x5B, 0x5C):  # VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN
        try:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

def send_key_combo(vk_modifier: int, vk_key: int):
    """通过系统级 keybd_event 真实发送组合键 (如 Ctrl+A, Ctrl+V, Ctrl+F)，确保 modifier 必被释放"""
    try:
        user32.keybd_event(vk_modifier, 0, 0, 0)
        time.sleep(0.04)
        try:
            user32.keybd_event(vk_key, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(vk_key, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.03)
        finally:
            user32.keybd_event(vk_modifier, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.04)
    except Exception:
        release_all_modifiers()

def send_single_key(vk_key: int):
    """通过系统级 keybd_event 真实发送单个物理按键 (如 Enter, Down, Escape, Backspace)"""
    try:
        user32.keybd_event(vk_key, 0, 0, 0)
        time.sleep(0.04)
        user32.keybd_event(vk_key, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.04)
    except Exception:
        pass

# Per-Monitor-V2 DPI 感知上下文句柄（Win10 1703+，旧系统调用失败则忽略）
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ScreenToClient.restype = wintypes.BOOL
user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.IsChild.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL


def human_sleep(min_s: float, max_s: float) -> None:
    """拟人化随机休眠，与 mac/mac_input.human_sleep 行为一致，降低频控风险。"""
    time.sleep(random.uniform(min_s, max_s))


class _DpiContext:
    """临时进入 Per-Monitor-V2 DPI 上下文，退出时恢复（参考 Access_wechat_article）。"""

    def __enter__(self):
        self._old = None
        try:
            if hasattr(user32, "SetThreadDpiAwarenessContext"):
                user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
                self._old = user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        except Exception:
            self._old = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._old:
                user32.SetThreadDpiAwarenessContext(self._old)
        except Exception:
            pass
        return False


def _make_lparam(x: int, y: int) -> int:
    """把客户区坐标打包为 lParam（低 16 位 x，高 16 位 y）。"""
    return (y << 16) | (x & 0xFFFF)


def _resolve_click_target(hwnd: int, screen_x: int, screen_y: int) -> int:
    """校验点击落点：只有落点窗口是目标窗口或其子窗口才直接投递，否则回退到目标窗口。
    防止坐标偏移导致消息发到其他应用（参考 Access_wechat_article 的防误点设计）。"""
    try:
        pt = wintypes.POINT(screen_x, screen_y)
        hit = user32.WindowFromPoint(pt)
        if hit and (hit == hwnd or user32.IsChild(hwnd, hit)):
            return int(hit)
    except Exception:
        pass
    return hwnd


def post_click(hwnd: int, screen_x: int, screen_y: int) -> bool:
    """向窗口投递合成鼠标点击（屏幕坐标）。
    不移动真实鼠标指针；Chromium webview 异步处理消息，抬起后固定等待导航启动。"""
    try:
        with _DpiContext():
            target = _resolve_click_target(hwnd, screen_x, screen_y)
            pt = wintypes.POINT(screen_x, screen_y)
            user32.ScreenToClient(target, ctypes.byref(pt))
            lparam = _make_lparam(pt.x, pt.y)
            user32.PostMessageW(target, WM_MOUSEMOVE, 0, lparam)
            time.sleep(0.02)
            user32.PostMessageW(target, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
            time.sleep(0.04)
            user32.PostMessageW(target, WM_LBUTTONUP, 0, lparam)
            time.sleep(0.25)  # Chromium 异步处理导航，等待页面开始加载
        return True
    except Exception as e:
        logger.debug("post_click(hwnd=%s, %s,%s) 异常: %s", hwnd, screen_x, screen_y, e)
        return False


def post_key(hwnd: int, vk: int, with_ctrl: bool = False) -> bool:
    """向窗口投递合成按键（如 F5 刷新）。
    注意：PostMessage 合成键对部分 DirectUI 窗口可能无效，调用方需有降级方案；
    Chromium webview（文章窗口）对 WM_KEYDOWN/WM_KEYUP 支持良好。"""
    try:
        if with_ctrl:
            # 先按下 Ctrl 再按字母键，模拟 Ctrl+X 组合
            user32.PostMessageW(hwnd, WM_KEYDOWN, VK_CONTROL, 0x001D0001)
            time.sleep(0.03)
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0x00000001)
        time.sleep(0.05)
        user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000001)
        if with_ctrl:
            time.sleep(0.03)
            user32.PostMessageW(hwnd, WM_KEYUP, VK_CONTROL, 0xC01D0001)
        return True
    except Exception as e:
        logger.debug("post_key(hwnd=%s, vk=0x%02X) 异常: %s", hwnd, vk, e)
        return False


def post_refresh(hwnd: int) -> bool:
    """向 webview 窗口发送 F5 刷新（文章页刷新会携带最新 key/pass_ticket 重新请求）。"""
    return post_key(hwnd, VK_F5)


def post_escape(hwnd: int) -> bool:
    return post_key(hwnd, VK_ESCAPE)


def post_down(hwnd: int) -> bool:
    return post_key(hwnd, VK_DOWN)


def post_return(hwnd: int) -> bool:
    return post_key(hwnd, VK_RETURN)


# ── 剪贴板与搜索输入（中文关键词无法用按键消息直接输入，走剪贴板粘贴） ──

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE


def set_clipboard_text(text: str) -> bool:
    """把文本写入系统剪贴板（CF_UNICODETEXT）。失败返回 False。"""
    if not text:
        return False
    try:
        buf = ctypes.create_unicode_buffer(text)
        nbytes = ctypes.sizeof(buf)
        h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, nbytes)
        if not h_global:
            return False
        try:
            target = kernel32.GlobalLock(h_global)
            if not target:
                return False
            ctypes.memmove(target, buf, nbytes)
            kernel32.GlobalUnlock(h_global)
        except Exception:
            return False
        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            return bool(user32.SetClipboardData(CF_UNICODETEXT, h_global))
        finally:
            user32.CloseClipboard()
    except Exception as e:
        logger.debug("set_clipboard_text 异常: %s", e)
        return False


def type_via_clipboard(hwnd: int, text: str, confirm: bool = True) -> bool:
    """向窗口粘贴输入文本：清空剪贴板→写入文本→Ctrl+V（可选回车确认）。
    用于搜索框输入中文关键词（按键消息无法直接输入中文）。"""
    if not set_clipboard_text(text):
        logger.warning("⚠️ 剪贴板写入失败，无法输入搜索关键词。")
        return False
    post_key(hwnd, VK_KEY_V, with_ctrl=True)
    time.sleep(0.6)  # 等待输入法/搜索联想渲染
    if confirm:
        post_key(hwnd, VK_RETURN)
        time.sleep(0.3)
    return True


def focus_search_and_type(main_hwnd: int, keyword: str, confirm: bool = False) -> bool:
    """微信主窗口聚焦搜索框并输入关键词（安全组合键 -> 剪贴板粘贴 -> 释放所有修饰键）。"""
    from windows.win_window import activate_window
    activate_window(main_hwnd)
    time.sleep(0.25)

    release_all_modifiers()

    # 1. 优先通过 UIA 在微信窗口左上方寻找搜索框控件点击激活
    focused = False
    try:
        import uiautomation as auto
        ctrl = auto.ControlFromHandle(main_hwnd)
        if ctrl:
            for c, _ in auto.WalkControl(ctrl, maxDepth=6):
                name = (c.Name or "").strip()
                if c.ControlType in (auto.ControlType.EditControl, 50004) or any(k in name for k in ("搜索", "Search")):
                    r = c.BoundingRectangle
                    if r.top < 150 and r.right > r.left and r.bottom > r.top:
                        try:
                            c.Click(simulateMove=False)
                        except Exception:
                            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                            post_click(main_hwnd, cx, cy)
                        focused = True
                        break
    except Exception:
        pass

    # 2. 微信标准快捷键 Ctrl+F（直接聚焦搜索框并自动全选内容）
    time.sleep(0.15)
    send_key_combo(VK_CONTROL, VK_KEY_F)
    time.sleep(0.25)

    # 3. 清空现有输入：Backspace
    send_single_key(0x08)  # Backspace
    time.sleep(0.1)

    # 4. 写入剪贴板并通过系统级按键 Ctrl+V 粘贴关键词
    if not set_clipboard_text(keyword):
        release_all_modifiers()
        return False
    send_key_combo(VK_CONTROL, VK_KEY_V)  # Ctrl+V
    time.sleep(0.6)  # 等待微信搜索下拉列表完全渲染出来

    if confirm:
        send_single_key(VK_RETURN)
        time.sleep(0.3)

    release_all_modifiers()
    return True
