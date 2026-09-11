"""等待与稳定性检测工具类。"""
from __future__ import annotations

import time
from typing import Callable, TypeVar, Any
import Quartz

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


def _extract_image_fingerprint(img: Any) -> bytes | None:
    """提取图像的采样二进制指纹用于对比画面是否稳定，完全摆脱 numpy。"""
    if img is None:
        return None
    cg_img = getattr(img, "cg_image", img)
    if cg_img is None:
        return None
    prov = Quartz.CGImageGetDataProvider(cg_img)
    if not prov:
        return None
    data = Quartz.CGDataProviderCopyData(prov)
    if not data:
        return None
    raw_bytes = bytes(data)
    # 取首尾与中间采样共 4KB 特征值，兼顾毫秒级极速比对与高可靠性
    length = len(raw_bytes)
    if length <= 4096:
        return raw_bytes
    mid = length // 2
    return raw_bytes[:1024] + raw_bytes[mid - 1024:mid + 1024] + raw_bytes[-1024:]


def wait_stable_screen(
    capture_fn: Callable[[], Any],
    stable_rounds: int = 2,
    diff_threshold: float = 0.005,
    timeout: float = 8.0,
    interval: float = 0.4
) -> Any:
    """
    等待屏幕画面稳定（连续 stable_rounds 次截图指纹一致）。
    常用于等待页面渲染完成，避免 OCR 截到过渡态白屏或残缺内容。
    """
    deadline = time.time() + timeout
    last_fp: bytes | None = None
    last_img: Any = None
    consecutive_stable = 0

    while time.time() < deadline:
        img = capture_fn()
        cur_fp = _extract_image_fingerprint(img)
        if last_fp is not None and cur_fp is not None:
            if cur_fp == last_fp:
                consecutive_stable += 1
                if consecutive_stable >= stable_rounds:
                    return img
            else:
                consecutive_stable = 0
        last_fp = cur_fp
        last_img = img
        time.sleep(interval)

    # 超时则返回最后截到的图像
    return last_img if last_img is not None else capture_fn()
