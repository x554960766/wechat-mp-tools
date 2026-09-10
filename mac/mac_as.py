"""AppleScript 驱动模块：微信激活、全局搜索、按键与窗口管理。"""
from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger("wechat_auto_mac.as")


def run_as(script: str, timeout: float = 10.0) -> str:
    """执行一段 AppleScript，返回 stdout。失败抛 RuntimeError。"""
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"AppleScript 失败: {r.stderr.strip()}")
    return r.stdout.strip()


def activate_wechat() -> None:
    """激活微信窗口置前。"""
    try:
        run_as('tell application "WeChat" to activate')
    except Exception:
        pass


def launch_wechat() -> None:
    """启动微信。"""
    try:
        run_as('tell application "WeChat" to launch')
    except Exception:
        pass


def search_and_open_first(keyword: str = "新京报") -> None:
    """
    全流程：激活微信 → 唤起搜索 → 粘贴关键词 → 回车打开搜一搜首个结果。
    具备 AppleScript 与 pyautogui (CGEvent) 双通道兜底机制。
    """
    kw = keyword.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "WeChat" to activate
delay 0.4
tell application "System Events"
    tell process "WeChat"
        keystroke "f" using command down
        delay 0.6
        set the clipboard to "{kw}"
        delay 0.2
        keystroke "v" using command down
        delay 0.6
        key code 36
    end tell
end tell
'''
    try:
        run_as(script)
    except Exception as e:
        logger.warning("AppleScript 发送按键受限 (%s)，切换至精准坐标点击与原生剪贴板模拟输入...", e)
        import pyautogui
        from mac.mac_win import find_main_window
        from mac.mac_input import move_and_click, type_text_via_clipboard
        activate_wechat()
        time.sleep(0.3)
        main_win = find_main_window(timeout=1.0)
        if main_win:
            # 点击微信主窗口左上方搜索栏 (x + 135, y + 36) 确保输入焦点
            move_and_click(main_win.x + 135, main_win.y + 36)
            time.sleep(0.3)
        else:
            pyautogui.hotkey("command", "f")
            time.sleep(0.3)
        type_text_via_clipboard(keyword, clear_first=True)
        time.sleep(0.6)
        pyautogui.press("return")
        time.sleep(0.5)


def press_escape(times: int = 2) -> None:
    """连续按下 Esc 退出搜索状态或对话框。"""
    for _ in range(times):
        try:
            run_as('tell application "System Events" to key code 53')
        except Exception:
            import pyautogui
            pyautogui.press("escape")
        time.sleep(0.3)


def close_front_window() -> None:
    """通过 Cmd+W 关闭当前前置窗口。"""
    try:
        run_as('tell application "System Events" to keystroke "w" using command down')
    except Exception:
        import pyautogui
        pyautogui.hotkey("command", "w")
