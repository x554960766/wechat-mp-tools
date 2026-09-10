"""等待与稳定性检测工具类。"""
from __future__ import annotations

import time
from typing import Callable, TypeVar, Any
import numpy as np

T = TypeVar("T")


class WaitTimeout(Exception):
    """等待超时异常。"""
    pass


class ElementNotFound(Exception):
    """目标元素未找到异常。"""
    pass


def wait_until(
    predicate: Callable[[], T | None],
    timeout: float = 10.0,
    interval: float = 0.4,
    desc: str = "目标条件"
) -> T:
    """轮询等待 predicate() 返回非 None/非 False 结果，超时抛出 WaitTimeout。"""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            res = predicate()
            if res:
                return res
        except Exception as e:
            last_err = e
        time.sleep(interval)
    msg = f"等待 [{desc}] 超时 ({timeout}s)"
    if last_err:
        msg += f"，最后一次错误: {last_err}"
    raise WaitTimeout(msg)


def wait_stable_screen(
    capture_fn: Callable[[], np.ndarray],
    stable_rounds: int = 2,
    diff_threshold: float = 0.005,
    timeout: float = 8.0,
    interval: float = 0.4
) -> np.ndarray:
    """
    等待屏幕画面稳定（连续 stable_rounds 次截图差异小于 diff_threshold）。
    常用于等待页面渲染完成，避免 OCR 截到过渡态白屏或残缺内容。
    """
    deadline = time.time() + timeout
    last_img: np.ndarray | None = None
    consecutive_stable = 0

    while time.time() < deadline:
        img = capture_fn()
        if last_img is not None:
            if img.shape == last_img.shape:
                diff = np.mean(np.abs(img.astype(np.float32) - last_img.astype(np.float32))) / 255.0
                if diff < diff_threshold:
                    consecutive_stable += 1
                    if consecutive_stable >= stable_rounds:
                        return img
                else:
                    consecutive_stable = 0
            else:
                consecutive_stable = 0
        last_img = img
        time.sleep(interval)

    # 超时则返回最后截到的图像
    return last_img if last_img is not None else capture_fn()
