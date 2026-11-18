"""落盘图片质量筛选器。

口径为保守档——宁可放过多余图，也不误杀正文配图，只针对形态上确定无用的图：
坏图（broken）、过小图标/缩略图（tiny）、极端长条分割线（strip）、
纯色占位图（flat）、近全透明层（transparent）、同篇内容重复图（dup）。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat

REASONS = {
    "broken": "图片损坏或不是有效图像",
    "tiny": "尺寸过小（图标/缩略图/装饰）",
    "strip": "极端长条（分割线/横条）",
    "flat": "纯色占位图",
    "transparent": "几乎全透明（水印层/蒙版）",
    "dup": "同篇文章内重复",
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


@dataclass
class ImageInfo:
    """单张已落盘图片的解码与统计指标。"""

    path: Path
    name: str
    bytes: int
    width: int = 0
    height: int = 0
    mode: str = ""
    fmt: str = ""
    std: float = 0.0
    transparent_ratio: float = 0.0
    sha256: str = ""
    error: str = ""

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def aspect(self) -> float:
        short = min(self.width, self.height) or 1
        return max(self.width, self.height) / short


def _transparent_ratio(im: Image.Image) -> float:
    """透明像素（alpha < 16）占比。无透明通道直接算 0。"""
    if "A" not in im.getbands() and im.mode != "P":
        return 0.0
    alpha = im.convert("RGBA").getchannel("A")
    histogram = alpha.histogram()
    total = sum(histogram) or 1
    return sum(histogram[:16]) / total


def inspect(path: Path) -> ImageInfo:
    """读取一张已落盘图片并提取判定指标；损坏图片仅记录 error，不抛出异常。"""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    info = ImageInfo(path=path, name=path.name, bytes=path.stat().st_size, sha256=digest)
    try:
        with Image.open(path) as im:
            im.load()  # 强制完整解码，截断或损坏图片在此暴露
            info.width, info.height = im.width, im.height
            info.mode, info.fmt = im.mode, im.format or ""
            info.std = float(ImageStat.Stat(im.convert("L")).stddev[0])
            info.transparent_ratio = _transparent_ratio(im)
    except Exception as exc:  # noqa: BLE001 - 坏图不该让整篇下载失败
        info.error = f"{type(exc).__name__}: {exc}"
    return info


def judge(info: ImageInfo, seen: list[ImageInfo]) -> str:
    """返回空串表示保留，否则返回原因码；``seen`` 为同篇已保留图用于内容去重。"""
    if info.error:
        return "broken"
    min_side = _env_int("WX_FILTER_MIN_SIDE", 200)
    min_area = _env_int("WX_FILTER_MIN_AREA", 80000)
    max_aspect = _env_float("WX_FILTER_MAX_ASPECT", 12.0)
    min_std = _env_float("WX_FILTER_MIN_STD", 4.0)
    max_transparent = _env_float("WX_FILTER_MAX_TRANSPARENT", 0.9)

    if min(info.width, info.height) < min_side or info.area < min_area:
        return "tiny"
    if info.aspect > max_aspect:
        return "strip"
    if info.std < min_std:
        return "flat"
    if info.transparent_ratio > max_transparent:
        return "transparent"
    for other in seen:
        if other.sha256 == info.sha256:
            return "dup"
    return ""
