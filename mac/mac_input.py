"""macOS 输入模拟与操作封装。"""
from __future__ import annotations

import random
import time

import Quartz
import pyautogui
import pyperclip

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


def human_sleep(a: float = 0.4, b: float = 0.8) -> None:
    """产生拟人随机微延迟。"""
    time.sleep(random.uniform(a, b))


def quartz_click(x: int, y: int, button: str = "left") -> None:
    """使用 macOS Quartz 原生 HID 事件模拟点击屏幕坐标点（单位：点）。"""
    pt = Quartz.CGPoint(x, y)
    btn_down = Quartz.kCGEventLeftMouseDown if button == "left" else Quartz.kCGEventRightMouseDown
    btn_up = Quartz.kCGEventLeftMouseUp if button == "left" else Quartz.kCGEventRightMouseUp
    mouse_btn = Quartz.kCGMouseButtonLeft if button == "left" else Quartz.kCGMouseButtonRight
    down = Quartz.CGEventCreateMouseEvent(None, btn_down, pt, mouse_btn)
    up = Quartz.CGEventCreateMouseEvent(None, btn_up, pt, mouse_btn)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(0.08)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def move_and_click(
    x: int,
    y: int,
    jitter: int = 2,
    double: bool = False,
    button: str = "left"
) -> None:
    """拟人化平滑移动并点击指定屏幕坐标点（单位：点）。"""
    tx = x + random.randint(-jitter, jitter)
    ty = y + random.randint(-jitter, jitter)
    pyautogui.moveTo(tx, ty, duration=random.uniform(0.12, 0.25), tween=pyautogui.easeOutQuad)
    time.sleep(random.uniform(0.05, 0.12))
    if double:
        quartz_click(tx, ty, button=button)
        time.sleep(0.08)
        quartz_click(tx, ty, button=button)
    else:
        quartz_click(tx, ty, button=button)


def type_text_via_clipboard(text: str, clear_first: bool = True) -> None:
    """中文输入唯一可靠方式：pbcopy + Cmd+A + Cmd+V。"""
    old = ""
    try:
        old = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)
    time.sleep(0.15)
    if clear_first:
        pyautogui.hotkey("command", "a")
        time.sleep(0.06)
    pyautogui.hotkey("command", "v")
    time.sleep(0.25)
    try:
        pyperclip.copy(old)
    except Exception:
        pass


def press(*keys: str, times: int = 1) -> None:
    """快捷键与单键按压模拟。"""
    for _ in range(times):
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        time.sleep(0.08)


def scroll(clicks: int, x: int | None = None, y: int | None = None) -> None:
    """
    macOS 滚轮模拟：负数 = 内容向下滚（露出下方内容）。
    """
    if x is not None and y is not None:
        pyautogui.moveTo(x, y, duration=0.1)
        time.sleep(0.1)
    pyautogui.scroll(clicks)
    time.sleep(0.4)


def scroll_smooth(total_clicks: int, x: int, y: int, step: int = 3) -> None:
    """平滑分步滚动，减轻跳跃感与渲染滞后。"""
    sign = 1 if total_clicks > 0 else -1
    remaining = abs(total_clicks)
    pyautogui.moveTo(x, y, duration=0.1)
    while remaining > 0:
        s = min(step, remaining)
        pyautogui.scroll(sign * s)
        remaining -= s
        time.sleep(random.uniform(0.15, 0.25))
    time.sleep(0.4)
