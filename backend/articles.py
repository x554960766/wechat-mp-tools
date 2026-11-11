"""
文章获取与管理模块
获取文章列表、搜索文章、管理下载任务
"""

import os
import sys
import subprocess
import json
import time
import re
import random
import urllib.parse
import threading
import logging
import requests as req
import shutil
from flask import Blueprint, jsonify, request, Response
from datetime import datetime
from pathlib import Path

from backend.config import (
    CONFIG_FILE, BASE_URL, DEFAULT_HEADERS, OUTPUT_DIR, DATA_DIR,
    DOWNLOAD_HISTORY_FILE, ACCOUNTS_FILE,
    load_json, save_json, get_settings, get_proxies_dict, report_proxy_status,
    normalize_wechat_url, get_default_wechat_ua
)
from backend.account_pool import borrow_session, borrow_weread_session, account_pool, get_weread_session
from backend.accounts import biz_to_book_id
from backend.wechat_url import normalize_wechat_short_url, short_id_from_review_id
from backend.runtime import desktop_available, host_path_hint, release_memory
from backend.request_profile import sleep_backoff, sleep_throttled

articles_bp = Blueprint("articles", __name__, url_prefix="/api/articles")
logger = logging.getLogger("backend.articles")

# 下载进度管理
_download_tasks = {}
_download_lock = threading.Lock()


def _clean_old_tasks_locked():
    """定期清理已结束的历史任务，防止常驻内存无上限增长"""
    if len(_download_tasks) > 50:
        finished = [
            k for k, t in _download_tasks.items()
            if isinstance(t, dict) and t.get("status") in ("completed", "failed", "cancelled")
        ]
        for k in finished[:max(1, len(_download_tasks) - 50)]:
            _download_tasks.pop(k, None)


def _get_session():
    """获取凭证（通过账号池）"""
    account_id, token, cookie_str = borrow_session()
    return token, cookie_str


def fetch_article_detail_content(url: str, review_id: str = "") -> str:
    """抓取微信公众号文章网页并提取 HTML 正文（.rich_media_content），还原图片 data-src
    参考 we-mp-rss 优化：支持微信读书免风控正文接口、移动微信高仿请求与无头浏览器兜底
    """
    import bs4
    from backend.downloader import fetch_wechat_article_html

    raw_html, _, error = fetch_wechat_article_html(url, review_id=review_id)
    if not raw_html:
        raise RuntimeError(error or "抓取文章内容失败")

    soup = bs4.BeautifulSoup(raw_html, "html.parser")
    content_node = soup.select_one(".rich_media_content") or soup.select_one("#js_content") or soup.select_one("#js_article")
    if not content_node:
        return raw_html

    for img in content_node.find_all("img"):
        if img.get("data-src"):
            img["src"] = img["data-src"]
            del img["data-src"]

    return str(content_node)


_review_url_cache: dict[str, str] = {}
_review_info_cache: dict[str, dict] = {}
_review_cache_lock = threading.Lock()

# 遵循官方客户端架构: 章节列表与元数据二级内存缓存 (并发降频保护，杜绝重复请求导致的 -2014)
MP_CHAPTERS_CACHE: dict[str, dict] = {}  # key: f"{book_id}:{offset}:{synckey}" -> {'expire': float, 'data': dict}
MP_INFO_CACHE: dict[str, dict] = {}      # key: book_id -> {'expire': float, 'data': dict}
_MP_CACHE_LOCK = threading.Lock()


def get_cached_chapters(book_id: str, offset: int, synckey: int = 0) -> dict | None:
    cache_key = f"{book_id}:0:{synckey}" if offset == 0 else f"{book_id}:{offset}"
    with _MP_CACHE_LOCK:
        item = MP_CHAPTERS_CACHE.get(cache_key)
        if item and time.time() < item.get("expire", 0):
            return item.get("data")
        if item:
            MP_CHAPTERS_CACHE.pop(cache_key, None)
    return None


def set_cached_chapters(book_id: str, offset: int, synckey: int, data: dict, ttl: int = 60):
    cache_key = f"{book_id}:0:{synckey}" if offset == 0 else f"{book_id}:{offset}"
    with _MP_CACHE_LOCK:
        if len(MP_CHAPTERS_CACHE) > 300:
            now = time.time()
            expired_keys = [k for k, v in MP_CHAPTERS_CACHE.items() if now >= v.get("expire", 0)]
            for k in expired_keys:
                MP_CHAPTERS_CACHE.pop(k, None)
            if len(MP_CHAPTERS_CACHE) > 300:
                MP_CHAPTERS_CACHE.clear()
        MP_CHAPTERS_CACHE[cache_key] = {
            "expire": time.time() + ttl,
            "data": data,
        }


def get_cached_book_info(book_id: str) -> dict | None:
    with _MP_CACHE_LOCK:
        item = MP_INFO_CACHE.get(book_id)
        if item and time.time() < item.get("expire", 0):
            return item.get("data")
        if item:
            MP_INFO_CACHE.pop(book_id, None)
    return None


def set_cached_book_info(book_id: str, data: dict, ttl: int = 600):
    with _MP_CACHE_LOCK:
        if len(MP_INFO_CACHE) > 300:
            now = time.time()
            expired_keys = [k for k, v in MP_INFO_CACHE.items() if now >= v.get("expire", 0)]
            for k in expired_keys:
                MP_INFO_CACHE.pop(k, None)
            if len(MP_INFO_CACHE) > 300:
                MP_INFO_CACHE.clear()
        MP_INFO_CACHE[book_id] = {
            "expire": time.time() + ttl,
            "data": data,
        }


def _put_review_cache_locked(review_id: str, info: dict):
    """带容量上限的 LRU 式缓存入库，防止数万推文导致内存持续上涨"""
    if len(_review_info_cache) >= 1000:
        for old_k in list(_review_info_cache.keys())[:250]:
            _review_info_cache.pop(old_k, None)
            _review_url_cache.pop(old_k, None)
    _review_info_cache[review_id] = info
    if info.get("doc_url"):
        _review_url_cache[review_id] = info["doc_url"]


def _check_is_original(it: dict, mp_i: dict) -> bool:
    """准确判断文章是否带有原创声明标签"""
    for src in (mp_i, it):
        if not isinstance(src, dict):
            continue
        c_type = src.get("copyright_type") or src.get("copyrightType") or src.get("copyright") or src.get("copyright_stat")
        if c_type is not None:
            try:
                if int(c_type) > 0:
                    return True
            except (ValueError, TypeError):
                if str(c_type).strip().lower() in ("1", "2", "true", "yes", "original"):
                    return True
        is_orig = src.get("is_original") or src.get("isOriginal") or src.get("original")
        if is_orig is True:
            return True
        if isinstance(is_orig, (int, str)) and str(is_orig).strip().lower() in ("1", "true", "yes"):
            return True
    return False


def _resolve_review_info(review_id: str, headers: dict) -> dict:
    """通过微信读书 review/single 接口动态解析推文详情 (短链 doc_url、真实发布时间、原创标记等)"""
    if not review_id:
        return {}
    with _review_cache_lock:
        if review_id in _review_info_cache:
            return _review_info_cache[review_id]

    info = {
        "doc_url": "",
        "create_time": 0,
        "is_original": False,
        "title": "",
        "digest": "",
        "author": "",
        "cover": "",
    }
    short_id = short_id_from_review_id(review_id)
    if short_id:
        info["doc_url"] = f"https://mp.weixin.qq.com/s/{short_id}"

    try:
        url = f"https://i.weread.qq.com/review/single?reviewId={review_id}"
        s = get_weread_session()
        resp = s.get(url, headers=headers, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            r_data = data.get("review") or {}
            mp_info = r_data.get("mpInfo") or {}
            orig_id = mp_info.get("originalId", "")
            if orig_id and len(orig_id) >= 15:
                doc_url = normalize_wechat_short_url(f"https://mp.weixin.qq.com/s/{orig_id}")
            else:
                doc_url = normalize_wechat_short_url(mp_info.get("doc_url", ""))
            if doc_url:
                info["doc_url"] = doc_url

            c_time = (
                r_data.get("createTime")
                or r_data.get("updateTime")
                or mp_info.get("time")
                or mp_info.get("create_time")
                or mp_info.get("update_time")
                or mp_info.get("publish_time")
                or 0
            )
            if c_time:
                info["create_time"] = int(c_time)

            info["title"] = mp_info.get("title") or r_data.get("title") or ""
            info["digest"] = mp_info.get("digest") or ""
            info["author"] = mp_info.get("author") or ""
            info["cover"] = mp_info.get("pic_url") or mp_info.get("thumbUrl") or ""
            info["is_original"] = _check_is_original(r_data, mp_info)

            with _review_cache_lock:
                _put_review_cache_locked(review_id, info)
            return info
    except Exception:
        pass

    with _review_cache_lock:
        _put_review_cache_locked(review_id, info)
    return info


def _resolve_review_doc_url(review_id: str, headers: dict) -> str:
    """通过微信读书 review/single 接口解析原生微信推文链接 (优先解析为免风控短链)"""
    if not review_id:
        return ""
    # 优先直接使用 review_id 包含的短链标识（如 MP_WXS_2940696320_yvrxT0XE-9ELBG4gQZhB4g -> yvrxT0XE-9ELBG4gQZhB4g）
    # 短链在微信网关下直接返回文章内容，完全绕过长链接引发的风控验证码
    short_id = short_id_from_review_id(review_id)
    if short_id:
        return f"https://mp.weixin.qq.com/s/{short_id}"

    with _review_cache_lock:
        if review_id in _review_url_cache:
            return _review_url_cache[review_id]
    info = _resolve_review_info(review_id, headers)
    return info.get("doc_url", "")








def _fetch_articles_page(fakeid: str, begin: int, count: int, keyword: str = "", account_name: str = "", from_article_id: str = "") -> tuple:
    """使用微信读书官方推文聚合引擎获取文章列表 (articles, total_count, can_continue)
    1. 统一 401 静默自愈续期通道，遇到凭据过期自动换取最新 token
    2. 首屏优先获取 mp/cover 置顶最新推文 (100% 成功率，带 doc_url)
    3. 获取 mp/chapters 分页推文；若受 499 临时限制自动平滑降级，绝不抛出 500
    4. 备用通道 article/list 兜底，并发解析每篇推文原生微信链接
    """
    book_id = biz_to_book_id(fakeid)
    if not book_id:
        raise RuntimeError(f"无效的公众号标识: {fakeid}")

    s = get_weread_session()

    def _do_get(url: str):
        """内部请求函数：对齐官方 SyncKeyRequestFeature 与 WRRetryHandler 错误码自愈策略"""
        req_url = url
        # 遵循 APK SyncKeyRequestFeature: 缺少 synckey 时自动追加 (但翻页带 offset 时官方接口不追加 synckey)
        if any(k in req_url for k in ("/mp/list", "/shelf/sync", "/subscription/books")) or ("/mp/chapters" in req_url and "offset=" not in req_url):
            if "synckey=" not in req_url:
                req_url = f"{req_url}&synckey=0" if "?" in req_url else f"{req_url}?synckey=0"

        last_resp = None
        last_h = None
        last_acc = None
        for att in range(2):
            try:
                acc_id, vid, tok, h = borrow_weread_session()
            except RuntimeError as e:
                raise RuntimeError("账号池中无可用微信读书账号，请先在『账号池』页面扫码登录") from e
            last_h = h
            last_acc = acc_id
            try:
                r = s.get(req_url, headers=h, timeout=10)
                last_resp = r

                # ── 优先检测腾讯防水墙 (TCaptcha) ──
                from backend.waterwall import detect_waterwall, waterwall_hit, record_failure
                ww_marker = detect_waterwall(r.status_code, r.text)
                if ww_marker:
                    record_failure(req_url, r.status_code, r.headers, r.text, ww_marker)
                    waterwall_hit(req_url, r.status_code, ww_marker)
                    return r, h, acc_id

                # 401 Token 过期: 自动静默续期重试
                if r.status_code == 401:
                    print(f"[_fetch_articles_page] 微信读书 401 Token 过期，触发自动静默续期 (第 {att+1} 次)...", flush=True)
                    account_pool.report(acc_id, status_code=401)
                    time.sleep(0.5)
                    continue

                # 429 或 499: 精准匹配官方错误码 (对齐 OverFreqInterceptor 与 WRRetryHandler 策略)
                if r.status_code in (429, 499):
                    err_json = {}
                    try:
                        err_json = r.json()
                    except Exception:
                        pass
                    err_c = err_json.get("errcode")
                    err_m = str(err_json.get("errmsg") or "")

                    # 1. 微信读书频控 -2014 (OverFreqInterceptor): 短时间瞬时并发过高，自动退避 2.5 秒后降频重试
                    if err_c == -2014 or "-2014" in r.text:
                        print(f"[_fetch_articles_page] 触发微信读书网关频控 (-2014)，自动退避 2.5 秒后降频重试...", flush=True)
                        time.sleep(2.5)
                        try:
                            r_retry = s.get(req_url, headers=h, timeout=10)
                            if r_retry.status_code == 200:
                                return r_retry, h, acc_id
                        except Exception as ex_retry:
                            logger.debug("频控退避重试网络异常: %s", ex_retry)

                        # 退避重试后依然触发 -2014 频控：激活腾讯防水墙/频控熔断并触发前端验证
                        print(f"[_fetch_articles_page] 账号 [{acc_id}] 退避重试后仍处于高频风控 (-2014)，激活防水墙与熔断保护...", flush=True)
                        record_failure(req_url, r.status_code, r.headers, r.text, "-2014")
                        waterwall_hit(req_url, r.status_code, "-2014")
                        account_pool.report(acc_id, status_code=499, ret=-2014, error_msg="触发高频风控 (-2014)，临时冷却并建议完成安全验证")
                        return r, h, acc_id

                    # 2. 网关超时 -2012 (对齐 WRRetryHandler.canRetryTimeOut / whetherNeedToRetry 策略)
                    elif err_c == -2012 or "-2012" in r.text:
                        print(f"[_fetch_articles_page] 上游网关超时 (-2012)，遵循官方策略等待 0.5 秒自动重试...", flush=True)
                        time.sleep(0.5)
                        try:
                            r_retry = s.get(req_url, headers=h, timeout=10)
                            if r_retry.status_code == 200:
                                return r_retry, h, acc_id
                        except Exception:
                            pass

                    # 3. 账号人机验证 / 终端同步态 -2041 (对齐 WRRetryHandler.canRetryAfterVerify 策略)
                    elif err_c == -2041 or "2041" in r.text:
                        print(f"[_fetch_articles_page] 账号 [{acc_id}] 触发终端会话在线态核验 (-2041)，调用 activate_terminal_session 模拟真机同步...", flush=True)
                        from backend.weread_signer import activate_terminal_session
                        if activate_terminal_session(h, session=s):
                            try:
                                r_retry = s.get(req_url, headers=h, timeout=10)
                                if r_retry.status_code == 200:
                                    print(f"[_fetch_articles_page] 账号 [{acc_id}] 移动端会话同步激活成功，重试请求通过！", flush=True)
                                    return r_retry, h, acc_id
                            except Exception as ex:
                                logger.debug("重试请求异常: %s", ex)

                        # 若移动会话已激活但仍返回 -2041，确定触发官方 TCaptcha 人机验证，唤起前端滑块弹窗！
                        print(f"[_fetch_articles_page] 账号 [{acc_id}] 移动会话已激活但仍返回 -2041，确认为官方强制 TCaptcha 人机验证！", flush=True)
                        record_failure(req_url, r.status_code, r.headers, r.text, "-2041")
                        waterwall_hit(req_url, r.status_code, "-2041")
                        account_pool.report(acc_id, status_code=499, ret=-2041, error_msg="触发腾讯人机验证 (-2041)，需在网页完成滑块验证")
                        return r, h, acc_id
                    else:
                        print(f"[_fetch_articles_page] 账号 [{acc_id}] 遇到 499/429 限制 ({err_c} {err_m})，自动轮换账号池备用账号...", flush=True)
                        account_pool.report(acc_id, status_code=r.status_code, ret=err_c, error_msg=f"请求频控 ({err_c} {err_m})，临时冷却")

                    # 频控后指数退避，避免轮换账号的瞬间形成密集重试
                    sleep_backoff(att + 1, base=0.4, cap=4.0)
                    continue
                return r, h, acc_id
            except Exception as e:
                logger.debug("[_fetch_articles_page] 网络异常: %s", e)
                sleep_backoff(att + 1, base=0.5, cap=4.0)
        return last_resp, last_h, last_acc

    articles = []
    has_more = False
    active_headers = None
    active_acc_id = None

    # ── 1. 优先尝试从 mp/chapters 获取分页推文列表 (官方标准分页推文接口) ──
    raw_items = []
    # 遵循官方客户端机制：先查二级内存缓存，避免用户或自动化任务短时间内重复请求导致的 -2014 网关频控
    cached_chap = get_cached_chapters(book_id, begin, 0)
    if cached_chap and isinstance(cached_chap, dict):
        raw_items = cached_chap.get("data") or cached_chap.get("chapters") or cached_chap.get("list") or []
        has_more = bool(cached_chap.get("hasMore", len(raw_items) >= count))
    else:
        try:
            # 遵循 APK MpRemoteService: 首页拉取走 syncChapters (带 pf=android, synckey=0)，翻页走 loadMoreChapters (带 offset，不带 synckey/pf)
            if begin == 0:
                url_chap = f"https://i.weread.qq.com/mp/chapters?bookId={book_id}&count={count}&pf=android&synckey=0"
            else:
                url_chap = f"https://i.weread.qq.com/mp/chapters?bookId={book_id}&count={count}&offset={begin}"
            resp_chap, h_chap, acc_chap = _do_get(url_chap)
            if resp_chap:
                if resp_chap.status_code == 200:
                    active_headers = h_chap or active_headers
                    active_acc_id = acc_chap or active_acc_id
                    data = resp_chap.json()
                    # 写入二级缓存 (首页 TTL 60秒，翻页 TTL 300秒)
                    ttl = 60 if begin == 0 else 300
                    set_cached_chapters(book_id, begin, 0, data, ttl=ttl)
                    raw_items = data.get("data") or data.get("chapters") or data.get("list") or []
                    has_more = bool(data.get("hasMore", len(raw_items) >= count))
                elif resp_chap.status_code in (429, 499):
                    print(f"[_fetch_articles_page] mp/chapters 返回 HTTP {resp_chap.status_code}，启用多级聚合引擎安全平滑降级...", flush=True)
        except Exception as e:
            logger.debug("[_fetch_articles_page] mp/chapters 异常: %s", e)

    # ── 2. 备用通道：若推文仍为空，尝试从 article/list 补齐 (注意: MP_ 公众号在官方 APK 中无 article/list 接口，跳过以避免 499 频控) ──
    from backend.waterwall import WATERWALL
    if len(raw_items) == 0 and not book_id.startswith("MP_") and not WATERWALL.get("active"):
        try:
            u_alist = f"https://i.weread.qq.com/article/list?bookId={book_id}&synckey=0"
            resp_alist, h_alist, acc_alist = _do_get(u_alist)
            if resp_alist and resp_alist.status_code == 200:
                active_headers = h_alist or active_headers
                active_acc_id = acc_alist or active_acc_id
                al_data = resp_alist.json()
                raw_items = al_data.get("reviews") or al_data.get("data") or []
        except Exception as e:
            logger.debug("[_fetch_articles_page] article/list 备用拉取异常: %s", e)

    # ── 3. 兜底通道：若推文仍为空且为第一页 (begin == 0)，从 mp/cover 获取置顶/最新封面推文 ──
    if len(raw_items) == 0 and begin == 0 and not WATERWALL.get("active"):
        try:
            u_cov = f"https://i.weread.qq.com/mp/cover?bookId={book_id}"
            resp_cov, h_cov, acc_cov = _do_get(u_cov)
            if resp_cov and resp_cov.status_code == 200:
                active_headers = h_cov or active_headers
                active_acc_id = acc_cov or active_acc_id
                cdata = resp_cov.json()
                rid = cdata.get("reviewId", "")
                if rid:
                    info = _resolve_review_info(rid, active_headers or {})
                    t_cov = info.get("title") or cdata.get("title", "")
                    doc_u = info.get("doc_url") or _resolve_review_doc_url(rid, active_headers or {})
                    cov_time = info.get("create_time") or cdata.get("createTime") or cdata.get("updateTime") or 0
                    if not keyword or keyword.lower() in t_cov.lower():
                        articles.append({
                            "title": t_cov,
                            "link": doc_u,
                            "cover": info.get("cover") or cdata.get("pic", "") or cdata.get("avatar", ""),
                            "digest": info.get("digest") or "",
                            "author": info.get("author") or account_name or cdata.get("name", ""),
                            "update_time": int(cov_time or 0),
                            "is_original": info.get("is_original", False),
                            "item_show_type": 0,
                            "id": rid,
                        })
        except Exception as e:
            logger.debug("[_fetch_articles_page] mp/cover 获取异常: %s", e)

    # ── 4. 并发解析推文原生官方链接 doc_url、真实时间戳及原创标记 ──
    if raw_items:
        import concurrent.futures

        def _get_item_meta(it):
            mp_i = it.get("mpInfo") or it.get("review") or {}
            if isinstance(mp_i, dict) and "mpInfo" in mp_i:
                mp_i = mp_i.get("mpInfo", {})
            rid = str(it.get("reviewId") or mp_i.get("reviewId") or "")
            short_id = short_id_from_review_id(rid)
            doc_u = ""
            if short_id:
                doc_u = f"https://mp.weixin.qq.com/s/{short_id}"
            else:
                doc_u = normalize_wechat_short_url(mp_i.get("doc_url") or mp_i.get("linkUrl") or it.get("url") or "")

            c_time = (
                it.get("createTime")
                or it.get("updateTime")
                or it.get("publishTime")
                or it.get("create_time")
                or it.get("update_time")
                or mp_i.get("time")
                or mp_i.get("create_time")
                or mp_i.get("update_time")
                or mp_i.get("publish_time")
                or 0
            )

            is_orig = _check_is_original(it, mp_i)

            # 若短链缺失或时间戳未知，通过 review/single 动态反查补齐
            if (not doc_u or not c_time) and rid:
                rev_info = _resolve_review_info(rid, active_headers or {})
                if not doc_u:
                    doc_u = rev_info.get("doc_url", "")
                if not c_time:
                    c_time = rev_info.get("create_time", 0)
                if not is_orig:
                    is_orig = rev_info.get("is_original", False)

            return doc_u, int(c_time or 0), is_orig

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, max(1, len(raw_items)))) as ex:
            metas = list(ex.map(_get_item_meta, raw_items))

        for it, (doc_u, create_t, is_orig) in zip(raw_items, metas):
            mp_i = it.get("mpInfo") or it.get("review") or {}
            if isinstance(mp_i, dict) and "mpInfo" in mp_i:
                mp_i = mp_i.get("mpInfo", {})
            title = mp_i.get("title") or it.get("title") or ""
            if not title:
                continue
            if keyword and keyword.lower() not in title.lower():
                continue
            pic = mp_i.get("pic_url") or mp_i.get("thumbUrl") or mp_i.get("picUrl") or it.get("cover") or ""
            review_id = str(it.get("reviewId") or mp_i.get("reviewId") or "")

            # 若已有相同项（如首屏兜底），用真实时间戳与详细信息补全
            existing = next((ex for ex in articles if ex.get("title") == title or (review_id and str(ex.get("id")) == review_id)), None)
            if existing:
                if (not existing.get("update_time") or existing.get("update_time") == 0) and create_t:
                    existing["update_time"] = create_t
                if not existing.get("cover") and pic:
                    existing["cover"] = pic
                if not existing.get("link") and doc_u:
                    existing["link"] = doc_u
                if not existing.get("id") and review_id:
                    existing["id"] = review_id
                if not existing.get("is_original") and is_orig:
                    existing["is_original"] = is_orig
                continue

            articles.append({
                "title": title,
                "link": doc_u,
                "cover": pic,
                "digest": mp_i.get("digest", ""),
                "author": mp_i.get("author") or account_name or "",
                "update_time": create_t,
                "is_original": is_orig,
                "item_show_type": 0,
                "id": review_id,
            })

    total_count = begin + len(articles) + (count if has_more else 0)
    can_continue = 1 if has_more else 0

    if active_acc_id:
        account_pool.report(active_acc_id, is_success=True)

    return articles, total_count, can_continue


@articles_bp.route("/list/<fakeid>", methods=["GET"])
def get_articles(fakeid):
    """获取指定公众号的文章列表"""
    begin = request.args.get("begin", 0, type=int)
    count = request.args.get("count", 10, type=int)
    keyword = request.args.get("keyword", "").strip()
    from_article_id = request.args.get("from_article_id", "").strip()

    t_start = time.time()
    print(f"[Articles API] Start fetching articles for fakeid={fakeid} begin={begin} count={count} from_article_id={from_article_id}", flush=True)

    try:
        articles, total_count, can_continue = _fetch_articles_page(
            fakeid, begin, count, keyword, account_name=keyword, from_article_id=from_article_id
        )
        print(f"[Articles API] Fetch finished in {time.time() - t_start:.2f}s, got {len(articles)} articles", flush=True)

        from backend.waterwall import is_waterwall_active, waterwall_snapshot
        res_data = {
            "articles": articles,
            "total": total_count,
            "can_msg_continue": can_continue,
            "begin": begin,
            "count": len(articles),
        }
        if is_waterwall_active():
            res_data["need_verify"] = True
            res_data["waterwall"] = waterwall_snapshot()

        return jsonify(res_data)

    except PermissionError as e:
        print(f"[Articles API] PermissionError in {time.time() - t_start:.2f}s: {e}", flush=True)
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Articles API] Exception in {time.time() - t_start:.2f}s: {e}", flush=True)
        from backend.waterwall import is_waterwall_active, waterwall_snapshot
        err_res = {"error": f"网络请求失败: {str(e)}", "articles": [], "total": 0, "count": 0}
        if is_waterwall_active():
            err_res["need_verify"] = True
            err_res["waterwall"] = waterwall_snapshot()
        return jsonify(err_res), 200


@articles_bp.route("/list-via-appmsg/<fakeid>", methods=["GET"])
def get_articles_appmsg(fakeid):
    """向后兼容接口：获取指定公众号文章列表"""
    return get_articles(fakeid)


def _parse_publish_response(data: dict) -> tuple:
    """解析 appmsgpublish 返回的数据"""
    publish_page_str = data.get("publish_page", "")
    if not publish_page_str:
        return [], 0

    publish_page = json.loads(publish_page_str)
    publish_list = publish_page.get("publish_list", [])
    total_count = publish_page.get("total_count", 0)

    articles = []
    for item in publish_list:
        publish_info_str = item.get("publish_info", "")
        if not publish_info_str:
            continue
        publish_info = json.loads(publish_info_str)
        appmsgex = publish_info.get("appmsgex", [])
        for a in appmsgex:
            articles.append({
                "title": a.get("title", ""),
                "link": a.get("link", ""),
                "cover": a.get("cover", ""),
                "digest": a.get("digest", ""),
                "author": a.get("author", ""),
                "update_time": a.get("update_time", a.get("create_time", 0)),
                "is_original": a.get("copyright_type", "0") != "0",
                "item_show_type": a.get("item_show_type", 0),
            })

    return articles, total_count


@articles_bp.route("/download", methods=["POST"])
def start_download():
    """批量下载文章"""
    data = request.get_json() or {}
    articles = data.get("articles", [])
    account_name = data.get("account_name", "unknown")

    if not articles:
        return jsonify({"error": "没有选择要下载的文章"}), 400

    task_id = f"batch_{int(time.time())}"

    with _download_lock:
        _clean_old_tasks_locked()
        _download_tasks[task_id] = {
            "status": "running",
            "total": len(articles),
            "completed": 0,
            "failed": 0,
            "current": "",
            "results": [],
            "start_time": time.time(),
        }

    thread = threading.Thread(
        target=_do_batch_download,
        args=(task_id, articles, account_name),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "message": f"已启动下载任务，共 {len(articles)} 篇"})


@articles_bp.route("/download-range", methods=["POST"])
def start_range_download():
    """按时间范围分页拉取文章并逐篇下载。"""
    data = request.get_json() or {}
    fakeid = data.get("fakeid", "")
    account_name = data.get("account_name", "unknown")
    start_time = data.get("start_time", 0)
    end_time = data.get("end_time", 0)
    keyword = data.get("keyword", "").strip()
    page_size = int(data.get("page_size", 10) or 10)

    if not fakeid:
        return jsonify({"error": "缺少公众号 fakeid"}), 400
    if not start_time or not end_time:
        return jsonify({"error": "请选择完整的开始和结束日期"}), 400
    if start_time > end_time:
        return jsonify({"error": "开始日期不能晚于结束日期"}), 400

    task_id = f"range_{int(time.time())}"
    with _download_lock:
        _clean_old_tasks_locked()
        _download_tasks[task_id] = {
            "status": "running",
            "mode": "range",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "scanned": 0,
            "current": "",
            "results": [],
            "start_time": time.time(),
            "cancel_requested": False,
            "stop_reason": "",
        }

    thread = threading.Thread(
        target=_do_range_download,
        args=(task_id, fakeid, account_name, start_time, end_time, keyword, page_size),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "message": "已启动按时间范围下载任务"})


@articles_bp.route("/collect-latest", methods=["POST"])
def collect_latest():
    """单个公众号一键采集最近 N 条（默认 50 条），自动去重跳过并深度优化风控"""
    data = request.get_json() or {}
    fakeid = data.get("fakeid", "").strip()
    account_name = data.get("account_name", "").strip()
    limit = int(data.get("limit", 50) or 50)
    skip_existing = data.get("skip_existing", True)

    if not fakeid:
        return jsonify({"error": "缺少公众号 fakeid"}), 400

    if not account_name:
        accounts = load_json(ACCOUNTS_FILE, [])
        for a in accounts:
            if a.get("fakeid") == fakeid:
                account_name = a.get("nickname", "unknown")
                break
        account_name = account_name or "unknown"

    task_id = f"collect_{int(time.time())}"
    with _download_lock:
        _clean_old_tasks_locked()
        _download_tasks[task_id] = {
            "status": "running",
            "mode": "collect",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "scanned": 0,
            "current": f"准备采集【{account_name}】最近 {limit} 篇推文...",
            "results": [],
            "start_time": time.time(),
            "cancel_requested": False,
            "stop_reason": "",
            "account_name": account_name,
        }

    targets = [{"fakeid": fakeid, "name": account_name}]
    thread = threading.Thread(
        target=_do_collect_latest,
        args=(task_id, targets, limit, skip_existing),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "message": f"已启动【{account_name}】一键采集任务（最近 {limit} 篇）"})


@articles_bp.route("/collect-all", methods=["POST"])
def collect_all():
    """全部关注公众号一键采集（各最新 50 条），串行流转、自动跳过已下载、多账号轮换防风控"""
    data = request.get_json() or {}
    limit = int(data.get("limit", 50) or 50)
    skip_existing = data.get("skip_existing", True)

    accounts = load_json(ACCOUNTS_FILE, [])
    if not accounts:
        return jsonify({"error": "关注列表为空，请先在公众号管理中关注或添加公众号"}), 400

    targets = []
    for a in accounts:
        fid = a.get("fakeid", "").strip()
        name = a.get("nickname", "").strip() or fid
        if fid:
            targets.append({"fakeid": fid, "name": name})

    if not targets:
        return jsonify({"error": "没有可采集的公众号"}), 400

    task_id = f"collect_all_{int(time.time())}"
    with _download_lock:
        _clean_old_tasks_locked()
        _download_tasks[task_id] = {
            "status": "running",
            "mode": "collect_all",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "scanned": 0,
            "total_accounts": len(targets),
            "current_account_idx": 0,
            "current_account": "",
            "current": f"准备采集全部 {len(targets)} 个公众号（各最新 {limit} 篇）...",
            "results": [],
            "start_time": time.time(),
            "cancel_requested": False,
            "stop_reason": "",
        }

    thread = threading.Thread(
        target=_do_collect_latest,
        args=(task_id, targets, limit, skip_existing),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "message": f"已启动全部 {len(targets)} 个公众号一键采集任务"})


@articles_bp.route("/download-cancel/<task_id>", methods=["POST"])
def cancel_download(task_id):
    """请求停止下载任务。"""
    with _download_lock:
        task = _download_tasks.get(task_id)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        if task["status"] not in ("running",):
            return jsonify({"message": "任务已结束"})
        task["cancel_requested"] = True
        task["status"] = "cancelling"
        task["stop_reason"] = "用户请求停止"
    return jsonify({"message": "正在停止下载任务"})


@articles_bp.route("/download-url", methods=["POST"])
def download_by_url():
    """通过 URL 下载单篇/多篇文章 (带智能正则提取与批量标准短链转换)"""
    data = request.get_json() or {}
    raw_urls = data.get("urls", [])
    if isinstance(raw_urls, str):
        raw_urls = [u.strip() for u in raw_urls.split("\n") if u.strip()]

    if not raw_urls:
        return jsonify({"error": "请输入文章 URL"}), 400

    import re
    extracted_urls = []
    for item in raw_urls:
        if not item or not isinstance(item, str):
            continue
        # 兼容用户直接粘贴带有前后缀文本、标题或标点符号的微信推文链接
        found = re.findall(r'https?://(?:mp\.weixin\.qq\.com/s[^\s\'"<>]+|mp\.weixin\.qq\.com/[^\s\'"<>]+)', item)
        if found:
            for u in found:
                u = re.sub(r'[\)\]\>，。]+$', '', u)
                if u not in extracted_urls:
                    extracted_urls.append(u)
        elif item.startswith("http://") or item.startswith("https://"):
            u = item.strip()
            if u not in extracted_urls:
                extracted_urls.append(u)

    if not extracted_urls:
        return jsonify({"error": "未检测到有效的微信公众号文章链接"}), 400

    extracted_urls = list(dict.fromkeys(normalize_wechat_short_url(u) for u in extracted_urls))

    # 批量将长链接预解析为免风控的标准短链与 reviewId
    url_info_map = {}
    long_urls_to_resolve = [u for u in extracted_urls if "__biz=" in u]
    if long_urls_to_resolve:
        try:
            from backend.account_pool import borrow_weread_session, get_weread_session
            acc_id, vid, tok, h = borrow_weread_session()
            s = get_weread_session()
            res = s.post("https://i.weread.qq.com/mp/getreviewid", json={"urls": long_urls_to_resolve}, headers=h, timeout=8).json()
            for r_item in res.get("reviewIds", []):
                orig_u = r_item.get("url", "")
                rid = r_item.get("reviewId", "")
                short_id = short_id_from_review_id(rid)
                if short_id:
                    url_info_map[orig_u] = {
                        "short_url": f"https://mp.weixin.qq.com/s/{short_id}",
                        "review_id": rid,
                    }
        except Exception as e:
            logger.debug(f"[download_by_url] 批量预解析短链异常: {e}")

    articles = []
    for i, raw_u in enumerate(extracted_urls):
        mapped = url_info_map.get(raw_u, {})
        short_u = mapped.get("short_url")
        rid = mapped.get("review_id", "")
        if not rid and "/s/" in raw_u and "__biz=" not in raw_u:
            sid_match = re.search(r'/s/([A-Za-z0-9_-]{15,})', raw_u)
            if sid_match:
                rid = f"MP_WXS_0_{sid_match.group(1)}"

        articles.append({
            "title": f"article_{i+1}",
            "link": short_u or raw_u,
            "id": rid,
            "aid": rid,
            "original_url": raw_u,
        })

    task_id = f"url_{int(time.time())}"

    with _download_lock:
        _clean_old_tasks_locked()
        _download_tasks[task_id] = {
            "status": "running",
            "total": len(articles),
            "completed": 0,
            "failed": 0,
            "current": "",
            "results": [],
            "start_time": time.time(),
            "cancel_requested": False,
            "stop_reason": "",
        }

    thread = threading.Thread(
        target=_do_batch_download,
        args=(task_id, articles, "url_download"),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "message": f"已启动下载任务，共 {len(articles)} 篇"})


@articles_bp.route("/download-progress/<task_id>", methods=["GET"])
def get_download_progress(task_id):
    """获取下载进度（SSE）"""
    def generate():
        while True:
            with _download_lock:
                task = _download_tasks.get(task_id)

            if not task:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                break

            yield f"data: {json.dumps(task, ensure_ascii=False)}\n\n"

            if task["status"] in ("completed", "failed"):
                break

            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@articles_bp.route("/download-status/<task_id>", methods=["GET"])
def get_download_status(task_id):
    """获取下载任务状态（普通 HTTP）"""
    with _download_lock:
        task = _download_tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task)


def _sync_history_from_disk(history: list) -> bool:
    """自动扫描本地下载文件夹，同步历史记录中缺失的条目，并自动清理残存的无正文空目录"""
    settings = get_settings()
    download_dir = Path(settings.get("download_dir") or str(OUTPUT_DIR))
    
    scan_dirs = []
    if download_dir.exists():
        scan_dirs.append(download_dir)
    if OUTPUT_DIR.resolve() != download_dir.resolve() and OUTPUT_DIR.exists():
        scan_dirs.append(OUTPUT_DIR)

    if not scan_dirs:
        return False

    existing_paths = {item.get("path") for item in history if isinstance(item, dict) and item.get("path")}
    changed = False

    try:
        for sdir in scan_dirs:
            for account_dir in sdir.iterdir():
                if not account_dir.is_dir() or account_dir.name in ("channels", "douyin_downloads", "xhs_downloads", "temp_uploads"):
                    continue

                for art_dir in account_dir.iterdir():
                    if not art_dir.is_dir():
                        continue

                    # 检查目录下是否存在 html 文件
                    html_files = [f for f in art_dir.glob("*.html") if not f.name.endswith("_raw.html")]
                    if not html_files:
                        # 检查是否为失败下载遗留的空目录（仅有空 media 目录等），如果是则安全清理
                        try:
                            has_files = any(f.is_file() for f in art_dir.rglob("*"))
                            if not has_files:
                                shutil.rmtree(art_dir, ignore_errors=True)
                        except Exception:
                            pass
                        continue

                    art_path_str = str(art_dir)
                    if art_path_str in existing_paths:
                        continue

                    meta_file = art_dir / "metadata.json"
                    meta = {}
                    if meta_file.exists():
                        try:
                            meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        except Exception:
                            meta = {}

                    try:
                        link = meta.get("url", "")
                        mtime = art_dir.stat().st_mtime
                        time_val = mtime
                        if meta.get("time"):
                            try:
                                # 尝试解析 2026-06-17T08:12:44 等标准 ISO 格式时间
                                time_val = datetime.fromisoformat(meta["time"]).timestamp()
                            except Exception:
                                pass

                        has_md = meta.get("has_markdown") if "has_markdown" in meta else (any(art_dir.glob("*.md")))
                        has_pdf = meta.get("has_pdf") if "has_pdf" in meta else (any(art_dir.glob("*.pdf")))
                        new_item = {
                            "title": meta.get("title") or art_dir.name,
                            "link": link,
                            "account": account_dir.name,
                            "success": True,
                            "time": time_val,
                            "error": None,
                            "path": art_path_str,
                            "cover_url": meta.get("cover_url", ""),
                            "digest": meta.get("digest", ""),
                            "publish_time": meta.get("publish_time") or int(time_val),
                            "has_markdown": has_md,
                            "has_pdf": has_pdf,
                        }
                        history.append(new_item)
                        existing_paths.add(art_path_str)
                        changed = True
                    except Exception:
                        pass
    except Exception:
        pass

    return changed


def _resolve_history_path(raw_path: str, download_dir: str) -> str:
    """把历史记录里的路径重定位到当前下载目录。

    迁移场景：桌面版记的是 /Users/xxx/.../articles_full/公众号/文章，
    容器里这份路径不存在。这里按 articles_full 之后的部分拼到当前
    download_dir 下，只有目标确实存在才替换，避免误伤。
    """
    if not raw_path:
        return raw_path
    try:
        if Path(raw_path).exists():
            return raw_path
    except OSError:
        pass

    parts = Path(raw_path).parts
    if "articles_full" in parts:
        idx = len(parts) - 1 - parts[::-1].index("articles_full")
        candidate = Path(download_dir).joinpath(*parts[idx + 1:])
        try:
            if candidate.exists():
                return str(candidate)
        except OSError:
            pass
    return raw_path


@articles_bp.route("/history", methods=["GET"])
def get_history():
    """获取下载历史"""
    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    
    # 自动从磁盘同步丢失的历史记录
    if _sync_history_from_disk(history):
        save_json(DOWNLOAD_HISTORY_FILE, history)

    download_dir = get_settings().get("download_dir") or str(OUTPUT_DIR)

    indexed_history = []
    for index, item in enumerate(history):
        if isinstance(item, dict):
            if item.get("account") == "微信视频号":
                continue
            indexed = dict(item)
            if not indexed.get("account") and indexed.get("account_name"):
                indexed["account"] = indexed["account_name"]
            indexed["_index"] = index
            if indexed.get("path"):
                # 旧机器的绝对路径重定位到当前下载目录；界面上再显示宿主机路径
                indexed["path"] = _resolve_history_path(indexed["path"], download_dir)
                indexed["display_path"] = host_path_hint(indexed["path"])
            indexed_history.append(indexed)
    indexed_history.sort(key=lambda x: x.get("time", 0), reverse=True)
    # 限制返回数量
    limit = request.args.get("limit", 50, type=int)
    sliced_history = indexed_history if limit <= 0 else indexed_history[:limit]
    return jsonify({"history": sliced_history, "total": len(indexed_history)})


@articles_bp.route("/history", methods=["DELETE"])
def clear_history():
    """清空下载历史，并删除对应的本地下载文件/目录"""
    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    deleted_dirs_count = 0
    remaining_history = []

    for item in history:
        if not isinstance(item, dict):
            continue

        # 保留微信视频号的历史记录不作处理（视频号有自己的 channels.py 清空接口）
        if item.get("account") == "微信视频号":
            remaining_history.append(item)
            continue

        path_str = item.get("path", "")
        if path_str:
            try:
                path = Path(path_str)
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    deleted_dirs_count += 1
            except Exception:
                pass

    save_json(DOWNLOAD_HISTORY_FILE, remaining_history)

    # 自动清理空出来的公众号目录
    try:
        settings = get_settings()
        download_dir = Path(settings.get("download_dir") or str(OUTPUT_DIR))
        if download_dir.exists():
            for account_dir in download_dir.iterdir():
                if account_dir.is_dir() and account_dir.name not in ("channels", "douyin_downloads", "xhs_downloads", "temp_uploads"):
                    # 如果该公众号目录没有任何文件/文件夹，则将其删除
                    if not any(account_dir.iterdir()):
                        account_dir.rmdir()
    except Exception:
        pass

    return jsonify({
        "message": f"历史已清空，成功删除 {deleted_dirs_count} 个本地下载内容。"
    })


@articles_bp.route("/history/<int:index>", methods=["DELETE"])
def delete_history_item(index):
    """删除单条下载历史，并删除对应下载文件夹。"""
    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    if index < 0 or index >= len(history):
        return jsonify({"error": "历史记录不存在"}), 404

    item = history[index]
    path_str = item.get("path", "") if isinstance(item, dict) else ""
    file_status = "no_path"
    if path_str:
        try:
            path = Path(path_str)
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                file_status = "deleted"
            else:
                file_status = "missing"
        except Exception as e:
            # 文件被占用或删除失败时不阻塞历史记录本身的删除
            file_status = f"error: {str(e)}"

    history.pop(index)
    save_json(DOWNLOAD_HISTORY_FILE, history)

    messages = {
        "deleted": "已删除下载文件和记录",
        "missing": "下载文件已不存在，已删除记录",
        "no_path": "记录没有文件路径，已删除记录",
    }
    msg = messages.get(file_status, f"下载文件清理失败（{file_status}），但已清除历史记录")
    return jsonify({"message": msg, "file_status": file_status})


def _do_batch_download(task_id: str, articles: list, account_name: str):
    """执行批量下载（后台线程）"""
    from backend.downloader import download_single_article

    settings = get_settings()
    delay = settings.get("request_delay", 0.8)
    max_retries = settings.get("max_retries", 3)

    download_dir = Path(settings.get("download_dir") or str(OUTPUT_DIR))
    out_dir = download_dir / account_name
    out_dir.mkdir(parents=True, exist_ok=True)

    history = load_json(DOWNLOAD_HISTORY_FILE, [])

    try:
        for i, article in enumerate(articles):
            with _download_lock:
                task = _download_tasks.get(task_id, {})
                if task.get("cancel_requested") or task.get("status") == "cancelling":
                    task["status"] = "cancelled"
                    task["current"] = ""
                    task["end_time"] = time.time()
                    task["stop_reason"] = task.get("stop_reason") or "用户请求停止"
                    save_json(DOWNLOAD_HISTORY_FILE, history)
                    return

            link = normalize_wechat_short_url(article.get("link", ""))
            title = article.get("title", f"article_{i+1}")

            with _download_lock:
                _download_tasks[task_id]["current"] = f"正在下载第 {i+1} 篇..."

            if not link:
                with _download_lock:
                    _download_tasks[task_id]["failed"] += 1
                    _download_tasks[task_id]["results"].append({
                        "title": title, "success": False, "error": "无链接"
                    })
                continue

            success = False
            error_msg = ""
            result = {}
            review_id = article.get("id") or article.get("aid") or ""

            for attempt in range(1, max_retries + 1):
                try:
                    result = download_single_article(link, out_dir, title, review_id=review_id)
                    if result.get("success"):
                        success = True
                        break
                    error_msg = result.get("error", "未知错误")
                except Exception as e:
                    error_msg = str(e)
                if attempt < max_retries:
                    # 指数退避 + 抖动，避免失败后齐步重试形成尖峰
                    sleep_backoff(attempt, base=1.0, cap=8.0)

            if result.get("title"):
                title = result["title"]

            downloaded_path = result.get("path") if success else None

            with _download_lock:
                if success:
                    _download_tasks[task_id]["completed"] += 1
                    _download_tasks[task_id]["current"] = f"已完成：{title}"
                    _download_tasks[task_id]["results"].append({
                        "title": title, "success": True, "path": downloaded_path or str(out_dir / title)
                    })
                else:
                    _download_tasks[task_id]["failed"] += 1
                    _download_tasks[task_id]["current"] = f"下载失败：{title}"
                    _download_tasks[task_id]["results"].append({
                        "title": title, "success": False, "error": error_msg
                    })

            real_account = result.get("source")
            if not real_account or real_account == "url_download":
                real_account = account_name if account_name != "url_download" else "链接下载"
            # 记录到下载历史
            hist_entry = {
                "title": title,
                "link": normalize_wechat_short_url(article.get("original_url") or link),
                "account": real_account,
                "author": result.get("author", ""),
                "success": success,
                "time": time.time(),
                "error": error_msg if not success else None,
                "path": downloaded_path,
                "cover_url": result.get("cover_url", ""),
                "digest": result.get("digest", ""),
                "publish_time": result.get("publish_time", int(time.time())),
                "has_markdown": result.get("has_markdown", False),
                "has_pdf": result.get("has_pdf", False),
            }
            history.append(hist_entry)
            try:
                from backend.db import record_history
                record_history(hist_entry)
            except Exception:
                pass

            if i < len(articles) - 1:
                # 对数正态节流：均值与原实现 max(1.0, delay)+U(0.3,1.2) 的期望（约 1.75s）对齐，
                # 只是把原本 1.3~2.2s 的窄带换成带长尾的分布；min_delay 抬住下沿，
                # 保证没有任何一次间隔比原来的 1.3s 更密
                sleep_throttled(max(1.0, delay) + 0.75, min_delay=1.3)

        # 保存历史
        save_json(DOWNLOAD_HISTORY_FILE, history)

        with _download_lock:
            task = _download_tasks[task_id]
            if task.get("status") == "cancelling":
                task["status"] = "cancelled"
                task["stop_reason"] = task.get("stop_reason") or "用户请求停止"
            else:
                task["status"] = "completed"
            task["current"] = ""
            task["end_time"] = time.time()
    except Exception as e:
        print(f"[_do_batch_download] 异常: {e}", flush=True)
        save_json(DOWNLOAD_HISTORY_FILE, history)
        with _download_lock:
            task = _download_tasks.get(task_id, {})
            task["status"] = "failed"
            task["current"] = ""
            task["stop_reason"] = str(e)
            task["end_time"] = time.time()
    finally:
        release_memory()


def _download_article_into_task(task_id: str, article: dict, account_name: str, history: list, index: int = 0):
    """Download one article and update task progress."""
    from backend.downloader import download_single_article

    settings = get_settings()
    max_retries = settings.get("max_retries", 3)
    download_dir = Path(settings.get("download_dir") or str(OUTPUT_DIR))
    out_dir = download_dir / account_name
    out_dir.mkdir(parents=True, exist_ok=True)

    link = normalize_wechat_short_url(article.get("link", ""))
    title = article.get("title", f"article_{index + 1}")
    review_id = article.get("id") or article.get("aid") or ""

    with _download_lock:
        _download_tasks[task_id]["current"] = title

    if not link:
        with _download_lock:
            _download_tasks[task_id]["failed"] += 1
            _download_tasks[task_id]["results"].append({
                "title": title, "success": False, "error": "无链接"
            })
        return

    success = False
    error_msg = ""
    result = {}
    for attempt in range(1, max_retries + 1):
        try:
            result = download_single_article(link, out_dir, title, review_id=review_id)
            if result.get("success"):
                success = True
                break
            error_msg = result.get("error", "未知错误")
        except Exception as e:
            error_msg = str(e)
        if attempt < max_retries:
            # 指数退避 + 抖动，避免失败后齐步重试形成尖峰
            sleep_backoff(attempt, base=1.0, cap=8.0)

    if result.get("title"):
        title = result["title"]
    downloaded_path = result.get("path") if success else None

    with _download_lock:
        if success:
            _download_tasks[task_id]["completed"] += 1
            _download_tasks[task_id]["results"].append({
                "title": title, "account": account_name, "success": True, "path": downloaded_path or str(out_dir / title)
            })
        else:
            _download_tasks[task_id]["failed"] += 1
            _download_tasks[task_id]["results"].append({
                "title": title, "account": account_name, "success": False, "error": error_msg
            })

    hist_entry = {
        "title": title,
        "link": link,
        "account": account_name,
        "success": success,
        "time": time.time(),
        "error": error_msg if not success else None,
        "path": downloaded_path,
        "cover_url": result.get("cover_url", ""),
        "digest": result.get("digest", ""),
        "publish_time": result.get("publish_time", int(time.time())),
        "has_markdown": result.get("has_markdown", False),
        "has_pdf": result.get("has_pdf", False),
        "review_id": review_id,
    }
    history.append(hist_entry)
    try:
        from backend.db import record_history
        record_history(hist_entry)
    except Exception:
        pass


def _do_range_download(
    task_id: str,
    fakeid: str,
    account_name: str,
    start_time: int,
    end_time: int,
    keyword: str,
    page_size: int,
):
    """分页拉取文章，下载时间范围内文章，遇到更早文章后停止（带存量智能跳过与风控平滑）。"""
    settings = get_settings()
    delay = settings.get("request_delay", 0.8)
    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    begin = 0
    downloaded_index = 0
    stop = False

    try:
        while not stop:
            with _download_lock:
                task = _download_tasks.get(task_id, {})
                if task.get("cancel_requested") or task.get("status") == "cancelling":
                    task["status"] = "cancelled"
                    task["current"] = ""
                    task["end_time"] = time.time()
                    task["stop_reason"] = task.get("stop_reason") or "用户请求停止"
                    save_json(DOWNLOAD_HISTORY_FILE, history)
                    return
                task["current"] = f"正在获取第 {begin // page_size + 1} 页"

            res = _fetch_articles_page(fakeid, begin, page_size, keyword, account_name=account_name)
            articles, total_count = res[0], res[1]
            if not articles:
                stop = True
                with _download_lock:
                    _download_tasks[task_id]["stop_reason"] = "没有更多文章"
                break

            with _download_lock:
                _download_tasks[task_id]["scanned"] += len(articles)

            out_of_range_count = 0
            for article in articles:
                article_time = article.get("update_time") or 0
                if article_time > 0:
                    if end_time and article_time > end_time:
                        continue
                    if start_time and article_time < start_time:
                        out_of_range_count += 1
                        continue
                elif start_time or end_time:
                    # 避免在指定具体时间范围时误下载时间未知的旧置顶/封面推文
                    continue

                link = normalize_wechat_short_url(article.get("link", ""))
                title = article.get("title", f"article_{downloaded_index + 1}")

                # 智能跳过已存在并下载成功的文章，极速节流防封控
                is_exist = False
                existing_path = None
                try:
                    from backend.db import is_article_downloaded
                    is_exist, existing_path = is_article_downloaded(account_name, link, title)
                except Exception:
                    pass

                if not is_exist:
                    for h_item in history:
                        if not isinstance(h_item, dict):
                            continue
                        h_link = h_item.get("link", "")
                        h_title = h_item.get("title", "")
                        h_path = h_item.get("path")
                        if (h_link and h_link == link) or (h_title and h_title == title and h_item.get("account") == account_name):
                            if h_item.get("success") and h_path and Path(h_path).exists():
                                is_exist = True
                                existing_path = h_path
                                break

                if is_exist:
                    with _download_lock:
                        task = _download_tasks.get(task_id, {})
                        task["total"] += 1
                        task["completed"] += 1
                        task["current"] = f"[已存在跳过] {title}"
                        task["results"].append({
                            "title": title, "account": account_name, "success": True, "path": existing_path, "skipped": True
                        })
                    continue

                with _download_lock:
                    task = _download_tasks.get(task_id, {})
                    if task.get("cancel_requested") or task.get("status") == "cancelling":
                        task["status"] = "cancelled"
                        task["current"] = ""
                        task["end_time"] = time.time()
                        task["stop_reason"] = task.get("stop_reason") or "用户请求停止"
                        save_json(DOWNLOAD_HISTORY_FILE, history)
                        return
                    task["total"] += 1

                _download_article_into_task(task_id, article, account_name, history, downloaded_index)
                downloaded_index += 1
                sleep_throttled(max(1.0, delay) + random.uniform(0.3, 0.8), min_delay=1.2)

            # 当前页所有具备有效时间戳的文章都早于 start_time，停止后续扫描
            valid_time_count = sum(1 for a in articles if (a.get("update_time") or 0) > 0)
            if start_time > 0 and valid_time_count > 0 and out_of_range_count >= valid_time_count:
                stop = True
                with _download_lock:
                    _download_tasks[task_id]["stop_reason"] = "已到达所选时间范围之前的文章"
                break

            begin += page_size
            if total_count and begin >= total_count:
                with _download_lock:
                    _download_tasks[task_id]["stop_reason"] = "已扫描全部文章"
                break

        save_json(DOWNLOAD_HISTORY_FILE, history)
        with _download_lock:
            task = _download_tasks[task_id]
            if task["status"] not in ("cancelled",):
                task["status"] = "completed"
            task["current"] = ""
            task["end_time"] = time.time()

    except PermissionError as e:
        save_json(DOWNLOAD_HISTORY_FILE, history)
        with _download_lock:
            task = _download_tasks[task_id]
            task["status"] = "failed"
            task["current"] = ""
            task["stop_reason"] = str(e)
            task["end_time"] = time.time()
    except Exception as e:
        save_json(DOWNLOAD_HISTORY_FILE, history)
        with _download_lock:
            task = _download_tasks[task_id]
            task["status"] = "failed"
            task["current"] = ""
            task["stop_reason"] = str(e)
            task["end_time"] = time.time()
    finally:
        release_memory()


def _do_collect_latest(task_id: str, targets: list, limit: int = 50, skip_existing: bool = True):
    """一键采集后台处理函数：支持单个或全部公众号，自动跳过存量文章，深度优化风控"""
    settings = get_settings()
    delay = settings.get("request_delay", 0.8)
    history = load_json(DOWNLOAD_HISTORY_FILE, [])

    try:
        for acc_idx, target in enumerate(targets):
            fakeid = target["fakeid"]
            account_name = target["name"]

            with _download_lock:
                task = _download_tasks.get(task_id, {})
                if task.get("cancel_requested") or task.get("status") == "cancelling":
                    task["status"] = "cancelled"
                    task["end_time"] = time.time()
                    task["stop_reason"] = "用户取消采集"
                    save_json(DOWNLOAD_HISTORY_FILE, history)
                    return
                task["current_account_idx"] = acc_idx + 1
                task["current_account"] = account_name
                task["current"] = f"正在检索【{account_name}】推文列表..."

            collected_articles = []
            begin = 0
            page_size = min(20, max(10, limit))

            while len(collected_articles) < limit:
                with _download_lock:
                    task = _download_tasks.get(task_id, {})
                    if task.get("cancel_requested") or task.get("status") == "cancelling":
                        task["status"] = "cancelled"
                        task["end_time"] = time.time()
                        save_json(DOWNLOAD_HISTORY_FILE, history)
                        return

                try:
                    articles, total_count, can_continue = _fetch_articles_page(
                        fakeid, begin, page_size, keyword="", account_name=account_name
                    )
                except Exception as e:
                    logger.warning(f"[_do_collect_latest] 拉取【{account_name}】列表异常: {e}")
                    break

                if not articles:
                    break

                for a in articles:
                    if len(collected_articles) >= limit:
                        break
                    # 避免列表内重复
                    if not any(ex.get("link") == a.get("link") or (a.get("title") and ex.get("title") == a.get("title")) for ex in collected_articles):
                        collected_articles.append(a)

                if not can_continue or len(articles) < page_size:
                    break
                begin += len(articles)
                # 微信读书列表翻页平滑间隔（0.5s ~ 0.9s）
                time.sleep(random.uniform(0.5, 0.9))

            # 遍历下载
            for a_idx, article in enumerate(collected_articles):
                with _download_lock:
                    task = _download_tasks.get(task_id, {})
                    if task.get("cancel_requested") or task.get("status") == "cancelling":
                        task["status"] = "cancelled"
                        task["end_time"] = time.time()
                        save_json(DOWNLOAD_HISTORY_FILE, history)
                        return
                    task["scanned"] += 1
                    task["total"] += 1
                    task["current"] = f"【{account_name}】{article.get('title', '')}"

                link = normalize_wechat_short_url(article.get("link", ""))
                title = article.get("title", f"article_{a_idx+1}")

                # 智能跳过已存在文章
                if skip_existing:
                    is_exist = False
                    existing_path = None
                    try:
                        from backend.db import is_article_downloaded
                        is_exist, existing_path = is_article_downloaded(account_name, link, title)
                    except Exception:
                        pass

                    if not is_exist:
                        for h in history:
                            if not isinstance(h, dict):
                                continue
                            if (h.get("link") and h.get("link") == link) or (h.get("title") and h.get("title") == title and h.get("account") == account_name):
                                if h.get("success") and h.get("path") and Path(h.get("path")).exists():
                                    is_exist = True
                                    existing_path = h.get("path")
                                    break
                    if is_exist:
                        with _download_lock:
                            task = _download_tasks.get(task_id, {})
                            task["completed"] += 1
                            task["current"] = f"【{account_name}】[已跳过] {title}"
                            task["results"].append({
                                "title": title, "account": account_name, "success": True, "path": existing_path, "skipped": True
                            })
                        continue

                # 未下载过：执行真实下载
                _download_article_into_task(task_id, article, account_name, history, a_idx)

                # 风控优化：动态随机抖动延迟（1.2s ~ 2.2s）
                sleep_throttled(max(1.0, delay) + random.uniform(0.3, 0.9), min_delay=1.2)

            save_json(DOWNLOAD_HISTORY_FILE, history)

            # 多公众号流转安全缓冲（2.0s ~ 3.5s），支持即时响应取消请求
            if acc_idx < len(targets) - 1:
                sleep_target = random.uniform(2.0, 3.5)
                slept = 0.0
                while slept < sleep_target:
                    with _download_lock:
                        if _download_tasks.get(task_id, {}).get("cancel_requested"):
                            break
                    time.sleep(0.2)
                    slept += 0.2

        with _download_lock:
            task = _download_tasks.get(task_id, {})
            if task.get("cancel_requested") or task.get("status") in ("cancelled", "cancelling"):
                task["status"] = "cancelled"
                task["stop_reason"] = task.get("stop_reason") or "用户取消采集"
                task["current"] = "任务已停止"
            else:
                task["status"] = "completed"
                task["current"] = "采集已完成"
            task["end_time"] = time.time()

    except Exception as e:
        save_json(DOWNLOAD_HISTORY_FILE, history)
        with _download_lock:
            task = _download_tasks.get(task_id, {})
            task["status"] = "failed"
            task["current"] = ""
            task["stop_reason"] = str(e)
            task["end_time"] = time.time()
    finally:
        release_memory()


@articles_bp.route("/open-folder", methods=["POST"])
def open_folder():
    """在系统文件管理器中打开微信下载目录"""
    import os
    import sys
    import subprocess
    
    data = request.get_json() or {}
    account = data.get("account", "").strip()
    
    settings = get_settings()
    download_dir_str = settings.get("download_dir") or str(OUTPUT_DIR)
    path = Path(download_dir_str)
    
    if account:
        path = path / account
    
    try:
        path.mkdir(parents=True, exist_ok=True)
        resolved_path = str(path.resolve())

        # 容器部署没有桌面环境，调 xdg-open 只会抛 FileNotFoundError
        if not desktop_available():
            hint = host_path_hint(resolved_path)
            return jsonify({
                "error": f"容器部署没有桌面环境，请在宿主机打开：{hint}",
                "mode": "headless",
                "container_path": resolved_path,
                "host_path": hint,
            }), 501

        if sys.platform == "darwin":
            subprocess.run(["open", resolved_path])
        elif sys.platform == "win32":
            try:
                if hasattr(os, "startfile"):
                    os.startfile(resolved_path)
                else:
                    subprocess.run(["explorer", resolved_path])
            except Exception:
                subprocess.run(["explorer", resolved_path])
        else:
            subprocess.run(["xdg-open", resolved_path])
        return jsonify({"message": "文件夹已打开"})
    except Exception as e:
        return jsonify({"error": f"打开文件夹失败: {str(e)}"}), 500


@articles_bp.route("/open-file", methods=["POST"])
def open_file():
    """在系统默认程序中打开特定的文件或文件夹，支持按类型定位 pdf/md"""
    import os
    import sys
    import subprocess

    data = request.get_json() or {}
    path_str = data.get("path", "")
    target_type = (data.get("type", "") or "").lower().strip()
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400

    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件或文件夹不存在"}), 404

        resolved_target = path
        if path.is_dir() and target_type:
            if target_type == "pdf":
                pdfs = list(path.glob("*.pdf"))
                if pdfs:
                    resolved_target = pdfs[0]
                else:
                    return jsonify({"error": "未找到 PDF 文件"}), 404
            elif target_type in ("md", "markdown"):
                mds = list(path.glob("*.md"))
                if mds:
                    resolved_target = mds[0]
                else:
                    return jsonify({"error": "未找到 Markdown 文件"}), 404

        resolved_path = str(resolved_target.resolve())

        # 容器部署：把「用系统程序打开」换成「浏览器直接访问」，前端会打开返回的 url
        if not desktop_available():
            url = "/api/articles/serve-file?path=" + urllib.parse.quote(resolved_path, safe="")
            return jsonify({
                "message": "已在浏览器中打开",
                "mode": "headless",
                "url": url,
                "container_path": resolved_path,
                "host_path": host_path_hint(resolved_path),
            })

        if sys.platform == "darwin":
            subprocess.run(["open", resolved_path])
        elif sys.platform == "win32":
            try:
                if hasattr(os, "startfile"):
                    os.startfile(resolved_path)
                else:
                    subprocess.run(["explorer", resolved_path])
            except Exception:
                subprocess.run(["explorer", resolved_path])
        else:
            subprocess.run(["xdg-open", resolved_path])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@articles_bp.route("/open-parent", methods=["POST"])
def open_parent():
    """打开文件所在的父目录并选中当前文件"""
    import os
    import sys
    import subprocess

    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400

    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件或文件夹不存在"}), 404
            
        resolved_path = str(path.resolve())

        if not desktop_available():
            target_dir = path.parent.resolve() if path.is_file() else path.resolve()
            hint = host_path_hint(str(target_dir))
            return jsonify({
                "error": f"容器部署没有桌面环境，请在宿主机打开：{hint}",
                "mode": "headless",
                "host_path": hint,
            }), 501

        if path.is_file():
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", resolved_path])
            elif sys.platform == "win32":
                subprocess.run(f'explorer /select,"{resolved_path}"', shell=True)
            else:
                subprocess.run(["xdg-open", str(path.parent.resolve())])
        else:
            if sys.platform == "darwin":
                subprocess.run(["open", resolved_path])
            elif sys.platform == "win32":
                try:
                    if hasattr(os, "startfile"):
                        os.startfile(resolved_path)
                    else:
                        subprocess.run(["explorer", resolved_path])
                except Exception:
                    subprocess.run(["explorer", resolved_path])
            else:
                subprocess.run(["xdg-open", resolved_path])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@articles_bp.route("/content-filter", methods=["GET"])
def get_content_filter():
    """获取内容过滤规则（屏蔽广告、二维码、小节标题横幅图等）"""
    from backend.content_filter import DEFAULT_RULES, last_stats, load_rules

    return jsonify({
        "rules": load_rules(),
        "defaults": DEFAULT_RULES,
        "last_stats": last_stats(),
    })


@articles_bp.route("/content-filter", methods=["POST"])
def save_content_filter():
    """保存内容过滤规则，新下载的文章立即生效"""
    from backend.content_filter import save_rules

    data = request.get_json() or {}
    rules = data.get("rules") if isinstance(data.get("rules"), dict) else data
    return jsonify({"rules": save_rules(rules), "message": "规则已保存"})


@articles_bp.route("/content-filter/apply-existing", methods=["POST"])
def apply_content_filter_existing():
    """对已下载文章重跑过滤。

    默认 dry_run=true 只统计不改文件；确认后传 {"dry_run": false} 才真正改写，
    改写前会把原 .html 备份成 .html.bak。
    """
    from backend.content_filter import refilter_existing

    data = request.get_json() or {}
    settings = get_settings()
    download_dir = settings.get("download_dir") or str(OUTPUT_DIR)
    result = refilter_existing(
        download_dir,
        dry_run=bool(data.get("dry_run", True)),
        force=bool(data.get("force", False)),
    )
    return jsonify(result)


@articles_bp.route("/serve-file", methods=["GET"])
@articles_bp.route("/serve-file/<path:filepath>", methods=["GET"])
def serve_file(filepath=""):
    """
    Serve a file from the configured download directory or OUTPUT_DIR.
    Supports ?path=<abs_or_rel_path> or /serve-file/<path:filepath>.
    Auto-detects .html inside directory when folder path is provided or if filename differs slightly.
    """
    from flask import send_from_directory, Response
    from urllib.parse import unquote
    settings = get_settings()
    download_dir = Path(settings.get("download_dir") or str(OUTPUT_DIR)).resolve()
    default_out_dir = OUTPUT_DIR.resolve()
    data_dir = DATA_DIR.resolve()

    allowed_roots = [download_dir, default_out_dir]
    safe_extensions = {
        ".html", ".htm", ".md", ".pdf",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
        ".mp4", ".mp3", ".wav", ".m4a",
        ".css", ".js",
    }

    query_path = request.args.get("path", "").strip()
    raw_path_str = query_path or filepath
    if not raw_path_str:
        return "File not found", 404

    decoded_path_str = unquote(raw_path_str)

    def is_safe_path(p: Path) -> bool:
        try:
            p_res = p.resolve()
            # 严禁直接访问 data 根目录下的系统凭据与配置文件（如 accounts.json, app_settings.json, *.db 等）
            if p_res == data_dir or p_res.parent == data_dir:
                return False
            return any(
                p_res.is_relative_to(root)
                for root in allowed_roots
                if str(root) != "/" and root.exists()
            )
        except (ValueError, OSError):
            return False

    def find_html_in_dir(d: Path) -> Path | None:
        if not d.exists() or not d.is_dir():
            return None
        candidates = [f for f in d.glob("*.html") if not f.name.endswith("_raw.html")]
        if candidates:
            for c in candidates:
                if c.stem == d.name:
                    return c
            return candidates[0]
        all_html = list(d.glob("*.html"))
        if all_html:
            return all_html[0]
        return None

    target_path = None

    # 1. 尝试直接作为绝对路径
    candidate_direct = Path(decoded_path_str)
    if candidate_direct.is_absolute() and candidate_direct.exists():
        if is_safe_path(candidate_direct):
            if candidate_direct.is_dir():
                target_path = find_html_in_dir(candidate_direct)
            else:
                target_path = candidate_direct

    # 2. 尝试在 download_dir 或 default_out_dir 下查找
    if not target_path or not target_path.exists():
        for base_dir in [download_dir, default_out_dir]:
            cand = (base_dir / decoded_path_str).resolve()
            if is_safe_path(cand):
                if cand.is_file():
                    target_path = cand
                    break
                elif cand.is_dir():
                    found = find_html_in_dir(cand)
                    if found:
                        target_path = found
                        break
                elif cand.parent.is_dir():
                    found = find_html_in_dir(cand.parent)
                    if found:
                        target_path = found
                        break

    # 3. 尝试模糊跨账号查找（防止相对路径中缺少公众号目录）
    if not target_path or not target_path.exists():
        pure_name = Path(decoded_path_str).name
        if pure_name.endswith(".html"):
            pure_name = pure_name[:-5]
        for base_dir in [download_dir, default_out_dir]:
            if base_dir.exists():
                for acc_dir in base_dir.iterdir():
                    if acc_dir.is_dir() and acc_dir.name not in ("channels", "douyin_downloads", "xhs_downloads", "temp_uploads"):
                        sub_cand = acc_dir / pure_name
                        if sub_cand.is_dir() and is_safe_path(sub_cand):
                            found = find_html_in_dir(sub_cand)
                            if found:
                                target_path = found
                                break
            if target_path:
                break

    if not target_path or not target_path.exists():
        not_found_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 80vh; margin: 0; color: #555; text-align: center; }
        .icon { width: 56px; height: 56px; color: #f59e0b; margin-bottom: 16px; }
        h3 { color: #111; margin: 0 0 8px 0; font-size: 1.15rem; }
        p { margin: 0 0 16px 0; font-size: 0.9rem; max-width: 480px; line-height: 1.6; color: #666; }
    </style>
</head>
<body>
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"></circle>
        <line x1="12" y1="8" x2="12" y2="12"></line>
        <line x1="12" y1="16" x2="12.01" y2="16"></line>
    </svg>
    <h3>未找到本地文章正文文件 (File not found)</h3>
    <p>该文章本地内容可能因网络异常尚未下载完成、或已被清理。您可以点击右上角“原文链接”查看微信在线正文。</p>
</body>
</html>"""
        return Response(not_found_html, status=404, mimetype="text/html")

    if not is_safe_path(target_path) or target_path.suffix.lower() not in safe_extensions:
        return "Access denied", 403

    return send_from_directory(target_path.parent, target_path.name)




@articles_bp.route("/avatars/<filename>", methods=["GET"])
def get_account_avatar_file(filename):
    from backend.avatar_cache import avatar_response

    return avatar_response(filename)


@articles_bp.route("/rss", methods=["GET"])
@articles_bp.route("/rss/<account>", methods=["GET"])
def get_rss(account=None):
    """RSS 2.0 订阅输出。

    具体实现见 backend/rss_feed.py：
      - 每次请求只读一次 rss_articles.json 并建索引（原实现按账号重复全量解析）
      - 按发布时间倒序，支持 ?limit= ?offset= 截断（默认 50，上限 500）
      - ?full=0 只输出摘要，不读 content.txt
      - 进程内缓存 + ETag/304，阅读器轮询基本不产生 IO
      - 反向代理下按 X-Forwarded-* 或 rss_base_url 生成链接
    """
    from backend.rss_feed import rss_response

    return rss_response(request, account)
