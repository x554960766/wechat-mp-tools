"""Retina 缩放检测与坐标换算。全工程统一在这里过一遍，杜绝 2x 偏差。"""
from __future__ import annotations

import Quartz


def detect_scale(window_id: int, bounds: dict) -> float:
    """
    用「截图实际像素宽 / bounds 逻辑宽」实测缩放比。
    比读 NSScreen.backingScaleFactor 更可靠（多显示器混接时各屏比例不同）。
    """
    img_ref = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming
        | Quartz.kCGWindowImageNominalResolution,
    )
    if img_ref is None:
        return 2.0  # 拿不到截图时按 Retina 默认
    px_w = Quartz.CGImageGetWidth(img_ref)
    pt_w = bounds.get("w") if "w" in bounds else bounds.get("Width", 0)
    if pt_w <= 0:
        return 2.0
    scale = px_w / pt_w
    # 归一到常见值，避免浮点误差
    return round(scale * 2) / 2   # 1.0 / 1.5 / 2.0 ...


class CoordSpace:
    """持有一个窗口的换算上下文：截图像素坐标 ↔ 屏幕点坐标。"""

    def __init__(self, window_id: int, bounds: dict):
        self.window_id = window_id
        self.x = bounds.get("x") if "x" in bounds else bounds.get("X", 0)
        self.y = bounds.get("y") if "y" in bounds else bounds.get("Y", 0)
        self.scale = detect_scale(window_id, bounds)

    def img_to_screen(self, px: int, py: int) -> tuple[int, int]:
        """截图内像素坐标 → pyautogui 可用的屏幕点坐标。"""
        return (int(self.x + px / self.scale),
                int(self.y + py / self.scale))
