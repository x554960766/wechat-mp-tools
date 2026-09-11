#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信 PC 客户端凭证自动刷新脚本 (Auto Refresh PC WeChat Credentials)
支持 Windows (参考 Access_wechat_article 的 UIAutomation 逻辑) 和 macOS (使用 AppleScript UI 自动化)。
触发微信客户端刷新页面或点击文章，促使微信内置浏览器发起带 key / pass_ticket / token 的请求，
从而由本地 mitm_proxy 捕获最新的客户端凭证。
"""

import sys
import time
import logging
import platform
import subprocess
import threading
from pathlib import Path

# 保证项目根目录在 sys.path 中
_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s")
logger = logging.getLogger("auto_refresh_pc_wechat")

# ── 配置参数 ──────────────────────────────────────────
DAEMON_CHECK_INTERVAL = 5 * 60          # 守护线程检测间隔：5 分钟（测试用）
STALE_THRESHOLD = 5 * 60                # 凭证老化阈值：5 分钟（测试用）
MIN_REFRESH_INTERVAL = 30               # 至少间隔 30 秒才允许再次触发 UI 刷新，防止高频冲突
SUCCESS_COALESCE_SECONDS = 60           # 成功刷新后 1 分钟内合并重复触发
PROACTIVE_RENEW_THRESHOLD = 5 * 60      # biz 专属凭证年龄超过 5 分钟即主动续期（测试用）
PROACTIVE_RETRY_BACKOFF = 2 * 60        # 同一公众号两次主动续期尝试的最小间隔（2分钟）
TARGET_PACING_RANGE = (10, 20)          # 相邻两次定向续期之间的随机间隔（秒）
IDLE_CHECK_INTERVAL = 5 * 60            # 巡检间隔：5 分钟（300 秒，测试用）
WORKER_STARTUP_SILENCE = 5              # 测试模式启动静默期：5 秒

_last_refresh_time = 0
_last_success_time = 0
_daemon_started = False
_daemon_lock = threading.Lock()
_ui_flow_lock = threading.Lock()        # 全局 UI 互斥：同一时刻只允许一个 UI 自动化流程操作微信客户端
_proactive_last_attempt = {}            # biz -> 上次主动续期尝试时间（退避用，防止失败目标霸占名额）

ENABLE_BACKGROUND_ACTIVE_REFRESH = False  # 微信凭证后台主动刷新，默认关闭，需手动开启


def get_auto_refresh_config() -> dict:
    """获取当前定时同步配置状态"""
    return {
        "enabled": ENABLE_BACKGROUND_ACTIVE_REFRESH,
        "interval_minutes": IDLE_CHECK_INTERVAL // 60,
        "interval_seconds": IDLE_CHECK_INTERVAL
    }


def set_auto_refresh_enabled(enabled: bool) -> dict:
    """设置定时同步开关状态"""
    global ENABLE_BACKGROUND_ACTIVE_REFRESH
    ENABLE_BACKGROUND_ACTIVE_REFRESH = bool(enabled)
    logger.info("🔧 定时自动同步开关已更新为: %s (间隔: %d 分钟)", ENABLE_BACKGROUND_ACTIVE_REFRESH, IDLE_CHECK_INTERVAL // 60)
    return get_auto_refresh_config()

def is_wechat_running_macos() -> bool:
    """检测 macOS 上微信是否在运行。"""
    try:
        res = subprocess.run(["pgrep", "-f", "WeChat"], capture_output=True, text=True)
        return res.returncode == 0 and len(res.stdout.strip()) > 0
    except Exception:
        return False


def refresh_wechat_pc_macos(keyword: str = "新京报") -> bool:
    """macOS 下使用 AppleScript + Vision OCR 自动化搜索并点击文章以刷新凭证。"""
    logger.info("正在检测 macOS 微信客户端进程...")

    if not is_wechat_running_macos():
        logger.warning("⚠️ 未检测到运行中的 macOS 微信客户端，请先打开并登录微信 Mac 版。")
        return False

    logger.info("正在调用 macOS UI 自动化流程（搜一搜 + Vision OCR 识别文章卡片 + 精确点击，目标: %s）...", keyword)

    # 1. 优先使用基于 AppleScript + Vision OCR 相对时间锚点的稳定自动化全流程
    try:
        from mac.main_mac import run_wechat_pc_flow_macos
        success = run_wechat_pc_flow_macos(keyword=keyword, auto_cleanup=True)
        if success:
            logger.info("✅ 已通过 macOS UI 自动化成功执行搜一搜文章定位与点击！")
            return True
        else:
            logger.warning("⚠️ macOS UI 自动化流程未能完成。")
            return False
    except Exception as e:
        logger.warning("⚠️ 载入或执行 mac.main_mac 流程异常: %s", e)
        return False


def _find_dropdown_filehelper_box(boxes: list) -> any:
    """从微信搜索下拉列表或独立浮层窗口中精准定位「功能」分类下的「文件传输助手」。"""
    if not boxes:
        return None

    import re

    # 1. 寻找「聊天记录」分类标题作为绝对下边界
    chat_top = float("inf")
    for b in boxes:
        txt = b.text.strip().replace(" ", "")
        if any(k in txt for k in ("聊天记录", "相关的聊天记录", "条聊天记录")) and len(txt) <= 12:
            if b.top < chat_top:
                chat_top = b.top

    # 2. 寻找「功能」分类标题（严格短文本，排除长句）
    func_header = None
    for b in boxes:
        txt = b.text.strip().replace(" ", "")
        # 在独立浮层窗口中，顶部的「功能」分类标题 top 通常在 15~35 之间
        if (txt == "功能" or (len(txt) <= 4 and "功能" in txt and "助手" not in txt and "搜索" not in txt)) and b.top < chat_top:
            func_header = b
            break

    # 3. 寻找在「功能」标题之后紧接着出现的其他分类标题（确定功能区下界）
    next_section_top = chat_top
    if func_header:
        for b in boxes:
            if b.top > func_header.top and b.top < next_section_top:
                txt = b.text.strip().replace(" ", "")
                if txt in ("联系人", "群聊", "公众号", "收藏", "小程序", "表情", "文章") or (len(txt) <= 4 and any(k in txt for k in ("联系人", "群聊", "收藏"))):
                    next_section_top = b.top

    # 4. 严格清洗候选块：只保留纯粹的「文件传输助手」条目，彻底排除对话记录
    def _is_pure_filehelper_item(b) -> bool:
        raw = b.text.strip()
        txt = raw.replace(" ", "")
        # 排除包含冒号的聊天对话格式
        if ":" in raw or "：" in raw:
            return False
        # 排除对话记录常见特征词
        chat_keywords = ("记录", "相关的", "条相关", "昨天", "今天", "撤回", "图片", "视频", "语音", "弱智")
        if any(k in txt for k in chat_keywords):
            return False
        # 排除时间或日期格式
        if re.search(r'\d{1,2}:\d{2}', raw) or re.search(r'\d{4}[-/.]\d{1,2}', raw):
            return False
        # 去掉 OCR 识别出的图标前缀杂字符（如“口”、“■”或字母）
        clean_txt = re.sub(r'^[^\u4e00-\u9fa5]+', '', txt)
        # 必须包含“文件传输助手”且长度极短（真正条目汉字仅有6个字）
        if "文件传输助手" not in clean_txt or len(clean_txt) > 8:
            return False
        # 必须在聊天记录分类上方
        if b.top >= chat_top:
            return False
        return True

    valid_cands = [b for b in boxes if _is_pure_filehelper_item(b)]
    if not valid_cands:
        return None

    # 按垂直 y 坐标从小到大排序
    valid_cands.sort(key=lambda x: x.top)

    # 策略 1（最高优先级）：识别到了「功能」分类标题，取紧随在「功能」标题下方的第一项！
    if func_header:
        below_func = [b for b in valid_cands if b.top > func_header.top and b.top < next_section_top]
        if below_func:
            target = below_func[0]
            logger.info("🎯 成功在「功能」标题 (y=%d) 下方匹配到文件传输助手: %s (y=%d)", func_header.top, target.text, target.top)
            return target

    # 策略 2（次高优先级）：纯净匹配条目
    strict_cands = [b for b in valid_cands if re.sub(r'^[^\u4e00-\u9fa5]+', '', b.text.strip().replace(" ", "")) == "文件传输助手"]
    if strict_cands:
        target = strict_cands[0]
        logger.info("🎯 精确定位到纯净「文件传输助手」功能条目: %s (y=%d)", target.text, target.top)
        return target

    target = valid_cands[0]
    logger.info("🎯 选取纯净「文件传输助手」候选: %s (y=%d)", target.text, target.top)
    return target


def bring_window_to_front(win) -> bool:
    """将指定微信窗口置于最前台顶层聚焦。"""
    try:
        from mac.mac_win import activate_wechat, raise_window
        from mac.mac_input import human_sleep
        activate_wechat()
        raise_window(win)
        human_sleep(0.3, 0.5)
        return True
    except Exception as e:
        logger.warning("置顶聚焦窗口失败: %s", e)
        return False


def _trigger_and_click_portal_button(web_win) -> bool:
    """在聚合页 Web 窗口中将窗口置顶并通过 OCR 动态定位并点击「一键开始全自动流转授权」按钮（绝不使用写死坐标）。"""
    try:
        from mac.mac_win import capture_window, raise_window, find_main_window
        from mac.mac_ocr import ocr
        from mac.scale import CoordSpace
        from mac.mac_input import move_and_click, human_sleep, scroll
        import pyautogui

        bring_window_to_front(web_win)
        human_sleep(0.4, 0.6)

        main_win_chk = find_main_window(timeout=0.2)
        is_embedded = ("(内嵌)" in (web_win.title or "")) or bool(main_win_chk and web_win.window_id == main_win_chk.window_id and web_win.w >= 850)
        scroll_x = web_win.x + (int(web_win.w * 0.78) if is_embedded else web_win.w // 2)
        scroll_y = web_win.y + (int(web_win.h * 0.5) if is_embedded else web_win.h // 2)
        pane_min_x = int(web_win.w * 0.55) if is_embedded else 0

        # 视口复位：平滑向上滚动确保页面处于顶部，防止流转按钮被滚出视口
        try:
            move_and_click(scroll_x, scroll_y)
            scroll(25)
            human_sleep(0.4, 0.6)
        except Exception:
            pass

        # 检查是否已经在运行
        try:
            from backend.auth import _batch_sync_state
            if _batch_sync_state.get("running"):
                logger.info("ℹ️ 后台全自动流水线已经在运行中，无需重复点击。")
                return True
        except Exception:
            pass

        # 尝试最多 4 次 OCR 动态探测（支持页面渲染与滚动微延时）
        btn_box = None
        cs_w = None
        for attempt in range(4):
            img_w = capture_window(web_win)
            if img_w is None or img_w.size == 0:
                human_sleep(0.3, 0.5)
                continue

            boxes_w = ocr(img_w)
            cs_w = CoordSpace(web_win.window_id, {"x": web_win.x, "y": web_win.y, "w": web_win.w, "h": web_win.h})

            # 严格多维度打分匹配目标按钮，杜绝误点标题或说明文字
            candidates = []
            for b in boxes_w:
                if b.left < pane_min_x:
                    continue
                txt = b.text.replace(" ", "").strip()
                # 排除非按钮文本（标题、清单、建议说明等）
                if "重新执行" not in txt and any(bad in txt for bad in ("中心", "清单", "建议", "说明", "低风控", "配额", "间隔", "就绪", "复制")):
                    continue
                # 匹配按钮特征关键词
                score = 0
                if "重新执行" in txt: score += 25
                if "流转授权" in txt: score += 20
                if "全自动" in txt: score += 15
                if "一键开始" in txt: score += 12
                if "自动流转" in txt: score += 10
                if "开始" in txt: score += 5
                if "授权" in txt: score += 4
                if "一键" in txt: score += 3

                if score > 0:
                    candidates.append((score, b))

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                btn_box = candidates[0][1]
                break

            # 若未找到，在窗口内再次向上滚动重试
            move_and_click(scroll_x, scroll_y)
            scroll(15)
            human_sleep(0.4, 0.6)

        if btn_box and cs_w:
            sx, sy = cs_w.img_to_screen(btn_box.center[0], btn_box.center[1])
            logger.info("✅ 聚合页已置顶，通过 OCR 动态精准定位到流转按钮 [%s] 坐标 (%d, %d)", btn_box.text, sx, sy)

            # 点击并进行闭环验证，若未触发则自动重试
            for click_try in range(2):
                pyautogui.click(sx, sy)
                human_sleep(0.8, 1.2)

                # 验证是否成功拉起后台流水线
                try:
                    from backend.auth import _batch_sync_state
                    if _batch_sync_state.get("running"):
                        logger.info("🚀 闭环确认：全自动流转流水线已成功进入运行状态！")
                        return True
                except Exception:
                    pass

                # 检查页面按钮文案是否更新
                img_chk = capture_window(web_win)
                boxes_chk = ocr(img_chk)
                if any(any(k in b.text for k in ("正在启动", "正在同步", "流转授权流水线")) for b in boxes_chk):
                    logger.info("🚀 闭环确认：聚合页状态已切换至正在同步！")
                    return True

                logger.info("第 %d 次点击流转按钮未见状态更新，尝试重新聚焦并重试点击...", click_try + 1)
                raise_window(web_win)
                human_sleep(0.4, 0.6)

            return True
        else:
            logger.warning("❌ OCR 未能在聚合页窗口识别到流转授权按钮，取消硬编码点击，请检查页面内容")
            return False
    except Exception as e:
        logger.warning("在聚合页点击授权按钮异常: %s", e)
        return False


def _wait_batch_portal_completion(sync_start_time: float, timeout_seconds: float = 45.0) -> bool:
    """等待单窗口流水线自动流转激活所有公众号主页会话（不关闭窗口，保留以便下次复用）。
    必须校验 updated_at >= sync_start_time - 2.0，确保凭证是本次新同步捕获的，而非复用历史旧凭证！
    """
    logger.info("⏳ 正在等待单窗口流水线自动流转激活所有公众号主页会话 (本次启动时间戳: %.2f)...", sync_start_time)
    from backend.config import ACCOUNTS_FILE, load_json
    from backend.account_pool import AccountPool, account_pool
    accounts_sub = load_json(ACCOUNTS_FILE, [])
    target_count = len(accounts_sub)

    deadline = time.time() + timeout_seconds
    all_ready = False
    ready_cnt = 0
    while time.time() < deadline:
        time.sleep(1.5)
        pool_acc = account_pool.acquire() or {}
        ready_cnt = 0
        now_check = time.time()
        for acc in accounts_sub:
            fakeid = acc.get("fakeid")
            cred = AccountPool.get_biz_credential(pool_acc, fakeid) if pool_acc else {}
            # 校验：1) 具备 key 2) getmsg_ready 3) 本次同步新捕获或处于 30 分钟内的新鲜窗口
            if cred.get("getmsg_ready") and cred.get("key"):
                updated_at = cred.get("updated_at", 0)
                if updated_at >= (sync_start_time - 2.0) or (now_check - updated_at < 1800):
                    ready_cnt += 1
        logger.info("当前批量授权同步进度: [%d/%d] (有效就绪凭证)", ready_cnt, target_count)
        if target_count > 0 and ready_cnt >= target_count:
            all_ready = True
            break

        # 闭环状态校验：检查后台流水线是否已报告全部完成
        try:
            from backend.auth import _batch_sync_state
            if _batch_sync_state.get("completed"):
                all_ready = True
                break
        except Exception:
            pass

    if all_ready:
        logger.info("🎉 全部 %d 个公众号主页最新凭证已 100%% 自动捕获完成！", target_count)
        now = time.time()
        for acc in accounts_sub:
            fakeid = acc.get("fakeid")
            if fakeid:
                _proactive_last_attempt[fakeid] = now
        return True
    else:
        logger.warning("⚠️ 批量授权超时或部分未捕获最新凭证 (本次成功捕获: %d/%d)", ready_cnt, target_count)
        return False


def run_batch_portal_flow_macos(timeout_seconds: float = 45.0) -> bool:
    """macOS 下全自动唤起或复用批量授权聚合中心，自动流转建立全部公众号主页会话。
    多层级智能探测流程：
    【层级 1】优先检查屏幕上是否已有打开的「批量授权中心」网页窗口：
              若有，先将窗口置顶聚焦，再通过 OCR 定位并主动点击「一键开始批量授权」按钮！
    【层级 2】若无网页窗口，先检查微信主窗口当前是否已停留在「文件传输助手」会话界面且有链接：
              若有，直接点击链接气泡打开聚合页，跳过搜索！
    【层级 3】若未停留在「文件传输助手」，先在左侧会话列表查找点击；若无才按 Cmd+F 搜索并在下拉菜单中精准点击「功能 -> 文件传输助手」。
    【层级 4】发送/点击聚合页链接后，将新打开的聚合页窗口置顶并主动点击「一键开始批量授权」，等待流转完成（不关闭网页窗口）。
    """
    if not is_wechat_running_macos():
        logger.warning("未检测到运行中的 macOS 微信客户端，请先打开微信 Mac 版。")
        return False

    if not _ui_flow_lock.acquire(blocking=False):
        logger.info("🔒 另一个 UI 自动化流程正在执行中，跳过本次批量聚合页流转。")
        return False

    sync_start_time = time.time()

    try:
        from mac.mac_win import (
            find_main_window, activate_wechat, capture_window,
            list_wechat_windows, wait_new_web_window, find_web_windows,
            find_portal_window, raise_window
        )
        from mac.mac_ocr import ocr
        from mac.scale import CoordSpace
        from mac.mac_input import move_and_click, type_text_via_clipboard, human_sleep, press
        # 确保凭证同步代理助手已在运行，否则无法拦截与流转凭据
        try:
            from backend.mitm_proxy import ProxyManager
            mgr = ProxyManager.get_instance()
            if not mgr.running:
                logger.info("⚡️ 自动启动凭证同步代理助手...")
                mgr.start()
        except Exception as ex_proxy:
            logger.warning("启动凭证同步代理助手异常: %s", ex_proxy)

        # ──【层级 1】：严格判断当前屏幕上是否已有打开的聚合页 Web 窗口（排除视频号、公众号名片等）──
        portal_win = find_portal_window(verify_content=True)

        if portal_win:
            logger.info("🔍 检测到聚合页 Webview 窗口已存在 (id=%d, bounds=%s)，直接将窗口置顶并主动点击「一键开始全自动流转授权」...",
                        portal_win.window_id, (portal_win.x, portal_win.y, portal_win.w, portal_win.h))
            _trigger_and_click_portal_button(portal_win)
            return _wait_batch_portal_completion(sync_start_time, timeout_seconds)

        # ──【层级 2 & 3】：屏幕上没有聚合页窗口，激活主窗口并定位「文件传输助手」 ──
        logger.info("🚀 屏幕上未发现聚合页窗口，正在激活并置顶微信主窗口...")
        activate_wechat()
        human_sleep(0.5, 0.8)

        main_win = find_main_window(timeout=3.0)
        if not main_win:
            logger.warning("未找到微信主窗口。")
            return False

        # 确保微信主窗口置顶聚焦（覆盖其他可能处于前台的公众号或视频号窗口）
        bring_window_to_front(main_win)
        human_sleep(0.4, 0.6)

        cs_main = CoordSpace(main_win.window_id, {"x": main_win.x, "y": main_win.y, "w": main_win.w, "h": main_win.h})
        img_main = capture_window(main_win)
        boxes_main = ocr(img_main)
        left_limit_px = min(230, int(img_main.shape[1] * 0.3))
        right_start_px = min(220, int(img_main.shape[1] * 0.25))

        def _clean_text(text: str) -> str:
            import re
            return re.sub(r'[\s\u3000]+', '', (text or "").lower()).replace('：', ':').replace('，', ',').replace('。', '.')

        def _is_portal_link(text: str) -> bool:
            t = _clean_text(text)
            if "5200" in t and any(k in t for k in ("mp-batch", "batch", "portal", "auth", "127.0.0.1", "localhost")):
                return True
            if "mp-batch-portal" in t or "batch-portal" in t or "auth/mp-batch" in t:
                return True
            if any(k in t for k in ("公众号批量授权", "批量授权聚合中心", "公众号主页批量授权", "批量授权中心")):
                return True
            return False

        # ──【阶段 1】：严格检测屏幕上是否已存在聚合页（独立弹窗或微信 4.x 内嵌第三栏）──
        portal_win = find_portal_window(verify_content=True)
        if portal_win:
            logger.info("🎉 屏幕上已检测到打开的「批量授权聚合中心」网页窗口 (id=%d, bounds=%s)，直接激活置顶并执行后续同步凭证！",
                        portal_win.window_id, (portal_win.x, portal_win.y, portal_win.w, portal_win.h))
            raise_window(portal_win)
            human_sleep(0.3, 0.5)
            _trigger_and_click_portal_button(portal_win)
            return _wait_batch_portal_completion(sync_start_time, timeout_seconds)

        # ──【阶段 2】：未检测到聚合页，激活主窗口，判断当前是否已在「文件传输助手」 ──
        logger.info("🚀 屏幕上未发现已打开的聚合页窗口，正在激活微信主窗口...")
        activate_wechat()
        human_sleep(0.5, 0.8)

        main_win = find_main_window(timeout=3.0)
        if not main_win:
            logger.warning("未找到微信主窗口。")
            return False

        # 再次确认主窗口激活后是否直接呈现聚合页（如内嵌分栏或被唤醒窗口）
        portal_win = find_portal_window(verify_content=True)
        if portal_win:
            logger.info("🎉 激活主窗口后直接检测到「批量授权聚合中心」界面，立即执行同步凭证！")
            raise_window(portal_win)
            human_sleep(0.3, 0.5)
            _trigger_and_click_portal_button(portal_win)
            return _wait_batch_portal_completion(sync_start_time, timeout_seconds)

        bring_window_to_front(main_win)
        human_sleep(0.4, 0.6)

        cs_main = CoordSpace(main_win.window_id, {"x": main_win.x, "y": main_win.y, "w": main_win.w, "h": main_win.h})
        img_main = capture_window(main_win)
        boxes_main = ocr(img_main) if img_main is not None else []
        left_limit_px = min(230, int(img_main.shape[1] * 0.3)) if img_main is not None else 230
        right_start_px = min(220, int(img_main.shape[1] * 0.25)) if img_main is not None else 220

        def _is_in_filehelper_window(w) -> bool:
            img = capture_window(w)
            if img is None:
                return False
            boxes = ocr(img)
            # 严格校验：顶部标题栏（top < 65, left >= 180），必须是纯粹的「文件传输助手」，排除对话内容
            for b in boxes:
                txt = b.text.strip().replace(" ", "")
                if (txt in ("文件传输助手", "文件传输助手(FileTransfer)") or (txt.startswith("文件传输助手") and len(txt) <= 8)) and b.top < 65 and b.left >= 180:
                    if ":" not in b.text and "：" not in b.text and "记录" not in txt:
                        return True
            return False

        if _is_in_filehelper_window(main_win):
            logger.info("🎉 当前微信主窗口已直接停留在「文件传输助手」会话界面，无需搜索！")
        else:
            for attempt in range(1, 3):
                logger.info("👉 正在定位并打开微信「文件传输助手」会话界面 (第 %d/2 次尝试)...", attempt)
                if attempt > 1:
                    press("escape", times=2)
                    human_sleep(0.3, 0.5)
                    move_and_click(main_win.x + 35, main_win.y + 145)
                    human_sleep(0.5, 0.8)

                img_cur = capture_window(main_win)
                boxes_cur = ocr(img_cur) if img_cur is not None else []
                cs_cur = CoordSpace(main_win.window_id, {"x": main_win.x, "y": main_win.y, "w": main_win.w, "h": main_win.h})

                # 优先检查左侧会话列表中是否直接可见「文件传输助手」
                list_helper = next((b for b in boxes_cur if b.left < 230 and b.top > 45 and ("文件传" in b.text or "传输助手" in b.text) and ":" not in b.text and "：" not in b.text and len(b.text.strip()) <= 8), None)
                if list_helper:
                    sx, sy = cs_cur.img_to_screen(*list_helper.center)
                    logger.info("✅ 直接在左侧会话列表中定位到「文件传输助手」，点击坐标 (%d, %d)...", sx, sy)
                    move_and_click(sx, sy)
                    human_sleep(0.8, 1.2)
                else:
                    logger.info("🔍 正在通过微信搜索「文件传输助手」...")
                    search_box = next((b for b in boxes_cur if b.left < left_limit_px and b.top < 80 and any(k in b.text for k in ("搜索", "Search"))), None)
                    if search_box:
                        search_x, search_y = cs_cur.img_to_screen(search_box.center[0], search_box.center[1])
                        move_and_click(search_x, search_y)
                    else:
                        move_and_click(main_win.x + 100, main_win.y + 25)

                    human_sleep(0.3, 0.5)
                    type_text_via_clipboard("文件传输助手", clear_first=True)
                    human_sleep(0.6, 0.8)

                    popover_win = None
                    t_start = time.time()
                    while time.time() - t_start < 1.5:
                        for w in list_wechat_windows():
                            if not w.title and 200 <= w.w <= 550 and w.h >= 180:
                                if abs(w.x - main_win.x) <= 350:
                                    popover_win = w
                                    break
                        if popover_win:
                            break
                        human_sleep(0.15, 0.25)

                    target_box = None
                    target_cs = cs_cur

                    if popover_win:
                        cs_pop = CoordSpace(popover_win.window_id, {"x": popover_win.x, "y": popover_win.y, "w": popover_win.w, "h": popover_win.h})
                        img_pop = capture_window(popover_win)
                        boxes_pop = ocr(img_pop) if img_pop is not None else []
                        target_box = _find_dropdown_filehelper_box(boxes_pop)
                        if target_box:
                            target_cs = cs_pop
                            logger.info("🎯 在独立搜索浮层窗口 (id=%s) 中检测到文件传输助手", popover_win.window_id)

                    if not target_box:
                        img_main2 = capture_window(main_win)
                        boxes2 = ocr(img_main2) if img_main2 is not None else []
                        drop_cands = [b for b in boxes2 if b.left < left_limit_px + 120]
                        target_box = _find_dropdown_filehelper_box(drop_cands)

                    if target_box:
                        sx, sy = target_cs.img_to_screen(target_box.center[0], target_box.center[1])
                        logger.info("✅ 精确定位到「功能 -> 文件传输助手」[%s]，点击屏幕坐标 (%d, %d)", target_box.text, sx, sy)
                        move_and_click(sx, sy)
                        human_sleep(0.8, 1.2)
                    else:
                        logger.warning("⚠️ 未在搜索浮层中定位到「功能」下方的文件传输助手，按 Escape 取消搜索防误触...")
                        press("escape")
                        human_sleep(0.3, 0.5)

                if _is_in_filehelper_window(main_win):
                    logger.info("🎉 成功准确选中并显示「文件传输助手」聊天页面！")
                    break
                else:
                    logger.warning("⚠️ 安全校验未通过：当前窗口非「文件传输助手」，按 Escape 退出以防误操作...")
                    press("escape", times=2)
                    human_sleep(0.4, 0.6)

        # ──【阶段 3】：在「文件传输助手」中通过 OCR 智能定位已有链接或在输入框发送链接 ──
        human_sleep(0.4, 0.6)

        # 再次检查是否有聚合页在会话切换后已打开
        portal_win = find_portal_window(verify_content=True)
        if portal_win:
            logger.info("🎉 切换至文件助手后检测到聚合页已打开，直接置顶并执行同步凭证！")
            raise_window(portal_win)
            human_sleep(0.3, 0.5)
            _trigger_and_click_portal_button(portal_win)
            return _wait_batch_portal_completion(sync_start_time, timeout_seconds)

        img_chat = capture_window(main_win)
        chat_boxes = ocr(img_chat) if img_chat is not None else []

        if not _is_in_filehelper_window(main_win):
            logger.warning("⚠️ 安全拦截：当前聊天窗口非「文件传输助手」，取消操作以防误触！")
            return False

        # 1. 优先扫描单行或相连两行气泡，寻找已有聚合页链接
        link_box = None
        # 倒序查找单块命中
        for b in reversed(chat_boxes):
            if b.left > right_start_px and _is_portal_link(b.text):
                link_box = b
                break

        # 若单块未匹配，尝试相连两块合并匹配（解决 OCR 将长 URL 换行切断的问题）
        if not link_box and len(chat_boxes) >= 2:
            sorted_boxes = sorted([b for b in chat_boxes if b.left > right_start_px], key=lambda x: (x.top, x.left))
            for i in range(len(sorted_boxes) - 1, 0, -1):
                b1, b2 = sorted_boxes[i - 1], sorted_boxes[i]
                if abs(b2.top - b1.bottom) < 30 and _is_portal_link(b1.text + b2.text):
                    link_box = b2
                    break

        if link_box:
            sx, sy = cs_main.img_to_screen(link_box.center[0], link_box.center[1])
            logger.info("✅ 发现已有聚合页链接/卡片气泡 [%s]，直接点击坐标 (%d, %d)...", link_box.text, sx, sy)
            move_and_click(sx, sy)
        else:
            portal_url = "http://127.0.0.1:5200/api/auth/mp-batch-portal"
            logger.info("👉 未在聊天记录中检测到有效的 5200 聚合页链接，定位输入框发送聚合页链接...")
            # 精准点击聊天窗口底部的输入区域
            input_x = main_win.x + int(main_win.w * 0.5)
            input_y = main_win.y + main_win.h - 50
            move_and_click(input_x, input_y)
            human_sleep(0.2, 0.4)
            type_text_via_clipboard(portal_url, clear_first=False)
            human_sleep(0.3, 0.5)
            press("return")
            human_sleep(0.9, 1.3)

            # 发送后重新 OCR 截取，精准定位刚刚发出的最新消息气泡
            img_chat2 = capture_window(main_win)
            chat_boxes2 = ocr(img_chat2) if img_chat2 is not None else []
            link_box = next((b for b in reversed(chat_boxes2) if b.left > right_start_px and _is_portal_link(b.text)), None)
            if link_box:
                sx, sy = cs_main.img_to_screen(link_box.center[0], link_box.center[1])
                logger.info("✅ 已通过 OCR 定位到新发送的聚合页链接气泡 [%s]，点击坐标 (%d, %d)...", link_box.text, sx, sy)
                move_and_click(sx, sy)
            else:
                # 智能识别输入框上方的最新一条消息气泡，杜绝盲目硬编码偏移
                msg_cands = [b for b in chat_boxes2 if b.left > right_start_px and b.top > img_chat2.shape[0] * 0.35 and b.bottom < img_chat2.shape[0] - 70]
                if msg_cands:
                    msg_cands.sort(key=lambda x: x.bottom, reverse=True)
                    bottom_bubble = msg_cands[0]
                    bx, by = cs_main.img_to_screen(bottom_bubble.center[0], bottom_bubble.center[1])
                    logger.info("🎯 点击刚发出的最新消息气泡底部条目 [%s] (%d, %d)...", bottom_bubble.text, bx, by)
                    move_and_click(bx, by)
                else:
                    logger.info("👉 兜底点击输入框正上方消息区域...")
                    move_and_click(input_x, main_win.y + main_win.h - 100)

        # ──【阶段 4】：等待聚合页网页窗口打开，将其置顶并主动点击「一键开始全自动流转授权」 ──
        human_sleep(1.5, 2.2)
        deadline_portal = time.time() + 8.0
        target_web_win = None
        while time.time() < deadline_portal:
            time.sleep(0.5)
            target_web_win = find_portal_window(verify_content=True)
            if target_web_win:
                break

        if target_web_win:
            logger.info("🎉 聚合页窗口已成功唤起并呈现，置顶并主动点击「一键开始全自动流转授权」...")
            raise_window(target_web_win)
            human_sleep(0.3, 0.5)
            _trigger_and_click_portal_button(target_web_win)
        else:
            logger.warning("未能自动检测到新打开的聚合页窗口，请手动确认微信界面")

        return _wait_batch_portal_completion(sync_start_time, timeout_seconds)
    finally:
        _ui_flow_lock.release()


# ── Windows 检测与刷新 ────────────────────────────────

def refresh_wechat_pc_windows(keyword: str = None) -> bool:
    """Windows 下按需选择流水线：
    - 带 keyword → 定向流程（搜索该公众号 → 打开文章 → 捕获该 biz 专属凭证）；
    - 无 keyword → 通用三级策略流水线（刷新已有文章窗口 → 订阅号消息点文章）；
    两者失败时均降级为旧的 uiautomation SendKeys 方案。"""
    # 1a. 定向：为指定公众号建立/续期独立会话
    if keyword:
        try:
            from windows.win_flow import run_targeted_account_flow_windows
            if run_targeted_account_flow_windows(keyword=keyword, auto_cleanup=True):
                logger.info("✅ 已通过 Windows 定向流水线为 [%s] 刷新凭证！", keyword)
                return True
            logger.warning("⚠️ Windows 定向流水线未捕获 [%s] 的凭证，降级为通用方案...", keyword)
        except ImportError as e:
            logger.warning("⚠️ windows 模块不可用 (%s)，降级为通用方案...", e)
        except Exception as e:
            logger.warning("⚠️ Windows 定向流水线执行异常: %s，降级为通用方案...", e)

    # 1b. 通用：三级策略流水线（不占用真实鼠标，参考 Access_wechat_article）
    try:
        from windows.win_flow import run_wechat_pc_flow_windows
        success = run_wechat_pc_flow_windows(auto_cleanup=True)
        if success:
            logger.info("✅ 已通过 Windows UI 自动化流水线成功触发凭证刷新！")
            return True
        logger.warning("⚠️ Windows 自动化流水线未捕获新凭证，降级为 SendKeys 备用方案...")
    except ImportError as e:
        logger.warning("⚠️ windows 模块不可用 (%s)，降级为 SendKeys 备用方案...", e)
    except Exception as e:
        logger.warning("⚠️ windows 自动化流水线执行异常: %s，降级为 SendKeys 备用方案...", e)

    # 2. 兜底：旧的 uiautomation SendKeys 方案（保留原有行为）
    return _legacy_refresh_windows_sendkeys()


def _legacy_refresh_windows_sendkeys() -> bool:
    """旧方案：使用 uiautomation 查找公众号/文章窗口并激活，发送 F5/Ctrl+R。"""
    logger.info("正在尝试使用 uiautomation 在 Windows 上查找微信客户端窗口...")
    try:
        import uiautomation as auto
    except ImportError:
        logger.error("❌ Windows 环境需安装 uiautomation 库 (pip install uiautomation)")
        return False

    try:
        root = auto.GetRootControl()
        target_win = None
        candidates = ["公众号", "服务号", "订阅号", "微信", "WeChat"]

        for win in root.GetChildren():
            win_name = win.Name or ""
            if any(cand in win_name for cand in candidates):
                target_win = win
                break

        if not target_win:
            logger.warning("⚠️ 未在 Windows 桌面上找到微信相关窗口。")
            return False

        logger.info(f"找到微信窗口: '{target_win.Name}', 尝试激活并发送刷新快捷键 (F5/Ctrl+R)...")
        target_win.SetFocus()
        time.sleep(0.5)
        auto.SendKeys("{F5}")
        time.sleep(0.5)
        auto.SendKeys("{Ctrl}r")
        logger.info("✅ 已成功向 Windows 微信客户端窗口发送刷新指令。")
        return True
    except Exception as e:
        logger.error("❌ Windows 窗口刷新执行异常: %s", e)
        return False


def run_batch_portal_flow_windows(timeout_seconds: float = 45.0) -> bool:
    """Windows 下全自动唤起或复用批量授权聚合中心，自动流转建立全部公众号主页会话。
    【层级 1】优先检查屏幕上是否已有打开的「批量授权中心」网页窗口：若有，将其置顶并点击「一键开始批量授权」；
    【层级 2 & 3】若无网页窗口，定位微信主窗口，进入「文件传输助手」，点击已有链接或在输入框发送聚合页链接并点击；
    【层级 4】等待聚合页窗口打开，将其置顶并点击「一键开始批量授权」，等待所有凭证流转完成。
    """
    try:
        from windows import win_window as ww
        from windows import win_input as wi
        from windows.win_batch_runner import WinBatchRunner
    except ImportError as e:
        logger.warning("windows 模块不可用: %s", e)
        return False

    if not ww.is_wechat_running_windows():
        logger.warning("未检测到运行中的 Windows 微信客户端，请先打开微信。")
        return False

    sync_start_time = time.time()

    # 确保凭证同步代理助手已在运行
    try:
        from backend.mitm_proxy import ProxyManager
        mgr = ProxyManager.get_instance()
        if not mgr.running:
            logger.info("⚡️ 自动启动凭证同步代理助手...")
            mgr.start()
    except Exception as ex_proxy:
        logger.warning("启动凭证同步代理助手异常: %s", ex_proxy)

    try:
        runner = WinBatchRunner()
        portal_win = runner._ensure_portal_window()
        if not portal_win:
            logger.warning("未能打开或定位到 Windows 聚合页窗口。")
            return False

        # 尝试点击聚合页中的「一键开始全自动流转授权」按钮
        try:
            import uiautomation as auto
            ctrl = auto.ControlFromHandle(portal_win.hwnd)
            if ctrl:
                btn = None
                for c, _ in auto.WalkControl(ctrl, maxDepth=10):
                    name = (c.Name or "").strip()
                    if any(k in name for k in ("一键开始", "全自动流转", "开始批量", "重新执行")):
                        btn = c
                        break
                if btn:
                    r = btn.BoundingRectangle
                    if r.right > r.left and r.bottom > r.top:
                        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                        logger.info("🎯 [UIA 命中] 定位到聚合页流转按钮 [%s]，点击坐标 (%d, %d)...", btn.Name, cx, cy)
                        wi.post_click(portal_win.hwnd, cx, cy)
        except Exception as e_click:
            logger.debug("点击聚合页流转按钮异常: %s", e_click)

        return _wait_batch_portal_completion(sync_start_time, timeout_seconds)
    except Exception as ex:
        logger.warning("Windows 批量聚合页流转执行异常: %s", ex)
        return False


# ── 触发入口（全局 UI 互斥 + 触发合并 + 防刷冷却） ────

def trigger_pc_wechat_refresh(keyword: str = None, force: bool = False) -> bool:
    """根据操作系统自动选择对应的 PC 微信刷新方案。

    - keyword 指定时：定向搜索该公众号刷新；
    - keyword 未指定时：macOS / Windows 默认走批量授权聚合页，一次性同步所有公众号凭证；
    - 全局互斥：同一时刻只允许一个 UI 流程在跑，其余触发直接跳过；
    - 触发合并：成功刷新后 SUCCESS_COALESCE_SECONDS 内的通用触发直接跳过；
    - 防刷冷却：MIN_REFRESH_INTERVAL 内不重复触发；
    - force=True：绕过冷却与合并，但仍然遵守全局互斥。
    """
    global _last_refresh_time, _last_success_time
    now = time.time()

    if not force:
        if not ENABLE_BACKGROUND_ACTIVE_REFRESH:
            logger.debug("主动自动化刷新已禁用，跳过本次自动触发。")
            return False
        if now - _last_refresh_time < MIN_REFRESH_INTERVAL:
            remaining = int(MIN_REFRESH_INTERVAL - (now - _last_refresh_time))
            logger.info("⏳ 处于自动刷新保护冷却期（剩余 %d 秒），跳过本次触发...", remaining)
            return False
        if _last_success_time and now - _last_success_time < SUCCESS_COALESCE_SECONDS:
            remaining = int(SUCCESS_COALESCE_SECONDS - (now - _last_success_time))
            logger.info("⏳ %d 秒内已有成功刷新，合并本次重复触发（剩余 %d 秒）...", SUCCESS_COALESCE_SECONDS, remaining)
            return False

    _last_refresh_time = now
    current_os = platform.system()

    if current_os == "Darwin":
        if keyword:
            # 用户明确指定单个关键词时，走单号搜一搜流程
            if not _ui_flow_lock.acquire(blocking=False):
                logger.info("🔒 另一个 UI 自动化流程正在执行中，本次触发跳过（互斥保护）。")
                return False
            try:
                ok = refresh_wechat_pc_macos(keyword=keyword)
                if ok:
                    _last_success_time = time.time()
                return ok
            finally:
                _ui_flow_lock.release()
        else:
            # 未指定关键词时，默认使用聚合页批量同步全部公众号主页凭证
            ok = run_batch_portal_flow_macos()
            if ok:
                _last_success_time = time.time()
            return ok

    elif current_os == "Windows":
        if not _ui_flow_lock.acquire(blocking=False):
            logger.info("🔒 另一个 UI 自动化流程正在执行中，本次触发跳过（互斥保护）。")
            return False
        try:
            if keyword:
                ok = refresh_wechat_pc_windows(keyword=keyword)
            else:
                # 用户未指定关键词时，优先走 Windows 批量授权聚合页流转（体验与 macOS 完全一致）
                ok = run_batch_portal_flow_windows()
                if not ok:
                    # 若批量聚合页未完成，降级为通用流水线
                    ok = refresh_wechat_pc_windows(keyword=None)
            if ok:
                _last_success_time = time.time()
            return ok
        finally:
            _ui_flow_lock.release()

    else:
        logger.warning("⚠️ 操作系统 [%s] 暂不支持自动操控 PC 客户端。", current_os)
        return False


# ── 主动续期 worker：队列优先 → biz 老化 → 全局兜底 ──

def _next_refresh_target():
    """选出下一个需要 UI 续期的公众号目标。
    优先级：① 刷新队列中采集失败的号（最紧迫）→ ② 订阅中 biz 凭证缺失或最老且超阈值的号。
    返回 {"biz","name","reason","attempts"} 或 None。"""

    # 1) 失败入队优先（"自动跳过 + 及时补凭证"）
    try:
        from backend.refresh_queue import refresh_queue
        item = refresh_queue.pop()
        if item:
            # 无名称无法定向搜索，尝试从订阅列表反查
            if not item.get("name"):
                item["name"] = _resolve_name_by_biz(item.get("biz", ""))
                if not item["name"]:
                    logger.warning("刷新队列中的 biz=%s 无法解析公众号名称，跳过（无法定向搜索）",
                                   (item.get("biz") or "")[:14])
                    return None
            return item
    except Exception as e:
        logger.debug("读取刷新队列失败: %s", e)

    # 2) 主动续期：订阅中 biz 凭证缺失（优先）或年龄超阈值（取最老）的号
    #    刚尝试过且仍在退避期内的号跳过，防止不可搜索的号反复霸占名额饿死其他号
    try:
        from backend.accounts import _load_accounts
        from backend.account_pool import account_pool
        now = time.time()
        best = None  # (无凭证优先, 年龄, name, biz)
        for acc in _load_accounts():
            fakeid = acc.get("fakeid") or acc.get("alias")
            name = acc.get("nickname") or acc.get("name") or ""
            if not fakeid or not name:
                continue
            if now - _proactive_last_attempt.get(fakeid, 0) < PROACTIVE_RETRY_BACKOFF:
                continue
            age = account_pool.get_biz_age(fakeid)
            if age is None:
                cand = (0, float("inf"), name, fakeid)   # 从未建立会话，最优先
            elif age > PROACTIVE_RENEW_THRESHOLD:
                cand = (1, age, name, fakeid)
            else:
                continue
            # 无凭证（优先级 0）永远排在仅老化（优先级 1）之前；同级取年龄最老的
            if best is None or cand[0] < best[0] or (cand[0] == best[0] and cand[1] > best[1]):
                best = cand
        if best:
            return {"biz": best[3], "name": best[2], "reason": "proactive", "attempts": 0}
    except Exception as e:
        logger.debug("主动续期扫描失败: %s", e)

    return None


def _resolve_name_by_biz(biz: str) -> str:
    """按 fakeid 反查公众号名称（从订阅列表）。"""
    try:
        from backend.accounts import _load_accounts
        for acc in _load_accounts():
            if (acc.get("fakeid") or acc.get("alias")) == biz:
                return acc.get("nickname") or acc.get("name") or ""
    except Exception:
        pass
    return ""


def _process_refresh_target(target: dict) -> bool:
    """执行一次定向凭证续期（绕过通用冷却/合并，但遵守全局 UI 互斥）。"""
    keyword = target.get("name") or ""
    if target.get("biz"):
        _proactive_last_attempt[target["biz"]] = time.time()  # 主动续期退避计时
    logger.info("🎯 定向凭证续期: [%s] (原因: %s, 第 %d 次尝试)",
                keyword, target.get("reason", "-"), max(1, target.get("attempts", 1)))
    return trigger_pc_wechat_refresh(keyword=keyword, force=True)


def _refresh_worker_loop():
    """保活 worker 主循环（单线程串行，天然与 UI 互斥配合）：
    macOS 优先通过聚合页批量同步全部公众号凭证，不再单独逐个搜公众号；
    启动静默 WORKER_STARTUP_SILENCE 秒。"""
    logger.info("🛡️ 凭证保活 worker 已就绪（启动静默: %d 秒，主动续期阈值: %d 分钟）",
                WORKER_STARTUP_SILENCE, PROACTIVE_RENEW_THRESHOLD // 60)
    time.sleep(WORKER_STARTUP_SILENCE)

    while True:
        if not ENABLE_BACKGROUND_ACTIVE_REFRESH:
            time.sleep(60)
            continue

        current_os = platform.system()
        target = None
        try:
            target = _next_refresh_target()
        except Exception as e:
            logger.error("获取刷新目标异常: %s", e)

        if target:
            if current_os == "Darwin":
                # macOS 下统一使用聚合页批量同步所有公众号凭证，避免逐个搜一搜干扰用户
                logger.info("🎯 检测到需要刷新凭证 (目标: %s, 原因: %s)，启动聚合页批量授权流程...",
                            target.get("name", "-"), target.get("reason", "-"))
                ok = False
                try:
                    ok = run_batch_portal_flow_macos()
                except Exception as e:
                    logger.error("macOS 聚合页批量授权异常: %s", e)

                if ok:
                    # 批量刷新成功，等待下一个检查周期
                    time.sleep(IDLE_CHECK_INTERVAL)
                else:
                    # 如果未完成或未开微信，等待较长退避时间，避免频繁打扰
                    time.sleep(180)
            else:
                ok = False
                try:
                    ok = _process_refresh_target(target)
                except Exception as e:
                    logger.error("定向续期执行异常: %s", e)
                if not ok and target.get("reason") != "proactive":
                    try:
                        from backend.refresh_queue import refresh_queue
                        refresh_queue.requeue(target)
                    except Exception as e:
                        logger.debug("刷新目标回队失败: %s", e)
                time.sleep(30)
        else:
            try:
                _check_and_refresh_stale_accounts()
            except Exception as e:
                logger.error("全局兜底巡检异常: %s", e)
            time.sleep(IDLE_CHECK_INTERVAL)


def _check_and_refresh_stale_accounts():
    """全局兜底巡检：账号池中存在凭证超过 STALE_THRESHOLD 未更新的 active 账号时，
    主动触发一次 PC 微信 UI 刷新（macOS 走批量聚合页，Windows 走窗口刷新）。"""
    try:
        from backend.account_pool import account_pool
    except ImportError:
        logger.debug("无法导入 account_pool，跳过本次检测。")
        return

    now = time.time()
    accounts = account_pool._load()
    stale_found = False

    for acc in accounts:
        if acc.get("status") != "active":
            continue
        save_time = acc.get("save_time", 0)
        if save_time and (now - save_time) > STALE_THRESHOLD:
            age_minutes = int((now - save_time) / 60)
            nickname = acc.get("nickname", "未命名")
            logger.info(
                "🔍 检测到账号 [%s] 凭证已 %d 分钟未更新（阈值 %d 分钟），准备主动刷新...",
                nickname, age_minutes, STALE_THRESHOLD // 60
            )
            stale_found = True
            break

    if not stale_found:
        return

    # 触发批量刷新（macOS 下无 keyword 会自动走聚合页）
    trigger_pc_wechat_refresh()


def start_background_refresh_daemon():
    """启动后台凭证保活 worker（全局只启动一次）。"""
    global _daemon_started
    with _daemon_lock:
        if _daemon_started:
            logger.debug("保活 worker 已在运行，跳过重复启动。")
            return
        _daemon_started = True

    t = threading.Thread(target=_refresh_worker_loop, daemon=True, name="credential-refresh-worker")
    t.start()
    logger.info("🚀 凭证保活 worker 已启动（macOS 聚合页批量授权 + Windows 自动化 + 全局互斥）。")


if __name__ == "__main__":
    success = trigger_pc_wechat_refresh()
    if success:
        print("\n[Auto Refresh] 客户端指令已发送，请检查 mitm_proxy 是否捕获到最新凭证！")
    else:
        print("\n[Auto Refresh] 自动刷新触发完成或受权限限制。")
