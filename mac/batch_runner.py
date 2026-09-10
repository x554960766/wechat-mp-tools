"""macOS 微信 PC 端公众号批量“即产即销”流水线调度器 (Batch Pipeline Runner)。
支持数百个公众号列表的自动化队列处理：
1. 自动从微信搜一搜/历史卡片打开目标公众号文章；
2. 毫秒级捕获该号专属凭证 (appmsg_token, key, pass_ticket)；
3. 立即调用微信原生接口 (/mp/profile_ext) 同步该号文章列表并入库；
4. 关闭微信临时 Web 窗口，执行随机安全休眠 (2.5~4.5s)；
5. 每处理 30 个号自动深度冷却 2 分钟，防止频控。
"""
from __future__ import annotations

import os
import sys

def ensure_virtualenv():
    """检测当前是否运行在虚拟环境 venv312 中。
    如果不是，并且检测到本地存在 venv312，则自动使用 venv312 的 python 解释器重载当前脚本！
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    if getattr(sys, 'frozen', False):
        return

    if sys.platform == 'win32':
        venv_python = os.path.join(project_root, 'venv312', 'Scripts', 'python.exe')
    else:
        venv_python = os.path.join(project_root, 'venv312', 'bin', 'python')
    if os.path.exists(venv_python):
        current_exe = os.path.abspath(sys.executable)
        target_exe = os.path.abspath(venv_python)
        if current_exe != target_exe:
            env = dict(os.environ)
            old_pypath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{project_root}:{old_pypath}" if old_pypath else project_root
            args = [venv_python, "-m", "mac.batch_runner"] + sys.argv[1:]
            os.execve(venv_python, args, env)




import argparse
import logging
import random
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from mac.mac_win import (
    find_portal_window, find_main_window, activate_wechat, raise_window,
    capture_window, list_wechat_windows, MacWindow
)
from mac.scale import CoordSpace
from mac.mac_input import move_and_click, human_sleep, scroll, press, type_text_via_clipboard
from mac.mac_ocr import ocr
from mac.steps.step4_cleanup import close_native_account_windows, cleanup_mac_wechat_windows, is_portal_window

from backend.config import DATA_DIR, load_json, save_json, DOWNLOAD_HISTORY_FILE
from backend.account_pool import account_pool, AccountPool
from backend.articles import _fetch_articles_page
from backend.mitm_proxy import ProxyManager

logger = logging.getLogger("wechat_auto_mac.batch")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_sh)
logger.propagate = False


class WeChatBatchRunner:
    """批量公众号流水线同步执行引擎（聚合页 OCR 驱动，零搜索，保留聚合页并关闭原生名片）"""

    def __init__(
        self,
        articles_per_account: int = 10,
        jitter_range: tuple[float, float] = (1.5, 3.0),
        batch_rest_every: int = 30,
        batch_rest_duration: float = 120.0,
        auto_cleanup: bool = True
    ):
        self.articles_per_account = articles_per_account
        self.jitter_range = jitter_range
        self.batch_rest_every = batch_rest_every
        self.batch_rest_duration = batch_rest_duration
        self.auto_cleanup = auto_cleanup

        self.total_processed = 0
        self.success_count = 0
        self.failed_list: list[dict] = []
        self.total_articles_synced = 0

    def _save_articles_to_history(self, articles: list[dict], account_name: str, fakeid: str) -> int:
        """保存/追加抓取到的文章到本地历史数据库"""
        if not articles:
            return 0
        history = load_json(DOWNLOAD_HISTORY_FILE, [])
        existing_titles = {item.get("title") for item in history if item.get("title")}
        new_added = 0
        for art in articles:
            t = art.get("title", "")
            if t and t not in existing_titles:
                history.append({
                    "title": t,
                    "link": art.get("link", ""),
                    "cover": art.get("cover", ""),
                    "digest": art.get("digest", ""),
                    "author": art.get("author") or account_name,
                    "account_name": account_name,
                    "fakeid": fakeid,
                    "publish_time": art.get("update_time", int(time.time())),
                    "download_time": int(time.time()),
                    "status": "synced",
                })
                existing_titles.add(t)
                new_added += 1

        if new_added > 0:
            save_json(DOWNLOAD_HISTORY_FILE, history)
            logger.info("💾 [数据入库] 公众号【%s】新增 %d 篇文章至本地历史库 (总拉取: %d)", account_name, new_added, len(articles))
        return new_added

    def _ensure_portal_window(self) -> MacWindow:
        """确保屏幕上存在批量授权聚合页窗口，如果不存在则通过微信文件传输助手自动唤起。"""
        # 确保代理助手处于运行状态
        import socket
        proxy_already_open = False
        try:
            with socket.create_connection(('127.0.0.1', 5202), timeout=0.5):
                proxy_already_open = True
        except Exception:
            pass

        if not proxy_already_open:
            try:
                mgr = ProxyManager.get_instance()
                mgr.start()
            except Exception:
                pass

        activate_wechat()
        human_sleep(0.3, 0.5)

        portal = find_portal_window()
        if portal:
            raise_window(portal)
            human_sleep(0.3, 0.5)
            return portal

        main = find_main_window(timeout=3.0)
        if not main:
            raise RuntimeError("未检测到微信客户端窗口，请先打开微信 Mac 版。")

        cs = CoordSpace(main.window_id, {'x': main.x, 'y': main.y, 'w': main.w, 'h': main.h})
        img = capture_window(main)
        boxes = ocr(img)

        def _is_in_filehelper_window(w) -> bool:
            img_c = capture_window(w)
            if img_c is None:
                return False
            boxes_c = ocr(img_c)
            r_start = int(img_c.shape[1] * 0.35)
            return any("文件传输助手" in b.text and b.top < 80 and b.left > r_start for b in boxes_c)

        if not _is_in_filehelper_window(main):
            for attempt in range(1, 3):
                if attempt > 1:
                    press("escape", times=2)
                    human_sleep(0.3, 0.5)
                # 确保在聊天 tab (侧边栏绿色气泡图标位于 y+145)
                move_and_click(main.x + 35, main.y + 145)
                human_sleep(0.3, 0.5)
                img = capture_window(main)
                boxes = ocr(img)
                left_limit = int(img.shape[1] * 0.38)
                helper_box = next((b for b in boxes if b.left < left_limit and b.top > 50 and '文件传输助手' in b.text), None)
                if helper_box:
                    sx, sy = cs.img_to_screen(helper_box.center[0], helper_box.center[1])
                    move_and_click(sx, sy)
                else:
                    # 搜索框精准查找文件传输助手
                    move_and_click(main.x + 100, main.y + 25)
                    human_sleep(0.2, 0.3)
                    type_text_via_clipboard('文件传输助手', clear_first=True)
                    human_sleep(0.6, 0.9)
                    img_s = capture_window(main)
                    boxes_s = ocr(img_s)
                    cand = next((b for b in boxes_s if b.left < left_limit + 100 and '文件传输助手' in b.text and b.top > 45), None)
                    if cand:
                        cs_s = CoordSpace(main.window_id, {'x': main.x, 'y': main.y, 'w': main.w, 'h': main.h})
                        move_and_click(*cs_s.img_to_screen(*cand.center))
                    else:
                        press("down")
                        human_sleep(0.2, 0.3)
                        press("return")
                        human_sleep(0.4, 0.6)
                        move_and_click(main.x + 150, main.y + 75)
                human_sleep(0.8, 1.2)
                if _is_in_filehelper_window(main):
                    break

        def _is_portal_link(text: str) -> bool:
            t = text.lower()
            if '5200' in t and any(k in t for k in ('mp-batch', 'batch', 'portal', 'auth', '127.0.0.1')):
                return True
            if 'mp-batch-portal' in t or 'batch-portal' in t:
                return True
            return False

        img2 = capture_window(main)
        boxes2 = ocr(img2)
        link_box = next((b for b in reversed(boxes2) if b.left > 280 and _is_portal_link(b.text)), None)
        if link_box:
            logger.info("✅ 找到已有聚合页链接气泡 [%s]，直接点击打开...", link_box.text)
            sx, sy = cs.img_to_screen(link_box.center[0], link_box.center[1])
            move_and_click(sx, sy)
        else:
            # 严格遵循用户指示：未找到聚合页链接时直接在输入框发送链接，绝对不乱点其它链接！
            portal_url = "http://127.0.0.1:5200/api/auth/mp-batch-portal"
            logger.info("👉 未在聊天记录中检测到有效的聚合页链接，正在输入框发送聚合页入口链接...")
            input_x = main.x + int(main.w * 0.5)
            input_y = main.y + main.h - 60
            move_and_click(input_x, input_y)
            human_sleep(0.2, 0.3)
            type_text_via_clipboard(portal_url, clear_first=False)
            human_sleep(0.2, 0.3)
            press("return")
            human_sleep(0.8, 1.2)
            img3 = capture_window(main)
            boxes3 = ocr(img3)
            link_box = next((b for b in reversed(boxes3) if b.left > 280 and _is_portal_link(b.text)), None)
            if link_box:
                logger.info("✅ 成功识别刚发送的聚合页链接 [%s]，点击打开...", link_box.text)
                sx, sy = cs.img_to_screen(link_box.center[0], link_box.center[1])
                move_and_click(sx, sy)
            else:
                logger.info("👉 点击刚发送的最新消息气泡...")
                move_and_click(main.x + int(main.w * 0.55), main.y + main.h - 130)

        deadline = time.time() + 8.0
        portal = None
        while time.time() < deadline:
            time.sleep(0.5)
            portal = find_portal_window()
            if portal:
                break

        if not portal:
            raise RuntimeError("未能唤起批量授权聚合页窗口，请手动在微信文件传输助手中点击聚合页链接！")
        raise_window(portal)
        # 确保聚合页滚动到公众号卡片清单区域（严密判定新版内嵌模式与旧版独立弹窗模式）
        main_win_chk = find_main_window(timeout=0.2)
        is_embedded = ("(内嵌)" in (portal.title or "")) or bool(main_win_chk and portal.window_id == main_win_chk.window_id and portal.w >= 850)
        try:
            img = capture_window(portal)
            boxes = ocr(img)
            min_check_x = int(portal.w * 0.55) if is_embedded else 0
            scroll_x = portal.x + (int(portal.w * 0.78) if is_embedded else portal.w // 2)
            scroll_y = portal.y + (int(portal.h * 0.6) if is_embedded else portal.h // 2)
            if not any(k in b.text for b in boxes if b.left >= min_check_x for k in ('新京报', '主力', '浙商', '数据', '潇湘', '行星', '清单')):
                move_and_click(scroll_x, scroll_y)
                human_sleep(0.2, 0.3)
                scroll(-10)
                human_sleep(0.5, 0.8)
        except Exception:
            pass
        return portal

    def _close_embedded_profile_tab(self, portal_win: MacWindow, account_name: str = "") -> bool:
        """
        在微信 4.1.x 三栏内嵌分栏中，精准定位并点击 Tab 标题右侧的圆形关闭叉号 (ⓧ)，
        关闭当前公众号主页/文章页 Tab，优雅回到聚合页。
        """
        import cv2
        main = find_main_window(timeout=1.0)
        if not main or main.w < 850:
            return False

        cs = CoordSpace(main.window_id, {'x': main.x, 'y': main.y, 'w': main.w, 'h': main.h})
        img = capture_window(main)
        if img is None:
            return False

        pane_x0 = int(main.w * 0.5)
        header_crop = img[0:65, pane_x0:main.w]
        if header_crop.size == 0:
            return False

        click_target = None

        # 1. 优先模板匹配名字右侧的圆圈叉号图标 (ⓧ)
        icon_path = os.path.join(os.path.dirname(__file__), 'assets', 'close_tab_icon.png')
        if os.path.exists(icon_path):
            icon = cv2.imread(icon_path)
            if icon is not None and header_crop.shape[0] >= icon.shape[0] and header_crop.shape[1] >= icon.shape[1]:
                res = cv2.matchTemplate(header_crop, icon, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                if max_val >= 0.70:
                    cx = pane_x0 + max_loc[0] + icon.shape[1] // 2
                    cy = max_loc[1] + icon.shape[0] // 2
                    click_target = (cx, cy)
                    logger.info("🎯 [Close Tab] 模板匹配定位到标签页关闭图标: (%d, %d), 置信度: %.2f", cx, cy, max_val)

        # 2. OCR 查找名字右侧的关闭按钮
        if not click_target:
            boxes = ocr(header_crop)
            for b in boxes:
                if account_name and account_name in b.text:
                    cx = pane_x0 + b.right + 20
                    cy = (b.top + b.bottom) // 2
                    click_target = (cx, cy)
                    logger.info("🎯 [Close Tab] OCR 根据公众号【%s】名称定位到关闭按钮: (%d, %d)", account_name, cx, cy)
                    break
                for char in ('⑧', 'ⓧ', '×', 'x', 'X'):
                    if char in b.text:
                        cx = pane_x0 + (b.left + b.right) // 2
                        cy = (b.top + b.bottom) // 2
                        click_target = (cx, cy)
                        logger.info("🎯 [Close Tab] OCR 识别到关闭字符【%s】定位: (%d, %d)", char, cx, cy)
                        break
                if click_target:
                    break

        # 3. 如果找到了关闭按钮，执行系统级拟人点击
        if click_target:
            sx, sy = cs.img_to_screen(*click_target)
            logger.info("👉 点击公众号主页 Tab 右侧圆形关闭按钮: 屏幕坐标 (%d, %d)...", sx, sy)
            move_and_click(sx, sy)
            human_sleep(0.5, 0.8)
            return True

        return False

    def _sync_via_portal_ocr(self, portal_win: MacWindow, account_name: str, target_fakeid: str) -> bool:
        """纯 OCR 驱动：在聚合页中识别指定公众号卡片并点击触发抓包，关闭公众号主页返回聚合页。"""
        # 1. 确保聚合页置顶在最前并注入焦点
        raise_window(portal_win)
        human_sleep(0.3, 0.5)

        main_win_chk = find_main_window(timeout=0.2)
        is_embedded = ("(内嵌)" in (portal_win.title or "")) or bool(main_win_chk and portal_win.window_id == main_win_chk.window_id and portal_win.w >= 850)
        pane_min_x = int(portal_win.w * 0.55) if is_embedded else 0
        scroll_cx = portal_win.x + (int(portal_win.w * 0.78) if is_embedded else portal_win.w // 2)
        scroll_cy = portal_win.y + (int(portal_win.h * 0.55) if is_embedded else portal_win.h // 2)

        # 激活分栏内部焦点，防止 macOS click-through 吞掉首次点击手势
        move_and_click(scroll_cx, portal_win.y + 45)
        human_sleep(0.2, 0.3)

        cs = CoordSpace(portal_win.window_id, {'x': portal_win.x, 'y': portal_win.y, 'w': portal_win.w, 'h': portal_win.h})

        name_clean = account_name.replace(" ", "")
        tokens = [t for t in account_name.split() if len(t) >= 2]

        def matches_acc(txt_clean: str) -> bool:
            # 严格排除顶部控制按钮与状态横幅（防止将顶部「⏳ 正在同步：【公众号名】」误判为卡片！）
            if any(bad in txt_clean for bad in ("正在同步", "正在启动", "流水线", "一键开始", "流转授权", "授权聚合中心", "建议", "清单", "就绪状态", "仅复制", "复制全部", "会话已就绪", "点击授权")):
                return False
            if name_clean in txt_clean or txt_clean in name_clean:
                return True
            if any(t in txt_clean for t in tokens):
                return True
            if len(name_clean) >= 3 and name_clean[:3] in txt_clean:
                return True
            return False

        # 2. 多轮 OCR 扫描寻找该公众号卡片（支持列表滚动）
        target_box = None
        for attempt in range(5):
            img = capture_window(portal_win)
            boxes = ocr(img)

            for b in boxes:
                # 排除顶部导航栏与控制区（前 80px），且必须在右侧分栏区域（若内嵌）
                if b.left >= pane_min_x and b.top > 80:
                    txt = b.text.replace(" ", "")
                    if matches_acc(txt):
                        target_box = b
                        break

            if target_box:
                break

            # 若未在当前可视区域发现，在列表区域向下滑动使下方卡片可见
            if attempt < 4:
                move_and_click(scroll_cx, scroll_cy)
                human_sleep(0.2, 0.3)
                scroll(-10)
                human_sleep(0.6, 0.9)

        if not target_box:
            # 若向下滑动未找到，尝试滚回顶部再扫描一次（防止前面处理项滚到底部）
            move_and_click(scroll_cx, scroll_cy)
            scroll(30)
            human_sleep(0.6, 0.9)
            img = capture_window(portal_win)
            boxes = ocr(img)
            for b in boxes:
                if b.left >= pane_min_x and b.top > 80:
                    txt = b.text.replace(" ", "")
                    if matches_acc(txt):
                        target_box = b
                        break

        if not target_box:
            logger.warning("❌ [Portal OCR] 未在聚合页中找到公众号【%s】的卡片", account_name)
            return False

        logger.info("✅ [Portal OCR] 定位到公众号【%s】卡片: [%s] @ (%d, %d)", account_name, target_box.text, target_box.left, target_box.top)

        # 3. 点击卡片触发原生主页与抓包
        sx, sy = cs.img_to_screen(target_box.center[0], target_box.center[1])
        logger.info("👉 点击公众号【%s】卡片，屏幕坐标 (%d, %d)...", account_name, sx, sy)

        old_updated_at = 0
        if target_fakeid:
            acc = account_pool.acquire()
            cred = AccountPool.get_biz_credential(acc, target_fakeid) if acc else {}
            old_updated_at = cred.get('updated_at', 0)

        move_and_click(sx, sy)

        # 4. 轮询等待 MITM 抓包截获凭据（若 2.5s 未捕获，自动补充点击一次确保激活）
        deadline = time.time() + 6.0
        captured = False
        reclicked = False
        while time.time() < deadline:
            time.sleep(0.3)
            # 检查凭据库是否已更新或已持有近期（15分钟内）新鲜有效凭证
            if target_fakeid:
                acc = account_pool.acquire()
                cred = AccountPool.get_biz_credential(acc, target_fakeid) if acc else {}
                up_time = cred.get('updated_at', 0)
                if cred.get('key') and (up_time > old_updated_at or (time.time() - up_time < 900)):
                    captured = True
                    logger.info("🎉 成功确认公众号【%s】有效凭证就绪 (更新于 %d 秒前)!", account_name, int(time.time() - up_time))
                    break
            if not reclicked and time.time() > (deadline - 3.5):
                logger.info("补充点击以确保手势触发: (%d, %d)...", sx, sy)
                move_and_click(sx, sy)
                reclicked = True

        # 5. 打开公众号主页后，点击名字右侧的圆圈叉号 (ⓧ) 关闭当前主页，无缝回到聚合页
        human_sleep(0.5, 0.8)
        if is_embedded:
            closed = self._close_embedded_profile_tab(portal_win, account_name=account_name)
            if not closed:
                logger.warning("未能自动匹配到标签页关闭按钮，尝试安全兜底...")
                cs_main = CoordSpace(portal_win.window_id, {'x': portal_win.x, 'y': portal_win.y, 'w': portal_win.w, 'h': portal_win.h})
                fallback_x, fallback_y = cs_main.img_to_screen(min(portal_win.w - 120, 820), 28)
                move_and_click(fallback_x, fallback_y)
                human_sleep(0.5, 0.8)

            # 验证聚合页是否仍在屏幕上；若分栏被收起，通过聊天中气泡秒级重新唤起聚合页
            img_chk = capture_window(portal_win)
            boxes_chk = ocr(img_chk) if img_chk is not None else []
            portal_keys = ('公众号批量授', '批量授', '授权', '清单', 'mp-batch', '5200', '已就绪')
            has_portal_still = any(any(k in b.text for k in portal_keys) for b in boxes_chk if b.left >= pane_min_x)
            if not has_portal_still:
                # 寻找并点击聊天区域中的聚合页链接气泡
                link_box = next((b for b in reversed(boxes_chk) if b.left > 280 and any(k in b.text for k in ('5200', 'mp-batch', 'portal'))), None)
                if link_box:
                    lx, ly = cs.img_to_screen(*link_box.center)
                    move_and_click(lx, ly)
                    human_sleep(1.2, 1.8)
        else:
            # 独立弹窗模式：关闭弹出的原生公众号名片窗口 (Cmd+W 或 AXCloseButton)
            close_native_account_windows()
            # 若聚合页被新标签覆盖，按 Cmd+W 返回
            try:
                img_check = capture_window(portal_win)
                boxes_check = ocr(img_check)
                if not any('公众号批量授权' in b.text for b in boxes_check[:5]):
                    press('command', 'w')
                    human_sleep(0.3, 0.5)
            except Exception:
                pass

        return captured

    def sync_single_account(self, account_name: str, fakeid: Optional[str] = None) -> dict:
        """从聚合页逐个打开公众号主页 -> 截获凭据 -> 立即关闭原生窗口 -> 同步文章入库"""
        t0 = time.time()
        logger.info("👉 [Batch] 开始处理公众号: 【%s】 (指定 fakeid: %s)", account_name, fakeid or "待捕获")
        
        target_fakeid = fakeid
        if not target_fakeid:
            try:
                from backend.accounts import _load_accounts
                for a in _load_accounts():
                    if (a.get("nickname") or a.get("name")) == account_name:
                        target_fakeid = a.get("fakeid") or a.get("alias")
                        break
            except Exception:
                pass

        # ⚡ 纯聚合页流转流程：确保聚合页在最前，逐个点击打开并同步
        portal_win = self._ensure_portal_window()
        ocr_ok = self._sync_via_portal_ocr(portal_win, account_name, target_fakeid)

        # 再次尝试提取最新捕获的 fakeid (biz) 与凭证
        if not target_fakeid:
            acc = account_pool.acquire()
            if acc and acc.get("biz_tokens"):
                target_fakeid = list(acc["biz_tokens"].keys())[-1]

        if not target_fakeid:
            raise RuntimeError(f"未能获取到公众号【{account_name}】的 fakeid (__biz)")

        if not ocr_ok:
            raise RuntimeError(f"未能通过聚合页 OCR 激活公众号【{account_name}】并捕获有效凭证")

        # 立即调用 API 拉取历史文章
        articles, total_count, can_continue = _fetch_articles_page(
            fakeid=target_fakeid,
            begin=0,
            count=self.articles_per_account,
            keyword="",
            account_name=account_name
        )

        if articles:
            self._save_articles_to_history(articles, account_name, target_fakeid)

        elapsed = time.time() - t0
        logger.info("✅ [Batch] 公众号【%s】同步成功! 耗时: %.2fs, 文章数: %d", account_name, elapsed, len(articles))

        return {
            "success": True,
            "account_name": account_name,
            "fakeid": target_fakeid,
            "articles_count": len(articles),
            "total_count": total_count,
            "elapsed": elapsed
        }

    def run_queue(
        self,
        account_list: list[str | dict],
        progress_callback: Optional[Callable[[int, int, str, bool, str], None]] = None
    ) -> dict:
        """执行整个批量队列"""
        total_accounts = len(account_list)
        logger.info("🚀 [Batch Runner] 启动批量流水线同步任务，总待处理目标数: %d", total_accounts)

        t_task_start = time.time()

        for idx, item in enumerate(account_list, start=1):
            if isinstance(item, dict):
                acc_name = item.get("name") or item.get("nickname") or item.get("keyword", "")
                fakeid = item.get("fakeid") or item.get("biz")
            else:
                acc_name = str(item).strip()
                fakeid = None

            if not acc_name:
                continue

            logger.info("═══════════════════════════════════════════════════")
            logger.info("⏳ 进度 [%d/%d] 正在处理: 【%s】", idx, total_accounts, acc_name)
            logger.info("═══════════════════════════════════════════════════")

            success = False
            err_msg = ""
            articles_count = 0

            try:
                res = self.sync_single_account(acc_name, fakeid)
                success = True
                articles_count = res.get("articles_count", 0)
                self.success_count += 1
                self.total_articles_synced += articles_count
            except Exception as e:
                err_msg = str(e)
                logger.error("❌ [%d/%d] 公众号【%s】同步失败: %s", idx, total_accounts, acc_name, err_msg)
                self.failed_list.append({
                    "index": idx,
                    "account_name": acc_name,
                    "fakeid": fakeid,
                    "error": err_msg
                })
                # 出现异常时强制清理多余窗口
                if self.auto_cleanup:
                    try:
                        cleanup_mac_wechat_windows()
                    except Exception:
                        pass
            finally:
                self.total_processed += 1
                try:
                    close_native_account_windows()
                except Exception:
                    pass
                if progress_callback:
                    try:
                        progress_callback(idx, total_accounts, acc_name, success, err_msg)
                    except Exception:
                        pass

            # 若还有后续任务，执行安全避让休眠
            if idx < total_accounts:
                # 检查是否达到批次深度休息阈值
                if idx % self.batch_rest_every == 0:
                    logger.info("☕ [批次冷却] 已连续处理 %d 个公众号，深度休息 %.0f 秒以重置微信频控计数器...", idx, self.batch_rest_duration)
                    time.sleep(self.batch_rest_duration)
                else:
                    sleep_sec = random.uniform(*self.jitter_range)
                    logger.info("😴 [随机避让] 安全休眠 %.2f 秒...", sleep_sec)
                    time.sleep(sleep_sec)

        total_elapsed = time.time() - t_task_start
        logger.info("🎉 [Batch Runner] 批量任务全部完成!")
        logger.info("📊 总处理: %d | 成功: %d | 失败: %d | 同步文章总数: %d | 总耗时: %.2fs",
                    self.total_processed, self.success_count, len(self.failed_list),
                    self.total_articles_synced, total_elapsed)

        return {
            "total": self.total_processed,
            "success": self.success_count,
            "failed_count": len(self.failed_list),
            "failed_list": self.failed_list,
            "total_articles_synced": self.total_articles_synced,
            "total_elapsed_seconds": total_elapsed
        }


def run_batch_pipeline(
    accounts: list[str | dict],
    articles_per_account: int = 10,
    batch_rest_every: int = 30
) -> dict:
    """批量流水线快捷入口"""
    runner = WeChatBatchRunner(
        articles_per_account=articles_per_account,
        batch_rest_every=batch_rest_every
    )
    return runner.run_queue(accounts)
