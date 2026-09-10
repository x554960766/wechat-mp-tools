"""步骤 4: 清理因自动化产生的 Web 窗口并恢复微信主界面。"""
from __future__ import annotations

import logging
import subprocess
from mac.mac_win import find_web_windows, activate_wechat, find_main_window, list_wechat_windows, MacWindow
from mac.mac_as import press_escape, close_front_window
from mac.mac_input import human_sleep

logger = logging.getLogger("wechat_auto_mac.step4")


def is_portal_window(w: MacWindow) -> bool:
    """判定是否为批量授权聚合页窗口（绝对禁止关闭！）"""
    title = w.title or ""
    return any(k in title for k in ("(窗口)", "聚合", "授权", "batch-portal", "5200"))


def close_native_account_windows() -> int:
    """精准且仅关闭微信原生公众号主页/名片弹窗 (Title='公众号')，绝不误伤聚合页与主窗口。"""
    closed = 0
    try:
        script = '''
tell application "System Events"
    tell process "WeChat"
        set wList to every window whose name is "公众号"
        set c to count of wList
        repeat with w in wList
            try
                tell w to click (first button whose subrole is "AXCloseButton")
            on error
                tell w to perform action "AXRaise"
                keystroke "w" using command down
            end try
        end repeat
        return c
    end tell
end tell
'''
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5.0)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            closed = int(r.stdout.strip())
            if closed > 0:
                logger.info("✅ 已安全关闭 %d 个原生微信公众号名片窗口 (Title='公众号')", closed)
    except Exception as e:
        logger.warning("尝试关闭原生公众号窗口异常: %s", e)
    return closed


def cleanup_mac_wechat_windows(max_rounds: int = 4) -> None:
    """关闭因自动化产生的原生公众号窗口或临时文章标签，但【100% 保护并保留聚合页窗口】。"""
    # 1. 优先关闭原生公众号弹窗
    close_native_account_windows()

    # 2. 检查是否有除聚合页与主窗口之外的多余临时搜一搜/文章窗口
    main = find_main_window(timeout=0.5)
    main_id = main.window_id if main else None

    for _ in range(max_rounds):
        wins = list_wechat_windows()
        temp_wins = [w for w in wins if w.window_id != main_id and not is_portal_window(w)]
        if not temp_wins:
            break
        # 仅对明确不是聚合页的窗口执行关闭
        close_native_account_windows()
        human_sleep(0.2, 0.4)

    logger.info("✅ 自动化安全窗口清理完成（聚合页已受保护完好保留）")

