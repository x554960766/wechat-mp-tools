"""图片过滤器：把归档下来的图分成「值得留」和「不值得留」两类。

口径是保守档——宁可放过多余的图，也不能误杀正文配图。所以每条规则只针对
形态上"确定无用"的图：坏图、缩略图/图标、纯色占位、长条分割线、同篇重复。

被判定丢掉的图不删，移到同级 `_rejected/` 目录，误判也能拿回来。
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageStat

from . import config

# 判定原因 → 给人看的中文说明
REASONS = {
    "broken": "图片损坏或不是有效图像",
    "tiny": "尺寸过小（图标/缩略图/装饰）",
    "strip": "极端长条（分割线/横条）",
    "flat": "纯色占位图",
    "transparent": "几乎全透明（水印层/蒙版）",
    "dup": "同篇文章内重复",
}
# 体积上限之外，再兜一层：这些原因都算"丢"
REJECT_DIR = "_rejected"


@dataclass
class ImageInfo:
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
    """透明像素（alpha < 16）占比。非 RGBA 直接算 0。"""
    if "A" not in im.getbands() and im.mode != "P":
        return 0.0
    alpha = im.convert("RGBA").getchannel("A")
    histogram = alpha.histogram()
    total = sum(histogram) or 1
    return sum(histogram[:16]) / total


def inspect(path: Path) -> ImageInfo:
    """读一张图，取出判定所需的一切指标。读不动就记 error，不抛。"""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    info = ImageInfo(path=path, name=path.name, bytes=path.stat().st_size, sha256=digest)
    try:
        with Image.open(path) as im:
            im.load()  # 强制解码，截断的图这里才暴露
            info.width, info.height = im.width, im.height
            info.mode, info.fmt = im.mode, im.format or ""
            info.std = float(ImageStat.Stat(im.convert("L")).stddev[0])
            info.transparent_ratio = _transparent_ratio(im)
    except Exception as exc:  # 坏图不该让整批失败
        info.error = f"{type(exc).__name__}: {exc}"
    return info


def judge(info: ImageInfo, seen: list[ImageInfo]) -> str:
    """返回空串表示保留，否则返回原因码。seen 是同篇已保留的图，用于去重。"""
    if info.error:
        return "broken"
    if min(info.width, info.height) < config.FILTER_MIN_SIDE or info.area < config.FILTER_MIN_AREA:
        return "tiny"
    if info.aspect > config.FILTER_MAX_ASPECT:
        return "strip"
    if info.std < config.FILTER_MIN_STD:
        return "flat"
    if info.transparent_ratio > config.FILTER_MAX_TRANSPARENT:
        return "transparent"
    for other in seen:
        if other.sha256 == info.sha256:
            return "dup"
    return ""


@dataclass
class FilterResult:
    kept: list[ImageInfo] = field(default_factory=list)
    rejected: list[tuple[ImageInfo, str]] = field(default_factory=list)

    @property
    def dropped(self) -> int:
        return len(self.rejected)

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for _, reason in self.rejected:
            counts[reason] = counts.get(reason, 0) + 1
        return {"kept": len(self.kept), "dropped": self.dropped, "reasons": counts}


def filter_dir(images_dir: Path, *, move: bool = True) -> FilterResult:
    """过滤一个归档目录。move=True 时把被丢的图移到 `_rejected/`。

    只处理目录内的顶层图片文件，不递归——归档结构是平铺的。
    """
    result = FilterResult()
    if not images_dir.is_dir():
        return result
    files = sorted(p for p in images_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"})
    for path in files:
        info = inspect(path)
        reason = judge(info, result.kept)
        if not reason:
            result.kept.append(info)
            continue
        result.rejected.append((info, reason))
        if move:
            target_dir = images_dir.parent / REJECT_DIR
            try:
                target_dir.mkdir(exist_ok=True)
                shutil.move(str(path), str(target_dir / path.name))
            except Exception:
                pass  # 移动失败就留在原地，判定结果照常汇报
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="对一个归档目录跑图片过滤，只打印不移动")
    parser.add_argument("images_dir", type=Path)
    parser.add_argument("--move", action="store_true", help="真的把被丢的图移到 _rejected/")
    args = parser.parse_args()
    result = filter_dir(args.images_dir, move=args.move)
    for info in result.kept:
        print(f"保留  {info.name}  {info.width}x{info.height}  {info.bytes/1024:.0f}K")
    for info, reason in result.rejected:
        print(f"丢弃  {info.name}  {info.width}x{info.height}  原因={reason}")
    print(result.summary())


if __name__ == "__main__":
    main()
