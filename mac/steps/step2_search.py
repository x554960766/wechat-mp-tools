"""步骤 2: 搜索关键词并打开搜一搜 Web 结果窗口。"""
from __future__ import annotations

import logging
from mac.mac_win import MacWindow, find_web_windows, wait_new_web_window
from mac.mac_as import search_and_open_first
from mac.mac_input import human_sleep
from mac.waiter import ElementNotFound

logger = logging.getLogger("wechat_auto_mac.step2")


def search_and_open_web_window(keyword: str = "新京报", timeout: float = 8.0) -> MacWindow:
    """
    通过 AppleScript/原生输入唤起搜索并打开搜一搜窗口，返回 Web 窗口对象。
    """
    before_web_ids = {w.window_id for w in find_web_windows()}

    logger.info("执行搜索关键词 [%s] 并打开搜一搜...", keyword)
    search_and_open_first(keyword)

    # 等待新窗口（搜一搜 Web 窗口或下拉提示框）弹出
    candidate = wait_new_web_window(before_web_ids, timeout=4.0)

    # 如果捕获到的是搜索下拉菜单（尺寸较小），则 OCR 点击"搜索网络结果 / Q 关键词"
    if candidate and (candidate.w < 550 or candidate.h < 450):
        logger.info("检测到搜索下拉联想菜单 %s，正在 OCR 定位网络搜索项...", candidate)
        from mac.mac_win import capture_window
        from mac.mac_ocr import ocr
        from mac.scale import CoordSpace
        from mac.mac_input import move_and_click

        img = capture_window(candidate)
        boxes = ocr(img)
        target = next((b for b in boxes if keyword in b.text and b.top < 120), None)
        if not target:
            target = next((b for b in boxes if "搜索" in b.text and b.top < 120), None)

        if target:
            cs = CoordSpace(candidate.window_id, {"x": candidate.x, "y": candidate.y, "w": candidate.w, "h": candidate.h})
            sx, sy = cs.img_to_screen(target.center[0], target.center[1])
            logger.info("点击下拉联想项 [%s] 屏幕坐标: (%d, %d)", target.text, sx, sy)
            move_and_click(sx, sy)

        # 再次等待真正的搜一搜大窗口弹出
        real_web = wait_new_web_window(before_web_ids | {candidate.window_id}, timeout=6.0)
        if real_web:
            candidate = real_web

    # 如果还未定位到大窗口，检查当前所有微信窗口中是否有宽>=550且高>=450的 Web 窗口
    if not candidate or candidate.w < 550:
        cands = [w for w in find_web_windows() if w.w >= 550 and w.h >= 450]
        if cands:
            candidate = max(cands, key=lambda w: w.w * w.h)

    if not candidate:
        raise ElementNotFound("搜一搜 Web 结果窗口未在预期时间内出现")

    logger.info("✅ 成功定位到搜一搜 Web 窗口: %s", candidate)
    human_sleep(0.8, 1.2)
    return candidate
