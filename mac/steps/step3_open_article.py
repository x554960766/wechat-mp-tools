"""步骤 3: 从搜一搜结果精准定位【公众号】主页，并在公众号主页中随机点击打开一篇真实文章详情。"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

from mac.mac_win import MacWindow, capture_window, list_wechat_windows, find_web_windows, wait_new_web_window
from mac.mac_input import human_sleep, move_and_click, scroll_smooth
from mac.scale import CoordSpace
from mac.mac_ocr import ocr, TextBox
from mac.waiter import ElementNotFound, wait_stable_screen, wait_until

logger = logging.getLogger("wechat_auto_mac.step3")


def _find_official_account_entry_in_search(boxes: list[TextBox]) -> Optional[TextBox]:
    """在搜一搜结果页面中定位【公众号】专属卡片（严格排除小程序和视频号）。"""
    # 查找标记为'公众号'的标签块
    mp_tag = next((b for b in boxes if b.text == "公众号" and b.left < 250), None)
    if mp_tag:
        # 查找公众号标签上方的标题行（如'新京报 ®'）
        title_box = next((
            b for b in boxes
            if b.bottom <= mp_tag.top + 5
            and mp_tag.top - b.bottom < 45
            and b.left < 250
            and not any(x in b.text for x in ("小程序", "视频号", "账号", "搜索", "全部"))
        ), None)
        return title_box if title_box else mp_tag

    # 兜底：查找包含'篇原创内容'的元信息行上方的标题
    meta_box = next((b for b in boxes if "原创内容" in b.text and b.left < 250), None)
    if meta_box:
        title_box = next((
            b for b in boxes
            if b.bottom <= meta_box.top + 5
            and meta_box.top - b.bottom < 65
            and b.left < 250
            and not any(x in b.text for x in ("小程序", "视频号", "账号", "搜索"))
        ), None)
        if title_box:
            return title_box

    return None


def _extract_articles_from_mp_profile(boxes: list[TextBox]) -> list[dict]:
    """在公众号主页窗口中提取文章列表（以'阅读'、'赞'或日期为锚点）。"""
    # 锚点 1: '阅读' 统计行
    read_boxes = [b for b in boxes if "阅读" in b.text and b.left < 250]
    articles = []

    if read_boxes:
        for rb in read_boxes:
            title_boxes = [
                b for b in boxes
                if b is not rb
                and b.bottom <= rb.top + 5
                and rb.top - b.bottom < 85
                and b.left < 320
                and not any(k in b.text for k in ("今天", "昨天", "置顶", "全部", "贴图", "文章", "视频号", "关注"))
            ]
            if title_boxes:
                title_boxes.sort(key=lambda b: b.top)
                full_title = "".join(b.text for b in title_boxes)
                l = min(b.left for b in title_boxes)
                r = max(b.right for b in title_boxes)
                t = min(b.top for b in title_boxes)
                bt = max(b.bottom for b in title_boxes)
                articles.append({
                    "title": full_title,
                    "meta": rb.text,
                    "click_x": (l + r) // 2,
                    "click_y": (t + bt) // 2
                })

    # 兜底：如果未识别到'阅读'，按'今天'/'昨天'日期标签下方的内容提取
    if not articles:
        date_boxes = [b for b in boxes if b.text in ("今天", "昨天") and b.left < 200]
        for db in date_boxes:
            title_boxes = [
                b for b in boxes
                if b is not db
                and b.top >= db.bottom - 5
                and b.top - db.bottom < 60
                and b.left < 320
                and not any(k in b.text for k in ("阅读", "置顶", "全部", "贴图", "文章", "视频号", "关注"))
            ]
            if title_boxes:
                title_boxes.sort(key=lambda b: b.top)
                full_title = "".join(b.text for b in title_boxes)
                l = min(b.left for b in title_boxes)
                r = max(b.right for b in title_boxes)
                t = min(b.top for b in title_boxes)
                bt = max(b.bottom for b in title_boxes)
                articles.append({
                    "title": full_title,
                    "meta": db.text,
                    "click_x": (l + r) // 2,
                    "click_y": (t + bt) // 2
                })

    return articles


def find_and_click_article_mac(
    web_win: MacWindow,
    max_scrolls: int = 3,
    min_candidates: int = 1,
    random_pick: bool = True,
    wait_cred_update_seconds: float = 6.0
) -> dict:
    """
    全流程执行：
    1. 在搜一搜页面中定位【公众号】专属卡片（严格过滤小程序与视频号）并点击；
    2. 等待弹出的【公众号】主页独立窗口；
    3. 聚焦公众号主页，OCR 提取文章列表；
    4. 随机挑选一篇文章点击打开详情页；
    5. 等待文章渲染并检测凭证捕获。
    """
    cs_search = CoordSpace(web_win.window_id, {"x": web_win.x, "y": web_win.y, "w": web_win.w, "h": web_win.h})
    logger.info("搜一搜结果窗口坐标空间: %s", web_win)

    # 阶段 1: 在搜一搜页面中 OCR 寻找并点击【公众号】卡片
    before_ids = {w.window_id for w in list_wechat_windows()}
    old_save_time = 0
    try:
        from backend.account_pool import account_pool
        accs = account_pool._load()
        if accs:
            old_save_time = max(a.get("save_time", 0) for a in accs)
    except Exception:
        pass

    # 阶段 1: 在搜一搜页面中 OCR 寻找并点击【公众号】卡片（支持多屏动态识别）
    mp_entry: Optional[TextBox] = None
    for search_step in range(max_scrolls + 1):
        img_search = wait_stable_screen(lambda: capture_window(web_win), stable_rounds=2, timeout=6.0)
        boxes_search = ocr(img_search)
        mp_entry = _find_official_account_entry_in_search(boxes_search)
        if mp_entry:
            break
        if search_step < max_scrolls:
            logger.info("当前屏未检测到【公众号】条目，向下平滑滚动寻找...")
            cx, cy = cs_search.img_to_screen(img_search.shape[1] // 2, img_search.shape[0] // 2)
            scroll_smooth(-12, cx, cy, step=3)
            human_sleep(0.6, 1.0)

    if not mp_entry:
        raise ElementNotFound("在搜一搜结果中未找到【公众号】条目（已严格排除小程序/视频号）")

    click_x, click_y = mp_entry.center
    screen_x, screen_y = cs_search.img_to_screen(click_x, click_y)
    logger.info("🎯 点击搜一搜结果中的【公众号】卡片: [%s] 屏幕坐标 (%d, %d)", mp_entry.text, screen_x, screen_y)
    move_and_click(screen_x, screen_y)
    human_sleep(1.5, 2.0)

    # 阶段 2: 等待【公众号】主页窗口弹出
    mp_win: Optional[MacWindow] = None
    deadline = time.time() + 10.0
    while time.time() < deadline:
        wins = list_wechat_windows()
        cands = [w for w in wins if w.title == "公众号" or (w.window_id not in before_ids and w.window_id != web_win.window_id)]
        if cands:
            mp_win = cands[0]
            break
        time.sleep(0.4)

    if not mp_win:
        raise ElementNotFound("点击公众号卡片后，未能在预期时间内检测到【公众号】主页窗口")

    logger.info("✅ 成功打开并捕获【公众号】主页窗口: %s", mp_win)

    # 阶段 2.5: 检测【公众号主页】原生会话凭证捕获（profile_ext?action=home）
    cred_captured = False
    try:
        from backend.account_pool import account_pool
        accs = account_pool._load()
        for a in accs:
            if a.get("status") == "active" and a.get("save_time", 0) > old_save_time:
                logger.info("🎉 [MITM 主页凭证已捕获] 账号 [%s] 已建立公众号主页 profile_ext 会话！", a.get("nickname", "未命名"))
                cred_captured = True
                break
    except Exception:
        pass

    # 阶段 3: 聚焦公众号主页窗口并 OCR 解析文章列表
    # 点击标题栏聚焦窗口
    move_and_click(mp_win.x + mp_win.w // 2, mp_win.y + 20)
    human_sleep(0.6, 1.0)

    cs_mp = CoordSpace(mp_win.window_id, {"x": mp_win.x, "y": mp_win.y, "w": mp_win.w, "h": mp_win.h})
    img_mp = wait_stable_screen(lambda: capture_window(mp_win), stable_rounds=2, timeout=6.0)
    boxes_mp = ocr(img_mp)

    articles = _extract_articles_from_mp_profile(boxes_mp)
    if not articles:
        if cred_captured:
            logger.info("主页已捕获有效凭证，但主页中未解析到文章文字（可能纯图或无历史消息），直接返回主页就绪状态。")
            return {
                "success": True,
                "chosen_title": mp_entry.text if mp_entry else "公众号主页",
                "chosen_meta": "profile_ext",
                "click_point": (mp_win.x + mp_win.w // 2, mp_win.y + 20),
                "mp_win": mp_win,
                "cred_captured": True,
                "candidates": [],
            }
        raise ElementNotFound("在【公众号】主页中未识别到有效文章列表")

    logger.info("在【公众号】主页中成功解析出 %d 篇候选文章:", len(articles))
    for i, a in enumerate(articles):
        logger.info("  [%d] 标题: %s | 锚点: %s | 坐标: (%d, %d)", i, a["title"], a["meta"], a["click_x"], a["click_y"])

    # 阶段 4: 随机/首篇选中文章并精确点击
    chosen_art = random.choice(articles) if random_pick else articles[0]
    art_sx, art_sy = cs_mp.img_to_screen(chosen_art["click_x"], chosen_art["click_y"])
    logger.info("🎯 准备打开公众号文章: [%s]，落点屏幕坐标: (%d, %d)", chosen_art["title"], art_sx, art_sy)
    move_and_click(art_sx, art_sy)
    human_sleep(1.5, 2.5)

    # 阶段 5: 等待文章详情加载与凭证捕获检测
    deadline = time.time() + wait_cred_update_seconds
    while time.time() < deadline and not cred_captured:
        try:
            from backend.account_pool import account_pool
            accs = account_pool._load()
            for a in accs:
                if a.get("status") == "active" and a.get("save_time", 0) > old_save_time:
                    logger.info("🎉 [MITM 凭证已捕获] 账号 [%s] 凭证已成功更新！", a.get("nickname", "未命名"))
                    cred_captured = True
                    break
        except Exception:
            pass
        if cred_captured:
            break
        time.sleep(0.5)

    return {
        "success": True,
        "chosen_title": chosen_art["title"],
        "chosen_meta": chosen_art["meta"],
        "click_point": (art_sx, art_sy),
        "mp_win": mp_win,
        "cred_captured": cred_captured,
        "candidates": articles,
    }
