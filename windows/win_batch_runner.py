"""Windows 微信 PC 端公众号批量“聚合页”流转执行引擎 (WinBatchRunner)。
与 macOS 端 mac/batch_runner.py 体验完全对齐：
1. 自动定位/唤起微信内置浏览器中的「公众号批量授权聚合中心」；
2. 保持聚合页窗口永久常驻与安全保护；
3. 纯 Win32 / UIA 驱动逐个定位并点击聚合页中的公众号卡片（后台 PostMessage 投递，不抢用户鼠标焦点）；
4. 毫秒级捕获该号专属凭据 (key, pass_ticket, appmsg_token)；
5. Win32 WM_CLOSE 异步秒级关闭弹出的原生公众号名片窗口；
6. 立即拉取最新文章入库，安全休眠后流转下一个公众号。
"""
from __future__ import annotations

import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Callable, Optional

# 确保项目根目录在 sys.path 中
_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

logger = logging.getLogger("wechat_auto_windows.batch")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_sh)
logger.propagate = False

from backend.config import DATA_DIR, load_json, save_json, DOWNLOAD_HISTORY_FILE
from backend.account_pool import account_pool, AccountPool
from backend.articles import _fetch_articles_page
from backend.mitm_proxy import ProxyManager


class WinBatchRunner:
    """Windows 批量公众号流水线执行引擎（聚合页驱动，零搜索，保留聚合页并关闭原生名片）"""

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
        now_ts = int(time.time())

        for art in articles:
            title = art.get("title", "")
            if not title or title in existing_titles:
                continue

            entry = {
                "title": title,
                "url": art.get("link") or art.get("url", ""),
                "publish_time": art.get("create_time") or art.get("publish_time") or now_ts,
                "account_name": account_name,
                "fakeid": fakeid,
                "digest": art.get("digest", ""),
                "cover": art.get("cover", ""),
                "synced_at": now_ts,
            }
            history.insert(0, entry)
            existing_titles.add(title)
            new_added += 1

        if new_added > 0:
            save_json(DOWNLOAD_HISTORY_FILE, history)
            logger.info("💾 [数据入库] 公众号【%s】新增 %d 篇文章至本地历史库 (总拉取: %d)",
                        account_name, new_added, len(articles))
        else:
            logger.info("ℹ️ [数据入库] 公众号【%s】本次拉取的 %d 篇文章均已在库中", account_name, len(articles))
        return new_added

    def _ensure_portal_window(self):
        """确保聚合页窗口在屏幕上已打开并置顶"""
        from windows import win_window as ww
        from windows import win_input as wi

        # 1. 确保 MITM 代理服务已启动
        try:
            mgr = ProxyManager.get_instance()
            if not mgr.running:
                mgr.start()
        except Exception:
            pass

        # 2. 查找已打开的聚合页窗口
        portal = ww.find_portal_window_windows()
        if portal:
            ww.activate_window(portal.hwnd)
            wi.human_sleep(0.3, 0.5)
            return portal

        # 3. 若未打开，切换到文件传输助手并在输入框发送聚合页链接
        main = ww.find_main_window()
        if not main:
            ww.launch_wechat_windows()
            main = ww.find_main_window()
        if not main:
            raise RuntimeError("未检测到微信 PC 客户端主窗口，请先启动并登录微信！")

        portal_url = "http://127.0.0.1:5200/api/auth/mp-batch-portal"
        ww.activate_window(main.hwnd)
        wi.human_sleep(0.3, 0.5)

        import re

        def _is_in_filehelper(hwnd: int) -> bool:
            try:
                import uiautomation as auto
                ctrl = auto.ControlFromHandle(hwnd)
                if not ctrl:
                    return False
                for c, _ in auto.WalkControl(ctrl, maxDepth=6):
                    name = (c.Name or "").strip().replace(" ", "")
                    r = c.BoundingRectangle
                    # 顶部标题栏特征：y 坐标较小，名称纯粹为文件传输助手
                    if (name in ("文件传输助手", "文件传输助手(FileTransfer)") or (name.startswith("文件传输助手") and len(name) <= 8)) and r.top < 120:
                        if ":" not in name and "：" not in name and "记录" not in name:
                            return True
            except Exception:
                pass
            return False

        def _enter_filehelper_window() -> bool:
            if _is_in_filehelper(main.hwnd):
                logger.info("🎉 当前已停留在 Windows 微信「文件传输助手」会话界面！")
                return True

            for attempt in range(1, 3):
                logger.info("👉 正在定位并打开 Windows 微信「文件传输助手」会话 (第 %d/2 次尝试)...", attempt)
                if attempt > 1:
                    wi.post_escape(main.hwnd)
                    time.sleep(0.1)
                    wi.post_escape(main.hwnd)
                    wi.human_sleep(0.3, 0.5)

                # 1. 优先通过 UIA 检查左侧会话列表中是否直接可见纯粹的「文件传输助手」
                filehelper_item = None
                try:
                    import uiautomation as auto
                    ctrl = auto.ControlFromHandle(main.hwnd)
                    if ctrl:
                        for c, _ in auto.WalkControl(ctrl, maxDepth=8):
                            name = (c.Name or "").strip().replace(" ", "")
                            r = c.BoundingRectangle
                            # 左侧会话栏：排除聊天内容，只匹配纯粹的文件传输助手
                            if r.right > r.left and r.bottom > r.top:
                                if ("文件传" in name or "传输助手" in name) and len(name) <= 8 and ":" not in name and "：" not in name and "记录" not in name:
                                    filehelper_item = c
                                    break
                except Exception:
                    pass

                if filehelper_item:
                    r = filehelper_item.BoundingRectangle
                    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                    logger.info("✅ 直接在会话列表中定位到「文件传输助手」，点击坐标 (%d, %d)...", cx, cy)
                    wi.post_click(main.hwnd, cx, cy)
                    wi.human_sleep(0.8, 1.2)
                else:
                    # 2. 搜索框精准查找：输入「文件传输助手」，confirm=False 绝不直接回车！
                    logger.info("🔍 正在通过搜索框查找「文件传输助手」...")
                    wi.focus_search_and_type(main.hwnd, "文件传输助手", confirm=False)
                    wi.human_sleep(0.6, 0.9)

                    # 3. 在搜索结果列表中精准匹配「功能」分类下的「文件传输助手」
                    target_cand = None
                    try:
                        import uiautomation as auto
                        # 查找范围包括主窗口以及属于微信进程的所有顶级窗口（兼容独立搜索下拉浮层窗口）
                        wechat_hwnds = [main.hwnd]
                        for w in ww.find_wechat_windows():
                            if w.hwnd not in wechat_hwnds:
                                wechat_hwnds.append(w.hwnd)

                        func_header_top = None
                        all_cands = []
                        for h in wechat_hwnds:
                            c_root = auto.ControlFromHandle(h)
                            if not c_root:
                                continue
                            for c, _ in auto.WalkControl(c_root, maxDepth=10):
                                txt = (c.Name or "").strip().replace(" ", "")
                                r = c.BoundingRectangle
                                if r.right <= r.left or r.bottom <= r.top:
                                    continue
                                # 检测「功能」分类标题
                                if (txt == "功能" or (len(txt) <= 4 and "功能" in txt and "助手" not in txt)) and func_header_top is None:
                                    func_header_top = r.top
                                # 严格清洗候选条目：排除聊天记录、冒号、长度过长的项
                                if ":" in txt or "：" in txt or any(k in txt for k in ("记录", "相关的", "条相关", "昨天", "今天", "撤回", "图片", "视频", "语音")):
                                    continue
                                clean_txt = re.sub(r'^[^\u4e00-\u9fa5]+', '', txt)
                                if "文件传输助手" in clean_txt and len(clean_txt) <= 8:
                                    all_cands.append((c, r, clean_txt))

                        if all_cands:
                            # 优先匹配在「功能」标题下方的第一项
                            if func_header_top is not None:
                                below_func = [item for item in all_cands if item[1].top > func_header_top]
                                if below_func:
                                    below_func.sort(key=lambda x: x[1].top)
                                    target_cand = below_func[0]
                                    logger.info("🎯 成功在「功能」标题下方匹配到文件传输助手: %s", target_cand[2])

                            if target_cand is None:
                                # 次优先：完全等于「文件传输助手」的纯净项
                                strict = [item for item in all_cands if item[2] == "文件传输助手"]
                                if strict:
                                    strict.sort(key=lambda x: x[1].top)
                                    target_cand = strict[0]
                                    logger.info("🎯 精确定位到纯净「文件传输助手」条目: %s", target_cand[2])
                                else:
                                    all_cands.sort(key=lambda x: x[1].top)
                                    target_cand = all_cands[0]
                    except Exception as e_find:
                        logger.debug("UIA 搜索条目定位异常: %s", e_find)

                    if target_cand:
                        r = target_cand[1]
                        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                        logger.info("✅ 精确定位到「功能 -> 文件传输助手」，点击屏幕坐标 (%d, %d)", cx, cy)
                        wi.post_click(main.hwnd, cx, cy)
                        wi.human_sleep(0.8, 1.2)
                    else:
                        # 降级多重保障：真实系统级方向下键 + 回车确认，同时辅以几何物理坐标点击
                        logger.info("👉 UIA 未直接捕获独立浮层条目，使用系统级按键选择搜索首项功能...")
                        wi.send_single_key(wi.VK_DOWN)
                        wi.human_sleep(0.2, 0.3)
                        wi.send_single_key(wi.VK_RETURN)
                        wi.human_sleep(0.6, 0.8)

                        # 若仍未进入，直接点击搜索框正下方第一项功能条目物理区域 (通常在 left+120, top+75)
                        if not _is_in_filehelper(main.hwnd):
                            l, t, r, b = main.rect
                            fallback_cand_x = l + 120
                            fallback_cand_y = t + 75
                            logger.info("👉 尝试直接点击搜索下拉浮层首项坐标 (%d, %d)...", fallback_cand_x, fallback_cand_y)
                            wi.post_click(main.hwnd, fallback_cand_x, fallback_cand_y)
                            wi.human_sleep(0.8, 1.2)

                # 4. 验证是否成功显示「文件传输助手」聊天页面
                if _is_in_filehelper(main.hwnd):
                    logger.info("🎉 成功准确选中并显示 Windows 微信「文件传输助手」聊天页面！")
                    return True
                else:
                    logger.warning("⚠️ 安全校验未通过：当前窗口非「文件传输助手」，按 Escape 退出以防误操作...")
                    wi.send_single_key(wi.VK_ESCAPE)
                    time.sleep(0.15)
                    wi.send_single_key(wi.VK_ESCAPE)
                    wi.human_sleep(0.4, 0.6)

            return _is_in_filehelper(main.hwnd)

        if not _enter_filehelper_window():
            logger.warning("未能自动进入「文件传输助手」页面，请手动在微信中打开文件传输助手。")

        # 检查会话内是否已有聚合页链接气泡，若没有则在输入框发送（绝不误触其它无关链接）
        has_portal_link = False
        link_click_pt = None
        try:
            import uiautomation as auto
            ctrl = auto.ControlFromHandle(main.hwnd)
            if ctrl:
                for c, _ in auto.WalkControl(ctrl, maxDepth=10):
                    name = c.Name or ""
                    if "5200" in name and any(k in name for k in ("mp-batch", "batch", "portal", "auth", "127.0.0.1")):
                        r = c.BoundingRectangle
                        if r.right > r.left and r.bottom > r.top:
                            link_click_pt = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                            has_portal_link = True
                            break
        except Exception:
            pass

        if has_portal_link and link_click_pt:
            logger.info("✅ 检测到已有聚合页链接气泡，点击坐标: %s...", link_click_pt)
            wi.post_click(main.hwnd, link_click_pt[0], link_click_pt[1])
        else:
            logger.info("👉 未在聊天记录中检测到有效的 5200 聚合页链接，正在输入框发送聚合页入口链接...")
            # 点击聊天窗口下方的输入区域 (main 窗口底部约 80px 处)
            l, t, r, b = main.rect
            input_x = l + (r - l) // 2
            input_y = b - 70
            wi.post_click(main.hwnd, input_x, input_y)
            wi.human_sleep(0.2, 0.3)
            wi.type_via_clipboard(main.hwnd, portal_url, confirm=True)
            wi.human_sleep(1.0, 1.5)

            # 再次通过 UIA 点击刚发送的链接气泡，或点击输入框上方刚发出的消息
            sent_clicked = False
            try:
                import uiautomation as auto
                ctrl = auto.ControlFromHandle(main.hwnd)
                if ctrl:
                    for c, _ in auto.WalkControl(ctrl, maxDepth=10):
                        name = c.Name or ""
                        if "5200" in name and any(k in name for k in ("mp-batch", "batch", "portal", "auth", "127.0.0.1")):
                            r = c.BoundingRectangle
                            if r.right > r.left and r.bottom > r.top:
                                wi.post_click(main.hwnd, (r.left + r.right) // 2, (r.top + r.bottom) // 2)
                                sent_clicked = True
                                break
            except Exception:
                pass
            if not sent_clicked:
                wi.post_click(main.hwnd, input_x, b - 150)

        wi.human_sleep(1.5, 2.5)

        portal = ww.find_portal_window_windows()
        if not portal:
            raise RuntimeError(
                f"未能自动唤起批量授权聚合页窗口。请在微信文件传输助手中手动点击链接: {portal_url}"
            )
        ww.activate_window(portal.hwnd)
        return portal

    def _sync_via_portal(self, portal_win, account_name: str, target_fakeid: str, card_index: int) -> bool:
        """在 Windows 微信聚合页中定位指定公众号卡片并点击触发抓包，关闭当前主页Tab/名片，保留聚合页。"""
        from windows import win_window as ww
        from windows import win_input as wi

        ww.activate_window(portal_win.hwnd)
        wi.human_sleep(0.3, 0.5)

        is_embedded = ("(内嵌)" in (portal_win.title or "")) or (portal_win.width >= 1000)

        # 1. 定位卡片中心坐标 (优先通过 UIA，若不可用则通过精确网格几何推算)
        click_point = None
        try:
            import uiautomation as auto
            ctrl = auto.ControlFromHandle(portal_win.hwnd)
            if ctrl:
                tokens = [t for t in account_name.split() if len(t) >= 2]
                for c, _ in auto.WalkControl(ctrl, maxDepth=10):
                    name = c.Name or ""
                    if name and (account_name in name or any(t in name for t in tokens)):
                        r = c.BoundingRectangle
                        if r.right > r.left and r.bottom > r.top:
                            click_point = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                            logger.info("✅ [UIA 命中] 定位到公众号【%s】卡片控件 @ %s", account_name, click_point)
                            break
        except Exception:
            pass

        # 几何网格兜底：聚合页列表为居中卡片布局
        if click_point is None:
            left, top, right, bottom = portal_win.rect
            cx = left + (int(portal_win.width * 0.78) if is_embedded else portal_win.width // 2)
            # 聚合页标题+说明栏约高 340px，每张卡片高度约 70px
            cy = top + 350 + (card_index * 72)
            click_point = (cx, cy)
            logger.info("📐 [几何定位] 使用卡片网格比例推算公众号【%s】[#%d] 坐标: %s",
                        account_name, card_index, click_point)

        # 2. 记录旧凭证时间戳
        old_updated_at = 0
        if target_fakeid:
            acc = account_pool.acquire()
            cred = AccountPool.get_biz_credential(acc, target_fakeid) if acc else {}
            old_updated_at = cred.get("updated_at", 0)

        # 3. 模拟后台点击卡片
        logger.info("👉 点击公众号【%s】卡片，坐标: %s...", account_name, click_point)
        wi.post_click(portal_win.hwnd, click_point[0], click_point[1])

        # 4. 轮询等待 MITM 抓包截获凭据
        deadline = time.time() + 6.0
        captured = False
        reclicked = False

        while time.time() < deadline:
            time.sleep(0.3)
            # 检查凭据是否已被代理拦截入库（支持新鲜度验证）
            if target_fakeid:
                acc = account_pool.acquire()
                cred = AccountPool.get_biz_credential(acc, target_fakeid) if acc else {}
                up_time = cred.get("updated_at", 0)
                if cred.get("key") and (up_time > old_updated_at or (time.time() - up_time < 900)):
                    captured = True
                    logger.info("🎉 成功确认公众号【%s】有效凭证就绪 (更新于 %d 秒前)!", account_name, int(time.time() - up_time))
                    break

            if not reclicked and time.time() > (deadline - 3.5):
                logger.info("补充点击以确保手势触发: %s...", click_point)
                wi.post_click(portal_win.hwnd, click_point[0], click_point[1])
                reclicked = True

        # 5. 关闭打开的公众号主页：新版微信内嵌分栏按 Ctrl+W 关闭 Tab；旧版微信关闭弹出的独立原生名片
        wi.human_sleep(0.4, 0.7)
        if is_embedded:
            # 向当前分栏发送 Ctrl+W 原生快捷键关闭当前 Tab
            logger.info("👉 [内嵌模式] 向分栏投递 Ctrl+W 关闭公众号主页 Tab，回到聚合页...")
            wi.post_key(portal_win.hwnd, wi.VK_KEY_W, with_ctrl=True)
            wi.human_sleep(0.5, 0.8)
        else:
            # 独立窗口模式：关闭弹出的原生公众号名片窗口
            ww.close_native_account_windows_windows()

        return captured

    def sync_single_account(self, account_name: str, fakeid: Optional[str] = None, card_index: int = 0) -> dict:
        """从聚合页打开公众号主页 -> 截获凭据 -> 立即关闭原生窗口 -> 同步文章入库"""
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

        # ⚡ 纯聚合页流转流程：确保聚合页在最前，点击打开并同步
        portal_win = self._ensure_portal_window()
        ocr_ok = self._sync_via_portal(portal_win, account_name, target_fakeid, card_index)

        # 再次尝试提取最新捕获的 fakeid (biz) 与凭证
        if not target_fakeid:
            acc = account_pool.acquire()
            if acc and acc.get("biz_tokens"):
                target_fakeid = list(acc["biz_tokens"].keys())[-1]

        if not target_fakeid:
            raise RuntimeError(f"未能获取到公众号【{account_name}】的 fakeid (__biz)")

        if not ocr_ok:
            acc = account_pool.acquire()
            cred = AccountPool.get_biz_credential(acc, target_fakeid) if acc else {}
            if not cred.get("key"):
                raise RuntimeError(f"未能通过聚合页激活公众号【{account_name}】并捕获有效凭证")

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
        logger.info("🚀 [Batch Runner] 启动 Windows 批量流水线同步任务，总待处理目标数: %d", total_accounts)

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
                res = self.sync_single_account(acc_name, fakeid, card_index=idx - 1)
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
            finally:
                self.total_processed += 1
                try:
                    from windows import win_window as ww
                    ww.close_native_account_windows_windows()
                except Exception:
                    pass
                if progress_callback:
                    try:
                        progress_callback(idx, total_accounts, acc_name, success, err_msg)
                    except Exception:
                        pass

            # 若还有后续任务，执行安全避让休眠
            if idx < total_accounts:
                if idx % self.batch_rest_every == 0:
                    logger.info("☕ [批次冷却] 已连续处理 %d 个公众号，深度休息 %.0f 秒以重置微信频控计数器...",
                                idx, self.batch_rest_duration)
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
