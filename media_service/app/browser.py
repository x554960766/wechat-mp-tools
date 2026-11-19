"""浏览器通道：用本机真实 Chrome 过风控，抓 DOM 并下载图片。

为什么不用 requests：实测本机对该站点的静态请求一律返回「环境异常」验证页
（HTTP 200 但内容是验证码墙），换 UA / 关代理都无效。
"""
from __future__ import annotations

import logging
import re
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Error, Page, sync_playwright

from . import config

log = logging.getLogger("wx.browser")
_PROFILE_LOCK = threading.Lock()

# 一次性从页面里取回结构化数据
_EXTRACT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const qpic = u => !!u && /mmbiz\.qpic\.cn|mmbiz\.qlogo\.cn/.test(u);
  const uniq = a => [...new Set(a)];
  const src = el => el.getAttribute('data-src') || el.getAttribute('src') || '';
  const content = document.querySelector('#js_content');
  const contentImgs = content
    ? uniq([...content.querySelectorAll('img')].map(src).filter(qpic)) : [];
  const allImgs = uniq([...document.querySelectorAll('img')].map(src).filter(qpic));
  // 留言区有两套 UI：旧版 discuss_*，2024 后的 PC 页面是新的 cmt_* 组件。
  // 快照实测：.discuss_item/.discuss_message_img 只出现在 CSS 与 JS 模板串里，
  // 活 DOM 一条都匹配不到，所以新旧选择器必须同时带上，否则永远是 0。
  const cmtRoot = document.querySelector('.stream_comment_dialog__content, #js_bt_cmt_container');
  const commentImgs = uniq([...document.querySelectorAll(
      '.discuss_message_img img, .discuss_item img, .comment_content img, ' +
      '.stream_comment_dialog__content img, #js_bt_cmt_container img')]
    .map(src).filter(qpic));
  // 留言条数：comments=0 时用来区分「留言没带图」和「留言区压根没加载出来」
  const cmtText = cmtRoot ? clean(cmtRoot.innerText) : '';
  const commentCount = document.querySelectorAll('.discuss_item, .cmt_item, .comment_item').length;
  // 跳转/已注销页可能还没有 body，直接取 innerText 会抛 TypeError
  const head = clean(document.body ? document.body.innerText : '').slice(0, 1500);
  // 整页 HTML：验证页有两类指纹——URL 里带 appmsgcaptcha，
  // 或还没重定向但 HTML 已埋了 js_verify/TCaptcha（23K 小页面实测）。
  // 只看 innerText 会漏掉后者，把它误报成「文章没正文」。
  const html = document.documentElement.outerHTML;
  // 错误页的真实文案（参数错误 / 已删除 / 需验证…），没有就是空串
  const errBox = document.querySelector(
    '.weui-msg__title, .weui-msg__desc, #js_error_msg, .msg_desc, .global_error_msg, .tips');
  const errText = clean(errBox ? errBox.innerText : '');
  return {
    href: location.href,
    errText: errText,
    head: head,
    title: clean(window.msg_title || (document.querySelector('#activity-name') || {}).innerText || ''),
    author: clean((document.querySelector('#js_name') || {}).innerText || ''),
    publish: clean((document.querySelector('#publish_time') || {}).innerText || ''),
    cover: window.msg_cdn_url || (document.querySelector('meta[property="og:image"]') || {}).content || '',
    contentImgs: contentImgs,
    otherImgs: allImgs.filter(u => !contentImgs.includes(u)),
    commentImgs: commentImgs,
    commentCount: commentCount,
    // 新组件加载失败的证据：容器在、但只有「首屏重试」占位、正文几乎为空
    commentRootFound: !!cmtRoot,
    commentPlaceholder: !!(cmtRoot && cmtRoot.querySelector('.cmt_body_placeholder, .cmt_paging_retry')),
    commentTextLen: cmtText.length,
    bodyLen: content ? content.innerText.length : 0,
    verify: /appmsgcaptcha/.test(location.href) || /环境异常|去验证/.test(head) ||
            /js_verify|TCaptcha|wappoc/.test(html),
    // 风控给页面打过 poc_token 才会降级/拦验证。只看 URL，不看 HTML：
    // HTML 里的 poc_token 只是正文链接残留，验证页和正常页都可能带。
    pocToken: /[?&]poc_token=/.test(location.href),
    pageBytes: html.length,
    gone: /已自主注销|内容无法查看|已被发布者删除|因违规无法查看|该内容已被删除/.test(head),
    // 留言接口实测返回 {"ret":-3,"errmsg":"no session"}：正文能匿名读，留言不能。
    // 没有登录态时点开留言只会停在「加载失败，点击重试」，这时候说「没留言」是在骗人。
    hasSession: /(?:^|;\s*)(?:wap_sid2|data_ticket|slave_sid)=/.test(document.cookie),
  };
}
"""


@contextmanager
def open_browser(headless: bool | None = None) -> Iterator[tuple[BrowserContext, Page]]:
    """启动带持久化 profile 的真实 Chrome（登录态/验证 Cookie 留存到 .chrome-profile）。"""
    headless = config.HEADLESS if headless is None else headless
    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    # archive worker 与验证控制任务共用唯一持久化 profile，任何时刻只允许一个上下文。
    _PROFILE_LOCK.acquire()
    try:
      with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(config.PROFILE_DIR),
            channel=config.CHANNEL,
            headless=headless,
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield ctx, page
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    finally:
        _PROFILE_LOCK.release()


def warm_up(page: Page) -> None:
    """先访问首页建立正常会话，降低首次即被判环境的概率。"""
    try:
        page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded",
                  timeout=config.PAGE_TIMEOUT_MS)
        page.wait_for_timeout(800)
    except Exception as e:  # 首页失败不影响后续
        log.warning("warm-up 失败: %s", e)


def _eval(page: Page, js: str, retries: int = 3):
    """文章页会自己跳一次（canonical 重定向），evaluate 可能撞到执行上下文销毁，重试即可。"""
    last = None
    for _ in range(retries):
        try:
            return page.evaluate(js)
        except Error as e:  # playwright 的通用 Error
            last = e
            if "Execution context was destroyed" in str(e) or "navigat" in str(e).lower():
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                continue
            raise
    raise last


def _scroll_to_bottom(page: Page) -> None:
    """正文图是懒加载的，必须滚到底把它们真正请求出来。"""
    last = -1
    for _ in range(config.SCROLL_ROUNDS):
        h = _eval(page, "() => document.documentElement.scrollHeight")
        if h == last:
            break
        last = h
        page.mouse.wheel(0, 3500)
        page.wait_for_timeout(500)
    page.wait_for_timeout(600)


def _open_comment_area(page: Page) -> bool:
    """评论区默认不渲染，先点开「留言」入口，再等它把首屏留言拉回来。

    实测两件事：入口按钮文案就是「留言」两个字（不含「全部留言」），所以三个候选
    都要试、「留言」放最后；点开之后新组件 cmt_* 常常停在「首屏加载失败，重试」
    占位（cmt_body_placeholder cmt_first_screen_retry），需要再点一次重试并等一会儿。
    """
    clicked = False
    # 先认真实入口：底部栏的留言按钮（#js_comment_bottom_text 的祖先 button）。
    # 按文字「留言」去匹配会命中 a11y 隐藏标签或弹层标题，点到的是死节点。
    for sel in ("#js_comment_bottom_text", ".sns_opr_gap", "#js_cmt_icon"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                clicked = True
                break
        except Exception:
            continue
    for text in ("查看全部留言", "全部留言", "留言"):
        if clicked:
            break
        try:
            loc = page.get_by_text(text, exact=False).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        try:
            if page.locator(".discuss_item, .cmt_item").count():
                return True
        except Exception:
            pass
        return False

    page.wait_for_timeout(1200)
    for _ in range(3):
        try:
            retry = page.locator(".cmt_first_screen_retry, .cmt_paging_retry").first
            if retry.count() and retry.is_visible():
                retry.click(timeout=1500)
        except Exception:
            pass
        page.wait_for_timeout(700)
        try:
            if page.locator(
                    ".stream_comment_dialog__content img, #js_bt_cmt_container img, .discuss_item").count():
                break
        except Exception:
            pass
    return True


_GONE_RE = re.compile(r"已自主注销|内容无法查看|已被发布者删除|因违规无法查看|该内容已被删除|此内容因违规")
_BADLINK_RE = re.compile(r"参数错误|链接已失效|链接不存在|invalid|url参数")


def classify(data: dict) -> tuple[str, str]:
    """把页面数据归成一个状态 + 给人看的原因。

    关键教训：验证码页没加载完时 bodyLen 也是 0，和「文章本身空白」长得一样，
    所以必须先用页面的真实文案（errText）区分，不能一律报 empty。
    """
    err = (data.get("errText") or "").strip()
    head = data.get("head") or ""
    if data.get("verify"):
        return "verify", "触发微信环境验证，等放行后再跑，或点「打开浏览器验证」人工过一下"
    if data.get("gone") or _GONE_RE.search(err) or _GONE_RE.search(head):
        return "gone", f"文章已删除/注销{f'：{err}' if err else ''}"
    if _BADLINK_RE.search(err.lower()):
        return "badlink", f"链接无效{f'：{err}' if err else ''}（sn 参数错或链接已过期）"
    if not data.get("title") and data.get("bodyLen", 0) == 0:
        return "empty", f"页面无正文{f'：{err}' if err else '，可能仍是验证页'}"
    # 2026-11-06 实证：风控还有一档「静默降级」——URL 带 poc_token、页面体积正常、
    # 正文文字齐全，但 #js_content 里一张图都不下发，也不报「环境异常」。
    # 同一链接早些时候能抓到 4 张正文图，说明不是文章本来没图。
    # 不判这一档的话会记成 ok，产出「只有封面」的归档还以为成功。
    if data.get("pocToken") and not data.get("contentImgs"):
        return "degraded", "页面被降级：正文只有文字、配图未下发（风控 poc_token），过几分钟再跑"
    return "ok", ""


def comments_loaded(data: dict, entry_clicked: bool) -> bool:
    """留言是不是真的渲染出来了。点到了按钮不算。

    三类证据任一成立才算加载成功：有留言节点、有留言图、有像样的留言正文
    （且不再是「加载失败，点击重试」占位）。
    """
    if not entry_clicked:
        return False
    if data.get("commentCount", 0) > 0 or data.get("commentImgs"):
        return True
    return bool(data.get("commentRootFound")
                and data.get("commentTextLen", 0) > 30
                and not data.get("commentPlaceholder"))


def _dump_failure(page: Page, status: str, url: str) -> None:
    """失败页自动留整页快照到 data/debug/，事后可离线分析，不再靠手动探针耗风控额度。"""
    try:
        debug_dir = config.DATA_DIR / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = debug_dir / f"fail-{ts}-{status}.html"
        html = page.content()
        path.write_text(html, encoding="utf-8")
        log.info("失败快照: %s（%s）", path, url[:100])
    except Exception:
        log.exception("失败快照写入异常（不影响主流程）")


def capture(page: Page, url: str) -> dict:
    """打开一篇并抽取标题/封面/正文图/评论图。

    撞到「环境异常」时先按兵不动等一会儿再试：实测这是短时频率风控，
    等 60~120 秒通常会自行放行，直接重试反而继续被拦。
    """
    data: dict = {}
    status, reason = "ok", ""
    for attempt in range(config.VERIFY_RETRY + 1):
        page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)
        page.wait_for_timeout(1500)
        data = _eval(page, _EXTRACT_JS)
        # 弱页面兜底：没标题、没正文、也没验证指纹，多半是 domcontentloaded
        # 太早、页面还在跳转链里（2026-11-06 回归实测：真页面被误报 empty）。
        # 等 2.5s 重取一次再下结论，成本可忽略且不发新请求。
        if not data.get("title") and not data.get("bodyLen") and not data.get("verify"):
            page.wait_for_timeout(2500)
            data = _eval(page, _EXTRACT_JS)
        status, reason = classify(data)
        if status not in ("verify", "degraded"):
            break
        if attempt < config.VERIFY_RETRY:
            # 降级页要等的是风控窗口退去，实测以分钟计，比验证页更久
            wait = (config.DEGRADED_WAIT if status == "degraded"
                    else config.VERIFY_WAIT * (config.VERIFY_BACKOFF ** attempt))
            log.warning("页面%s，%ss 后重试（第 %d 次）: %s",
                        "被降级" if status == "degraded" else "撞到环境验证",
                        int(wait), attempt + 1, url)
            page.wait_for_timeout(int(wait) * 1000)

    if status != "ok":
        _dump_failure(page, status, url)
        return {"status": status, "url": url, "reason": reason}

    _scroll_to_bottom(page)
    data = _eval(page, _EXTRACT_JS)   # 懒加载后再取一次
    entry_clicked = _open_comment_area(page)
    if entry_clicked:
        data = _eval(page, _EXTRACT_JS)
    # 实测教训：点到了「留言」按钮不代表留言渲染出来（点完 .discuss_item 仍可能为 0），
    # 所以 commentsLoaded 只认「DOM 里真的出现了留言节点或留言正文」，点没点到单独记录。
    loaded = comments_loaded(data, entry_clicked)
    # 没登录态时留言接口必然 no session，此时「0 条留言」不代表文章真没留言
    needs_login = entry_clicked and not loaded and not data.get("hasSession")

    data.update({"status": "ok", "url": url,
                 "commentEntryClicked": entry_clicked,
                 "commentsLoaded": loaded,
                 "commentNeedsLogin": needs_login})
    return data


def download_image(ctx: BrowserContext, url: str) -> bytes | None:
    """借浏览器上下文下载（自动带 Cookie），并附 Referer 绕过 qpic 防盗链。"""
    try:
        r = ctx.request.get(url, headers={"Referer": "https://mp.weixin.qq.com/"}, timeout=30000)
        if r.ok:
            body = r.body()
            if 0 < len(body) <= config.MAX_IMAGE_BYTES:
                return body
            log.warning("图片体积异常 %s bytes: %s", len(body), url)
    except Exception as e:
        log.warning("下载失败 %s: %s", url, e)
    return None


_PROBE_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  // 留言区的「入口按钮」到底叫什么：只找叶子节点，避免把整段正文当成按钮
  const buttons = [];
  for (const t of ['查看全部留言', '全部留言', '留言']) {
    const hits = [...document.querySelectorAll('*')].filter(
      el => el.children.length === 0 && clean(el.innerText) === t);
    if (hits.length) buttons.push({ text: t, count: hits.length });
  }
  const items = [...document.querySelectorAll('.discuss_item, .cmt_item, .comment_item')];
  // 点开留言后到底渲染到哪：把 extra 区里的 class 全 dump 出来，别再猜选择器
  const extra = document.querySelector('.rich_media_extra');
  const classes = extra
    ? [...new Set([...extra.querySelectorAll('*')]
        .map(e => e.className)
        .filter(c => typeof c === 'string' && c.trim()))].slice(0, 40)
    : [];
  // 新组件 cmt_*：留言真正渲染进底部弹层 .stream_comment_dialog__content
  const dialog = document.querySelector('.stream_comment_dialog__content');
  const dialogText = dialog ? clean(dialog.innerText) : '';
  const imgs = [...document.querySelectorAll(
      '.discuss_message_img img, .discuss_item img, ' +
      '.stream_comment_dialog__content img, #js_bt_cmt_container img')]
    .map(i => i.getAttribute('data-src') || i.getAttribute('src') || '')
    .filter(u => /mmbiz\.qpic\.cn|mmbiz\.qlogo\.cn/.test(u));
  return {
    buttons: buttons,
    containers: ['#js_cmt_area', '.discuss_container', '#comment_area', '.comment_wrp',
                 '.discuss_list', '#js_cmt_list', '.comment_item', '.rich_media_extra',
                 '#js_bt_cmt_container', '.stream_comment_dialog__content']
      .filter(sel => document.querySelector(sel)),
    extraClasses: classes,
    iframes: [...document.querySelectorAll('iframe')].map(f => f.src).filter(Boolean),
    discussItem: items.length,
    discussImg: new Set(imgs).size,
    dialogTextLen: dialogText.length,
    dialogText: dialogText.slice(0, 300),
    cmtPlaceholder: [...document.querySelectorAll('.cmt_body_placeholder, .cmt_paging_retry')]
      .map(e => clean(e.innerText).slice(0, 60)),
    sampleComments: items.slice(0, 5).map(i => clean(i.innerText).slice(0, 80)),
  };
}
"""


def probe_comments(page: Page, url: str) -> dict:
    """只读探针：不下载、不归档，只报告留言区在 DOM 里到底是什么状态。

    用途是给「comments=0」提供证据：是没留言、留言没带图，还是按钮没找到。
    """
    page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)
    page.wait_for_timeout(1500)
    data = _eval(page, _EXTRACT_JS)
    status, reason = classify(data)
    if status != "ok":
        return {"status": status, "url": url, "reason": reason}

    _scroll_to_bottom(page)
    before = _eval(page, _PROBE_JS)
    opened = _open_comment_area(page)
    after = _eval(page, _PROBE_JS) if opened else before
    latest = _eval(page, _EXTRACT_JS) if opened else data

    # 留存点完按钮后的完整 DOM：一次成功抓取就是永久证据，
    # 之后分析选择器不再需要再碰微信（风控窗口宝贵，不许重复花）。
    snap = ""
    try:
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        snap_dir = Path(config.DATA_DIR) / "debug"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap = str(snap_dir / f"comments-{stamp}.html")
        Path(snap).write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # 快照失败不影响探针本身
        log.warning("留言区 DOM 快照保存失败: %s", exc)

    return {
        "status": "ok",
        "url": url,
        "title": data.get("title"),
        "comment_area_opened": opened,
        "dom_snapshot": snap,
        "before_click": before,
        "after_click": after,
        "commentCount": latest.get("commentCount", 0),
        "commentImgs": latest.get("commentImgs", []),
    }
