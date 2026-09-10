"""Windows 微信 PC 客户端凭证刷新流水线 (与 mac/main_mac.py 对等)。

三级策略：
  A. 已有文章 webview 窗口 → PostMessage F5 刷新，让微信重新签发 key/pass_ticket；
  B. 无文章窗口 → 打开主窗口『订阅号消息』→ 点击第一篇文章卡片 → 新文章窗口加载；
  C. 以上均未捕获新凭证 → 由调用方降级为旧的 uiautomation SendKeys 方案。

所有新捕获凭证最终由本地 mitm_proxy 静默截获并写入账号池，本模块只负责
"让微信客户端产生一次带凭证的请求"，并通过轮询账号池 save_time 确认捕获成功。

参考 Access_wechat_article：坐标由 UIA 提供（可选），点击用 PostMessage 合成消息。
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

# 保证项目根目录在 sys.path 中（独立运行或被 daemon 线程调用时均可导入 backend）
_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from windows import win_window as ww
from windows import win_input as wi

logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s][%(name)s] %(message)s")
logger = logging.getLogger("wechat_auto_windows")


# ── 凭证捕获轮询（与 mac/steps/step3_open_article.py 保持一致的行为） ──

def get_pool_max_save_time() -> float:
    """读取账号池中所有 active 账号的最新 save_time，读取失败返回 0。"""
    try:
        from backend.account_pool import account_pool
        accs = account_pool._load()
        return max((a.get("save_time", 0) for a in accs if a.get("status") == "active"), default=0)
    except Exception as e:
        logger.debug("读取账号池失败: %s", e)
        return 0


def wait_for_credential_update(before_ts: float, timeout: float = 25.0, poll_interval: float = 1.0) -> bool:
    """轮询账号池，确认 mitm_proxy 已捕获到比 before_ts 更新的凭证。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            from backend.account_pool import account_pool
            accs = account_pool._load()
            for a in accs:
                if a.get("status") == "active" and a.get("save_time", 0) > before_ts:
                    logger.info("🎉 [MITM 凭证已捕获] 账号 [%s] 凭证已成功更新！", a.get("nickname", "未命名"))
                    return True
        except Exception:
            pass
        time.sleep(poll_interval)
    return False


# ── 可选 UIA 元素定位（uiautomation 未安装时自动降级为比例坐标启发式） ──

def _try_uia_find_and_get_center(hwnd: int, name_keywords: tuple, max_depth: int = 6):
    """用 uiautomation 在指定窗口内浅层搜索名称匹配的控件，返回其屏幕中心坐标。
    找不到或库不可用时返回 None（搜索限制深度防止 UIA 深遍历卡死）。"""
    try:
        import uiautomation as auto
    except ImportError:
        return None
    try:
        ctrl = auto.ControlFromHandle(hwnd)
        if not ctrl:
            return None
        target = None
        for c, depth in auto.WalkControl(ctrl, includeTop=False, maxDepth=max_depth):
            name = c.Name or ""
            if name and any(k in name for k in name_keywords):
                target = c
                break
        if target is None:
            return None
        rect = target.BoundingRectangle
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    except Exception as e:
        logger.debug("UIA 定位控件异常: %s", e)
        return None


def _fallback_point_in_window(win: ww.WinWindow, x_ratio: float, y_ratio: float):
    """比例坐标兜底：窗口内按比例取点（类似 mac 端视觉锚点思路的简化版）。"""
    left, top, right, bottom = win.rect
    x = int(left + (right - left) * x_ratio)
    y = int(top + (bottom - top) * y_ratio)
    return (x, y)


# ── Step 1: 确保微信运行就绪 ─────────────────────────

def ensure_wechat_ready(wait_seconds: float = 12.0) -> ww.WinWindow | None:
    """确保微信已启动且主窗口可用；未运行时尝试拉起。返回主窗口，失败返回 None。"""
    main_win = ww.find_main_window()
    if main_win:
        return main_win
    logger.info("未检测到微信主窗口，尝试启动微信...")
    if ww.launch_wechat_windows(wait_seconds=wait_seconds):
        main_win = ww.find_main_window()
        if main_win:
            logger.info("✅ 微信主窗口已就绪: %s", main_win)
            return main_win
    logger.warning("⚠️ 微信主窗口不可用（可能未登录或被最小化到托盘）。")
    return None


# ── 策略 A: 刷新已有文章窗口 ─────────────────────────

def refresh_existing_article_window(baseline_save_time: float, timeout: float = 18.0) -> bool:
    """找到已打开的文章 webview 窗口，PostMessage F5 刷新并等待凭证捕获。
    文章页刷新时微信会重新携带最新 key/pass_ticket 请求 /s/ 页面，由 mitm 捕获。"""
    article_wins = ww.find_article_windows()
    if not article_wins:
        logger.info("当前无已打开的文章窗口，跳过策略 A。")
        return False

    target = article_wins[0]  # find_article_windows 已按面积降序，取最大（最可能是完整文章页）
    logger.info("📄 [策略A] 找到文章窗口: %s，发送 F5 刷新...", target)
    ww.activate_window(target.hwnd)
    wi.human_sleep(0.4, 0.7)
    if not wi.post_refresh(target.hwnd):
        logger.warning("⚠️ F5 消息投递失败。")
        return False
    if wait_for_credential_update(baseline_save_time, timeout=timeout):
        logger.info("✅ [策略A] 文章窗口刷新成功触发新凭证捕获。")
        return True
    logger.info("策略 A 未捕获到新凭证，尝试策略 B。")
    return False


# ── 策略 B: 打开订阅号消息并点击文章 ─────────────────

def _open_subscription_window(main_win: ww.WinWindow, baseline_hwnds: set, wait_seconds: float = 10.0):
    """打开『订阅号消息』窗口。优先复用已打开的窗口，否则在主窗口侧栏点击入口。
    返回订阅号消息窗口（WinWindow），失败返回 None。"""
    existing = [w for w in ww.find_webview_windows() if "订阅号" in w.title]
    if existing:
        logger.info("♻️ 复用已打开的订阅号消息窗口: %s", existing[0])
        return existing[0]

    logger.info("📬 正在主窗口定位『订阅号消息』入口...")
    point = _try_uia_find_and_get_center(main_win.hwnd, ("订阅号",))
    if point is None:
        point = _fallback_point_in_window(main_win, 0.05, 0.18)  # 左侧栏中部启发式坐标
        logger.info("UIA 未定位到入口，使用比例坐标兜底: %s", point)
    ww.activate_window(main_win.hwnd)
    wi.human_sleep(0.3, 0.6)
    if not wi.post_click(main_win.hwnd, point[0], point[1]):
        return None

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        subs = [w for w in ww.find_webview_windows() if "订阅号" in w.title and w.hwnd not in baseline_hwnds]
        if subs:
            wi.human_sleep(0.8, 1.2)  # 等列表首屏渲染
            return subs[0]
        time.sleep(0.8)
    logger.warning("⚠️ 订阅号消息窗口未出现。")
    return None


def _click_first_article_in_subscriptions(sub_win: ww.WinWindow, baseline_hwnds: set,
                                          wait_seconds: float = 12.0):
    """在订阅号消息窗口中点击第一篇文章卡片，返回新出现的文章窗口。"""
    point = _try_uia_find_and_get_center(sub_win.hwnd, ("阅读", "更新", "篇原创", "小时前"))
    if point is None:
        # 列表首屏：第一张文章卡片大约位于窗口顶部 22%~30% 处（标题栏+入口卡片之下）
        point = _fallback_point_in_window(sub_win, 0.5, 0.28)
        logger.info("UIA 未定位到文章卡片，使用比例坐标兜底: %s", point)
    logger.info("🖱️ 点击订阅号文章卡片: %s", point)
    if not wi.post_click(sub_win.hwnd, point[0], point[1]):
        return None

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        articles = [w for w in ww.find_article_windows() if w.hwnd not in baseline_hwnds]
        if articles:
            wi.human_sleep(1.0, 1.5)  # 等文章页加载并发出带凭证请求
            return articles[0]
        time.sleep(0.8)
    return None


def open_subscription_and_click_article(main_win: ww.WinWindow, baseline_hwnds: set,
                                        baseline_save_time: float, timeout: float = 25.0) -> bool:
    """策略 B 完整流程：订阅号消息 → 点击第一篇文章 → 等待凭证捕获。"""
    sub_win = _open_subscription_window(main_win, baseline_hwnds)
    if not sub_win:
        return False
    wi.human_sleep(0.6, 1.0)

    article_win = _click_first_article_in_subscriptions(sub_win, baseline_hwnds)
    if article_win is None:
        logger.warning("⚠️ 未能打开新的文章窗口。")
        return False
    logger.info("📄 新文章窗口已打开: %s", article_win)

    if wait_for_credential_update(baseline_save_time, timeout=timeout):
        logger.info("✅ [策略B] 订阅号文章点击成功触发新凭证捕获。")
        return True
    # 文章窗口已打开但凭证未捕获：补一次 F5（部分页面首载复用缓存未发起新请求）
    logger.info("文章窗口已打开但未捕获凭证，补发一次 F5...")
    wi.post_refresh(article_win.hwnd)
    return wait_for_credential_update(baseline_save_time, timeout=10.0)


# ── 定向刷新：为指定公众号建立/续期独立会话凭证 ───────

def _wait_for_new_window(match, baseline_hwnds: set, wait_seconds: float = 12.0):
    """等待一个匹配条件的新 webview 窗口出现（不在基线集合中）。"""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        for w in ww.find_webview_windows():
            if w.hwnd not in baseline_hwnds and match(w):
                return w
        time.sleep(0.8)
    return None


def _search_and_open_account_profile(main_win: ww.WinWindow, keyword: str,
                                     baseline_hwnds: set, wait_seconds: float = 12.0):
    """在主窗口搜索关键词并打开对应的公众号主页窗口。返回主页窗口，失败返回 None。"""
    logger.info("🔎 [定向] 主窗口搜索公众号: [%s]...", keyword)
    ww.activate_window(main_win.hwnd)
    wi.human_sleep(0.4, 0.7)

    # 1. 复用已打开的主页窗口（标题通常就是公众号名）
    for w in ww.find_webview_windows():
        if keyword in w.title and "搜一搜" not in w.title:
            logger.info("♻️ 复用已打开的主页窗口: %s", w)
            return w

    # 2. Ctrl+F 聚焦搜索框（清空残留）→ 剪贴板粘贴关键词，先不回车，尝试点选联想结果
    wi.focus_search_and_type(main_win.hwnd, keyword, confirm=False)
    wi.human_sleep(0.8, 1.2)

    # 3. UIA 在主窗口联想下拉中定位该公众号条目并点击；失败则回车兜底
    point = _try_uia_find_and_get_center(main_win.hwnd, (keyword,))
    if point:
        logger.info("🖱️ 点击搜索联想中的公众号条目: %s", point)
        wi.post_click(main_win.hwnd, point[0], point[1])
    else:
        logger.info("UIA 未定位到联想条目，回车搜索兜底...")
        wi.post_key(main_win.hwnd, wi.VK_RETURN)

    # 4. 等待公众号主页窗口（标题含关键词，排除搜一搜结果页）
    def _is_profile(w):
        return keyword in w.title and "搜一搜" not in w.title

    profile = _wait_for_new_window(_is_profile, baseline_hwnds, wait_seconds)
    if profile is None:
        # 搜索可能打开了搜一搜窗口，在其中再找一次公众号条目
        soso = next((w for w in ww.find_webview_windows() if "搜一搜" in w.title), None)
        if soso:
            point = _try_uia_find_and_get_center(soso.hwnd, (keyword, "公众号"))
            if point:
                wi.post_click(soso.hwnd, point[0], point[1])
                profile = _wait_for_new_window(_is_profile, baseline_hwnds, wait_seconds)
    if profile:
        logger.info("📖 公众号主页窗口已打开: %s", profile)
    return profile


def _click_first_article_in_profile(profile_win: ww.WinWindow, baseline_hwnds: set,
                                    wait_seconds: float = 12.0):
    """在公众号主页窗口点击第一篇文章，返回新出现的文章窗口（或 None）。"""
    point = _try_uia_find_and_get_center(profile_win.hwnd, ("阅读", "更新", "篇原创", "小时前"))
    if point is None:
        # 主页窗口顶部为账号信息区，文章列表首条约在窗口 30% 高度处
        point = _fallback_point_in_window(profile_win, 0.5, 0.30)
        logger.info("UIA 未定位到文章条目，使用比例坐标兜底: %s", point)
    logger.info("🖱️ 点击公众号主页第一篇文章: %s", point)
    if not wi.post_click(profile_win.hwnd, point[0], point[1]):
        return None
    article = _wait_for_new_window(lambda w: True, baseline_hwnds, wait_seconds)
    if article:
        wi.human_sleep(1.0, 1.5)  # 等文章页发出带 key/pass_ticket 的请求
    return article


def run_targeted_account_flow_windows(keyword: str, auto_cleanup: bool = True,
                                      wait_cred_seconds: float = 25.0) -> bool:
    """定向为指定公众号刷新凭证（供刷新队列/主动续期调度调用）：
    搜索该公众号 → 打开主页 → 点开第一篇文章 → 等待 mitm 捕获该 biz 的独立会话凭证。
    任何一步失败都返回 False，由调用方决定重试（最终一致，不要求每次成功）。"""
    if not keyword:
        return False
    logger.info("🚀 [定向] 开始为公众号 [%s] 刷新凭证...", keyword)
    baseline_save_time = get_pool_max_save_time()
    baseline_hwnds = set()

    try:
        main_win = ensure_wechat_ready()
        if not main_win:
            return False
        baseline_hwnds = {w.hwnd for w in ww.find_wechat_windows()}
        wi.human_sleep(0.3, 0.6)

        # 1. 已有该公众号的文章/主页窗口 → 直接 F5 续期（最快路径）
        for w in ww.find_webview_windows():
            if keyword in w.title and "搜一搜" not in w.title:
                logger.info("♻️ [定向] 已有 [%s] 相关窗口，F5 续期: %s", keyword, w)
                ww.activate_window(w.hwnd)
                wi.post_refresh(w.hwnd)
                if wait_for_credential_update(baseline_save_time, timeout=18.0):
                    return True
                break  # F5 没捕获到则继续走完整搜索流程

        # 2. 搜索 → 公众号主页 → 点第一篇文章
        profile = _search_and_open_account_profile(main_win, keyword, baseline_hwnds)
        if profile is None:
            logger.warning("⚠️ [定向] 未能打开 [%s] 的公众号主页。", keyword)
            return False
        wi.human_sleep(0.8, 1.2)

        article = _click_first_article_in_profile(profile, baseline_hwnds)
        if article is None:
            logger.warning("⚠️ [定向] [%s] 未能打开文章窗口，尝试对主页窗口 F5 兜底...", keyword)
            wi.post_refresh(profile.hwnd)
        else:
            logger.info("📄 [定向] 文章窗口已打开: %s", article)

        if wait_for_credential_update(baseline_save_time, timeout=wait_cred_seconds):
            logger.info("✅ [定向] [%s] 凭证刷新成功！", keyword)
            return True
        # 文章窗口已打开但凭证未捕获：补一次 F5
        last_win = article or profile
        wi.post_refresh(last_win.hwnd)
        if wait_for_credential_update(baseline_save_time, timeout=10.0):
            logger.info("✅ [定向] [%s] 凭证刷新成功（F5 兜底）！", keyword)
            return True
        logger.warning("⚠️ [定向] [%s] 本轮未能捕获新凭证，将由调度器择机重试。", keyword)
        return False
    except Exception as e:
        logger.warning("⚠️ [定向] [%s] 流程异常: %s", keyword, e)
        return False
    finally:
        if auto_cleanup:
            try:
                wi.human_sleep(1.0, 1.5)
                cleanup_opened_windows(baseline_hwnds)
            except Exception as clean_err:
                logger.warning("清理窗口时出现异常: %s", clean_err)




def cleanup_opened_windows(baseline_hwnds: set) -> None:
    """关闭流程中新出现的 webview 窗口（不影响用户此前已打开的窗口）。"""
    try:
        current = ww.find_wechat_windows()
        opened = [w for w in current if w.hwnd not in baseline_hwnds and w.exe_lower in ww.WEBVIEW_PROCESS_NAMES]
        for w in opened:
            logger.info("🧹 关闭自动化打开的窗口: %s", w)
            ww.close_window(w.hwnd)
            wi.human_sleep(0.3, 0.6)
    except Exception as e:
        logger.warning("清理窗口时出现异常: %s", e)


# ── 总编排入口 ───────────────────────────────────────

def run_wechat_pc_flow_windows(auto_cleanup: bool = True,
                               wait_cred_seconds: float = 25.0) -> bool:
    """运行 Windows 微信凭证刷新流水线。返回 True 表示成功触发新凭证捕获。"""
    logger.info("🚀 开始执行 Windows 微信 PC 端自动化流程...")
    baseline_save_time = get_pool_max_save_time()
    baseline_hwnds = set()

    try:
        main_win = ensure_wechat_ready()
        if not main_win:
            return False
        baseline_hwnds = {w.hwnd for w in ww.find_wechat_windows()}
        wi.human_sleep(0.3, 0.6)

        # 策略 A: 刷新已有文章窗口
        try:
            if refresh_existing_article_window(baseline_save_time):
                return True
        except Exception as e:
            logger.warning("⚠️ 策略 A 执行异常: %s", e)

        # 策略 B: 订阅号消息 → 点击文章
        try:
            if open_subscription_and_click_article(main_win, baseline_hwnds,
                                                   baseline_save_time, wait_cred_seconds):
                return True
        except Exception as e:
            logger.warning("⚠️ 策略 B 执行异常: %s", e)

        logger.warning("⚠️ Windows 自动化流水线未能触发新凭证捕获。")
        return False
    finally:
        if auto_cleanup:
            try:
                wi.human_sleep(1.0, 1.5)
                cleanup_opened_windows(baseline_hwnds)
            except Exception as clean_err:
                logger.warning("清理窗口时出现异常: %s", clean_err)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Windows 微信凭证刷新流水线")
    parser.add_argument("--keyword", "-k", default=None, help="定向刷新指定公众号（搜索该号并打开一篇文章）")
    args = parser.parse_args()
    if args.keyword:
        ok = run_targeted_account_flow_windows(args.keyword)
    else:
        ok = run_wechat_pc_flow_windows()
    sys.exit(0 if ok else 1)
