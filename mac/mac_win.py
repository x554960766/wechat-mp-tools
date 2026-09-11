"""macOS 微信窗口查找、激活与后台截图。"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import Quartz
from AppKit import NSWorkspace

WECHAT_BUNDLE_ID = "com.tencent.xinWeChat"
WECHAT_OWNER_NAMES = {"WeChat", "微信"}


@dataclass
class MacWindow:
    """一个窗口的完整描述。bounds 单位是「点 (point)」。"""
    window_id: int
    title: str
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def __repr__(self) -> str:
        return f"MacWindow(id={self.window_id}, title={self.title!r}, bounds=({self.x},{self.y},{self.w},{self.h}))"


class MacScreenImage:
    """轻量原生截屏图像包装器，无需依赖 numpy / cv2，支持切片与形状属性以兼容历史代码。"""
    def __init__(self, cg_image):
        self.cg_image = cg_image
        self.w = int(Quartz.CGImageGetWidth(cg_image)) if cg_image else 0
        self.h = int(Quartz.CGImageGetHeight(cg_image)) if cg_image else 0

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.h, self.w, 4)

    @property
    def size(self) -> int:
        return self.w * self.h * 4

    def __getitem__(self, item) -> MacScreenImage:
        """支持形如 img[0:65, pane_x0:main.w] 的 2D 裁剪切片语法"""
        if not self.cg_image or self.w == 0 or self.h == 0:
            return MacScreenImage(None)
        if isinstance(item, tuple) and len(item) == 2:
            slice_y, slice_x = item
            y0 = slice_y.start or 0
            y1 = slice_y.stop if slice_y.stop is not None else self.h
            x0 = slice_x.start or 0
            x1 = slice_x.stop if slice_x.stop is not None else self.w
            w = max(0, x1 - x0)
            h = max(0, y1 - y0)
            if w == 0 or h == 0:
                return MacScreenImage(None)
            sub = Quartz.CGImageCreateWithImageInRect(self.cg_image, Quartz.CGRectMake(x0, y0, w, h))
            return MacScreenImage(sub)
        raise NotImplementedError("仅支持 [y0:y1, x0:x1] 格式的切片")


# ---------------------------------------------------------------- 枚举与查找

def list_wechat_windows(min_size: int = 100) -> list[MacWindow]:
    """枚举当前屏幕上所有微信相关的有效窗口。"""
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    out = []
    for w in infos:
        if w.get("kCGWindowOwnerName") not in WECHAT_OWNER_NAMES:
            continue
        b = w.get("kCGWindowBounds", {})
        width = b.get("Width", 0)
        height = b.get("Height", 0)
        if width < min_size or height < min_size:
            continue
        out.append(MacWindow(
            window_id=w["kCGWindowNumber"],
            title=w.get("kCGWindowName", "") or "",
            x=int(b.get("X", 0)),
            y=int(b.get("Y", 0)),
            w=int(width),
            h=int(height),
        ))
    if not out:
        # 降级查询所有有效微信窗口（避免多屏幕/Spaces 空间切换时因焦点不在前台而过滤掉有效窗口）
        infos_all = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll,
            Quartz.kCGNullWindowID,
        )
        for w in infos_all:
            if w.get("kCGWindowOwnerName") not in WECHAT_OWNER_NAMES:
                continue
            b = w.get("kCGWindowBounds", {})
            width = b.get("Width", 0)
            height = b.get("Height", 0)
            if width < min_size or height < min_size:
                continue
            if int(b.get("X", 0)) < -500 or int(b.get("Y", 0)) < -500:
                continue
            out.append(MacWindow(
                window_id=w["kCGWindowNumber"],
                title=w.get("kCGWindowName", "") or "",
                x=int(b.get("X", 0)),
                y=int(b.get("Y", 0)),
                w=int(width),
                h=int(height),
            ))
    return out


def find_main_window(timeout: float = 1.0) -> MacWindow | None:
    """
    定位微信主窗口。
    主窗口判定：
    1. 标题为「微信」或「WeChat」且尺寸为真实主窗口（宽度 >= 600 且高度 >= 450），
       必须排除标题同为'微信'但尺寸较小的独立微窗（如 280x380 的登录提示、浮动小卡片等）；
    2. 微信 4.x 个别场景主窗口标题为空，退化为取「宽度在 600~2000 之间且高度>=450」的最大窗口。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        wins = list_wechat_windows()
        titled_cands = [w for w in wins if w.title in ("微信", "WeChat") and w.w >= 600 and w.h >= 450]
        if titled_cands:
            return max(titled_cands, key=lambda w: w.w * w.h)
        cands = [w for w in wins if 600 <= w.w <= 2000 and w.h >= 450]
        if cands:
            return max(cands, key=lambda w: w.w * w.h)
        time.sleep(0.2)
    return None


def find_web_windows(exclude_ids: set[int] | None = None) -> list[MacWindow]:
    """
    公众号/文章/搜一搜窗口判定：不是主窗口的其他微信窗口。
    微信 4.x 中文章页与搜一搜 Web 窗口通常是独立窗口。
    """
    main = find_main_window(timeout=0.5)
    exclude = set(exclude_ids or set())
    if main:
        exclude.add(main.window_id)
    return [w for w in list_wechat_windows() if w.window_id not in exclude]


def wait_new_web_window(before_ids: set[int], timeout: float = 12.0) -> MacWindow | None:
    """等待新增的 Web 窗口出现（如搜一搜、文章详情等）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        news = [w for w in find_web_windows() if w.window_id not in before_ids]
        if news:
            return max(news, key=lambda w: w.w * w.h)
        time.sleep(0.4)
    return None


def find_portal_window(verify_content: bool = True) -> MacWindow | None:
    """查找屏幕上已打开的公众号批量授权聚合页窗口（兼容微信 4.x 内嵌分栏与 3.x 独立弹窗）。"""
    portal_keywords = (
        "公众号批量授权", "批量授权", "授权聚合中心", "mp-batch-portal",
        "5200", "5202", "授权清单", "一键开始全自动流转授权", "流转授权",
        "全部公众号授权就绪", "低风控安全建议", "同步代理助手", "仅复制待授权链接"
    )

    # 1. 优先检测微信 4.x 主窗口内嵌的第三栏 Webview 分栏（常见于 4.x+ 分栏视图）
    main_win = find_main_window(timeout=0.3)
    if main_win and main_win.w >= 700:
        try:
            from mac.mac_ocr import ocr
            img_main = capture_window(main_win)
            if img_main is not None:
                boxes = ocr(img_main)
                # 检查右侧分栏区域（通常在 x >= main_win.w * 0.4）
                right_boxes = [b for b in boxes if b.left >= int(main_win.w * 0.4)]
                if any(any(k in b.text for k in portal_keywords) for b in right_boxes):
                    return MacWindow(
                        window_id=main_win.window_id,
                        title="公众号批量授权聚合中心(内嵌)",
                        x=main_win.x,
                        y=main_win.y,
                        w=main_win.w,
                        h=main_win.h,
                    )
        except Exception:
            pass

    # 2. 检测独立 Web 窗口（兼容微信 3.x 或微信独立弹窗模式）
    wins = list_wechat_windows()
    main_id = main_win.window_id if main_win else None
    candidates = []
    for w in wins:
        if main_id and w.window_id == main_id:
            continue
        title = (w.title or "").strip()
        # 明确排除公众号名片与明显非网页窗口
        if title == "公众号":
            continue
        # 命中标题关键词的置顶候选
        if any(k in title for k in ("聚合", "批量授权", "batch-portal", "5200", "授权中心")):
            candidates.insert(0, w)
        # 独立网页窗口尺寸常在 400x380 以上，即使标题为 "微信"、空或 "(窗口)" 也不放过
        elif w.w >= 400 and w.h >= 350:
            candidates.append(w)

    for w in candidates:
        if not verify_content and any(k in (w.title or "") for k in ("聚合", "批量授权", "batch-portal", "5200")):
            return w
        try:
            img = capture_window(w)
            if img is None:
                continue
            from mac.mac_ocr import ocr
            boxes = ocr(img)
            # 严格排除视频号相关窗口（避免误把视频号窗口识别为聚合页）
            if any(any(v in b.text for v in ("视频号", "赞和收藏", "下载", "直播", "动态", "私信")) for b in boxes[:8]):
                continue
            # 严格排除公众号名片与普通文章页面
            if any(any(v in b.text for v in ("全部 贴图 文章", "原创内容", "个朋友关注", "发消息", "关注公众号")) for b in boxes[:8]):
                continue
            if any(any(k in b.text for k in portal_keywords) for b in boxes):
                return w
        except Exception as e:
            import logging
            logging.getLogger("mac_win").warning("find_portal_window verify error: %s", e)
    return None



# ---------------------------------------------------------------- 激活

def raise_window(win: MacWindow) -> bool:
    """利用 macOS Accessibility (AXUIElement) 将指定窗口置顶激活（支持标题与坐标容差匹配）。"""
    activate_wechat()
    try:
        from ApplicationServices import AXUIElementCreateApplication, AXUIElementCopyAttributeValue, AXUIElementPerformAction
        ws = NSWorkspace.sharedWorkspace()
        for app in ws.runningApplications():
            if app.bundleIdentifier() == WECHAT_BUNDLE_ID:
                app.activateWithOptions_(1 << 1)
                pid = app.processIdentifier()
                app_ref = AXUIElementCreateApplication(pid)
                err, wins = AXUIElementCopyAttributeValue(app_ref, "AXWindows", None)
                if not err and wins:
                    for w in wins:
                        err_title, title_val = AXUIElementCopyAttributeValue(w, "AXTitle", None)
                        err_pos, pos_val = AXUIElementCopyAttributeValue(w, "AXPosition", None)
                        err_sz, sz_val = AXUIElementCopyAttributeValue(w, "AXSize", None)
                        match = False
                        if title_val and win.title and (title_val == win.title or ("(窗口)" in title_val and "(窗口)" in win.title)):
                            match = True
                        if "(内嵌)" in win.title and title_val in ("微信", "WeChat"):
                            match = True
                        if not match and pos_val and sz_val:
                            try:
                                ok1, pt = Quartz.AXValueGetValue(pos_val, Quartz.kAXValueCGPointType, None)
                                ok2, sz = Quartz.AXValueGetValue(sz_val, Quartz.kAXValueCGSizeType, None)
                                if ok1 and ok2:
                                    if abs(pt.x - win.x) <= 8 and abs(pt.y - win.y) <= 8 and abs(sz.width - win.w) <= 15:
                                        match = True
                            except Exception:
                                pass
                        if match:
                            AXUIElementPerformAction(w, "AXRaise")
                            return True
                break
    except Exception:
        pass
    return False


def activate_wechat() -> None:
    """把微信 App 置前；若未运行或窗口已关闭则唤起主界面。"""
    ws = NSWorkspace.sharedWorkspace()
    for app in ws.runningApplications():
        if app.bundleIdentifier() == WECHAT_BUNDLE_ID:
            app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
            # 若所有窗口均已关闭，open -a WeChat 会恢复主窗口
            subprocess.run(["open", "-a", "WeChat"], check=False)
            return
    subprocess.run(["open", "-a", "WeChat"], check=False)


def launch_wechat() -> None:
    """拉起微信 App。"""
    subprocess.run(["open", "-a", "WeChat"], check=False)


# ---------------------------------------------------------------- 截图（后台可截）

def capture_window(win: MacWindow) -> MacScreenImage:
    """
    按窗口 ID 截图，返回轻量原生的 MacScreenImage 包装对象（包含原生 CGImageRef）。
    完全不依赖 numpy 或 cv2，窗口被遮挡也能截到正确内容。
    """
    img_ref = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        win.window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming
        | Quartz.kCGWindowImageNominalResolution,
    )
    if img_ref is None:
        raise RuntimeError(f"截图失败 window_id={win.window_id}（请检查终端是否授予'屏幕录制'权限）")

    return MacScreenImage(img_ref)
