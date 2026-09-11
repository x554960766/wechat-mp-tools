"""相对时间锚点法识别文章卡片。
核心原理：
1. 锚点识别：命中相对时间正则（如"11小时前"、"4天前"、"20分钟前"）作为元信息行；
2. 锚点聚类：同一张卡片只保留同列最靠下的时间行；
3. 同列约束：标题与卡片正文候选必须与锚点水平重叠 >= 30%，排除右侧侧边栏干扰；
4. 卡片边界：向上聚拢确定最大字号标题行，计算目标点击坐标。
"""
from __future__ import annotations

import re
from typing import Any
from dataclasses import dataclass

from mac.mac_ocr import TextBox, ocr

TIME_RE = re.compile(
    r"(\d+\s*分钟前|\d+\s*小时前|\d+\s*天前|昨天|前天|上周|"
    r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*月\s*\d{1,2}\s*日)"
)
NOISE_RE = re.compile(r"^(相关搜索|搜索|全部|账号|文章|视频|划线|百科|问一问)")


@dataclass
class ArticleCard:
    """识别到的文章卡片结构。"""
    title: str
    meta: str
    click_x: int          # 截图像素坐标 x，需由 CoordSpace 换算为屏幕点坐标
    click_y: int          # 截图像素坐标 y
    title_box: tuple[int, int, int, int]  # (left, top, right, bottom)
    is_account: bool = False  # 是否为公众号/视频号主页卡片 (包含 '篇原创内容' 或 '条视频')

    def __repr__(self) -> str:
        return f"ArticleCard(title={self.title!r}, meta={self.meta!r}, is_account={self.is_account}, click=({self.click_x},{self.click_y}))"


def _x_overlap(a: TextBox, b: TextBox) -> float:
    """计算两个文本块在水平 X 轴上的重叠比例。"""
    ov = min(a.right, b.right) - max(a.left, b.left)
    if ov <= 0:
        return 0.0
    return ov / max(min(a.width, b.width), 1)


def find_article_cards(img: Any, min_title_len: int = 4) -> list[ArticleCard]:
    """在传入的图像中识别搜一搜文章卡片列表。"""
    boxes = ocr(img)

    # --- 规则 1 & 2: 锚点 + 聚类（同列最靠下的时间行才是真实元信息行）---
    anchors = [b for b in boxes if TIME_RE.search(b.text) and not NOISE_RE.match(b.text)]
    anchors.sort(key=lambda b: b.top)

    filtered_anchors: list[TextBox] = []
    for a in anchors:
        if (filtered_anchors and a.top - filtered_anchors[-1].bottom < a.height * 4
                and _x_overlap(a, filtered_anchors[-1]) >= 0.3):
            filtered_anchors[-1] = a  # 用更靠下的一行替换
        else:
            filtered_anchors.append(a)

    cards: list[ArticleCard] = []
    for a in filtered_anchors:
        # --- 规则 3: 同列约束（排除右侧相关搜索栏与无关控件）---
        above = [
            b for b in boxes
            if b is not a
            and b.bottom <= a.top + a.height * 0.5
            and _x_overlap(a, b) >= 0.3
            and len(b.text) >= 2
            and not NOISE_RE.match(b.text)
        ]
        if not above:
            continue

        # --- 规则 4: 向上聚拢，遇到上一个时间行或过大垂直间距即停止 ---
        above.sort(key=lambda b: b.bottom, reverse=True)
        group: list[TextBox] = []
        prev = a
        for b in above:
            if prev.top - b.bottom > a.height * 2.5:
                break
            if TIME_RE.search(b.text):
                break
            group.append(b)
            prev = b

        if not group:
            continue

        # 标题行判定：组内字号（高度）最大的靠上连续行
        max_h = max(b.height for b in group)
        big = [b for b in group if b.height >= max(a.height * 1.1, max_h * 0.85)]
        if not big:
            big = group[:1]  # 兜底：取离锚点最近的一行
        big.sort(key=lambda b: b.top)

        lines = [big[0]]
        for b in big[1:]:
            if b.top - lines[-1].bottom < a.height * 0.9:
                lines.append(b)
            else:
                break

        title = "".join(b.text for b in lines)
        if len(title) < min_title_len:
            continue

        l = min(b.left for b in lines)
        r = max(b.right for b in lines)
        t = min(b.top for b in lines)
        bt = max(b.bottom for b in lines)

        is_acc = any(w in a.text for w in ("原创内容", "条视频", "官方小程序"))
        cards.append(ArticleCard(
            title=title,
            meta=a.text,
            click_x=(l + r) // 2,
            click_y=(t + bt) // 2,
            title_box=(l, t, r, bt),
            is_account=is_acc
        ))

    # 去重（按标题前 12 字符）并按垂直 Y 坐标排序
    out: list[ArticleCard] = []
    seen: set[str] = set()
    for c in sorted(cards, key=lambda c: c.click_y):
        short_key = c.title[:12]
        if short_key not in seen:
            seen.add(short_key)
            out.append(c)

    return out
