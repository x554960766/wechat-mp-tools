"""
文章获取与管理模块
获取文章列表、搜索文章、管理下载任务
"""

import json
import time
import threading
import requests as req
import shutil
from flask import Blueprint, jsonify, request, Response
from datetime import datetime
from pathlib import Path

from backend.config import (
    CONFIG_FILE, BASE_URL, DEFAULT_HEADERS, OUTPUT_DIR,
    DOWNLOAD_HISTORY_FILE,
    load_json, save_json, get_settings, get_proxies_dict, report_proxy_status,
    normalize_wechat_url, get_default_wechat_ua
)
from backend.account_pool import borrow_session, account_pool

articles_bp = Blueprint("articles", __name__, url_prefix="/api/articles")

# 下载进度管理
_download_tasks = {}
_download_lock = threading.Lock()


def _get_session():
    """获取凭证（通过账号池）"""
    account_id, token, cookie_str = borrow_session()
    return token, cookie_str


def fetch_article_detail_content(url: str) -> str:
    """抓取微信公众号文章网页并提取 HTML 正文（.rich_media_content），还原图片 data-src"""
    import bs4
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = req.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")

    soup = bs4.BeautifulSoup(resp.text, "html.parser")
    content_node = soup.select_one(".rich_media_content") or soup.select_one("#js_content")
    if not content_node:
        return resp.text

    for img in content_node.find_all("img"):
        if img.get("data-src"):
            img["src"] = img["data-src"]
            del img["data-src"]

    return str(content_node)


def _fetch_articles_via_appmsg_fallback(fakeid: str, begin: int, count: int, keyword: str, token: str, cookie_str: str):
    """当拥有可用 Web 管理端 Token 时，尝试通过 /cgi-bin/appmsg 备用通道获取文章列表"""
    if not token or not str(token).isdigit():
        return None
    import html
    headers = {**DEFAULT_HEADERS, "Cookie": cookie_str}
    try:
        s = req.Session()
        s.trust_env = False
        resp = s.get(
            f"{BASE_URL}/cgi-bin/appmsg",
            params={
                "action": "list_ex",
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": "1",
                "type": "9",
                "query": keyword,
                "fakeid": fakeid,
                "begin": str(begin),
                "count": str(count),
            },
            headers=headers,
            timeout=3,
            verify=False,
        )
        if resp.status_code == 200:
            try:
                resp_text = resp.content.decode("utf-8", errors="replace") if hasattr(resp, "content") and resp.content else (resp.text or "")
                data = json.loads(resp_text)
            except Exception:
                data = {}
            if data.get("base_resp", {}).get("ret") == 0:
                articles = []
                for item in data.get("app_msg_list", []):
                    link = html.unescape(item.get("link", "")).strip()
                    articles.append({
                        "title": item.get("title", ""),
                        "link": link,
                        "cover": item.get("cover", ""),
                        "digest": item.get("digest", ""),
                        "author": item.get("author_name", ""),
                        "update_time": item.get("update_time", item.get("create_time", 0)),
                        "is_original": False,
                        "item_show_type": item.get("item_show_type", 0),
                        "id": str(item.get("aid", "")),
                    })
                total_cnt = data.get("app_msg_cnt", len(articles))
                can_continue = 1 if (begin + len(articles)) < total_cnt else 0
                return articles, total_cnt, can_continue
    except Exception as e:
        print(f"appmsg fallback 尝试跳过: {e}", flush=True)
    return None


def _enqueue_biz_refresh(fakeid: str, name: str, reason: str) -> None:
    """把凭证过期的公众号加入刷新队列（自动跳过 + 及时补凭证，下轮采集补齐数据）。"""
    try:
        from backend.refresh_queue import refresh_queue
        refresh_queue.enqueue(biz=fakeid, name=name, reason=reason)
    except Exception as e:
        print(f"[_fetch_articles_page] 刷新队列入队失败: {e}", flush=True)


def _fetch_articles_page(fakeid: str, begin: int, count: int, keyword: str = "", account_name: str = "") -> tuple:
    """使用微信客户端历史消息原生接口 (profile_ext?action=getmsg) 获取文章列表 (articles, total_count, can_continue)
    支持从账号池提取 appmsg_token, key, pass_ticket, uin 与 Cookie 进行翻页抓取
    凭证过期时自动跳过并把该公众号加入刷新队列（及时补凭证，下轮采集补齐数据）"""
    max_switch = 2
    last_exc = None

    for _ in range(max_switch):
        # biz 感知选号：优先持有该公众号专属凭证的账号，避免多账号池下选错账号导致 ret=-3
        account_data = account_pool.acquire_for_biz(fakeid) or account_pool.acquire()
        if not account_data:
            if last_exc is not None:
                break
            raise RuntimeError("账号池中无可用账号，请先在『账号池』页面添加/登录账号")

        account_id = account_data["id"]
        from backend.account_pool import AccountPool
        biz_cred = AccountPool.get_biz_credential(account_data, fakeid)

        token = biz_cred.get("token", "")
        appmsg_token = biz_cred.get("appmsg_token", token)
        cookie_str = biz_cred.get("cookie_str") or account_data.get("cookie_str", "")
        uin = biz_cred.get("uin") or account_data.get("uin", "")
        # 严格 biz 隔离：key / pass_ticket 只用该公众号自己的会话凭证，
        # 不回退账号级字段（账号级 key 属于最近打开的其他公众号，跨号使用必被拒绝）
        key = biz_cred.get("key", "")
        pass_ticket = biz_cred.get("pass_ticket", "")
        poc_token = biz_cred.get("poc_token", "")
        poc_sid = biz_cred.get("poc_sid", "")
        wxtoken = biz_cred.get("wxtoken", "777")

        if not token and not key:
            # 该公众号尚未建立独立阅读会话：不发起注定失败的请求。
            # 优先全池查找公众平台 Web Token（纯数字）——客户端通道被封锁时这是唯一 API 恢复路径
            web_token, web_cookie, _web_id = account_pool.get_web_token_session()
            if web_token:
                try:
                    fallback_res = _fetch_articles_via_appmsg_fallback(fakeid, begin, count, keyword, web_token, web_cookie)
                    if fallback_res is not None:
                        articles, total_cnt, can_continue = fallback_res
                        account_pool.report(account_id, ret=0)
                        return articles, total_cnt, can_continue
                except Exception as fb_err:
                    print(f"Appmsg 备用通道尝试失败: {fb_err}", flush=True)
            account_pool.report(account_id, ret=-3, error="该公众号尚未建立独立阅读会话（无专属凭证），已加入刷新队列")
            _enqueue_biz_refresh(fakeid, account_name or keyword, "无会话凭证")
            last_exc = PermissionError(f"当前公众号【{account_name or fakeid}】尚未建立独立主页会话：已加入自动刷新队列，请在电脑微信中打开该公众号主页建立会话！")
            continue

        import urllib.parse, re
        if appmsg_token:
            appmsg_token = urllib.parse.unquote(str(appmsg_token))
        if pass_ticket:
            pass_ticket = urllib.parse.unquote(str(pass_ticket))
        if key:
            key = urllib.parse.unquote(str(key))
        if poc_token:
            poc_token = urllib.parse.unquote(str(poc_token))

        # 规范化 Cookie 分隔符，并保证 pass_ticket 中的 + 编码为 %2B 防止被微信服务端解析为空格
        clean_cookie = cookie_str.replace(", ", "; ")
        if "pass_ticket=" in clean_cookie:
            clean_cookie = re.sub(r'pass_ticket=([^;,\s]+)', lambda m: 'pass_ticket=' + m.group(1).replace('+', '%2B'), clean_cookie)
        if poc_sid and "poc_sid=" not in clean_cookie:
            clean_cookie = f"{clean_cookie}; poc_sid={poc_sid}" if clean_cookie else f"poc_sid={poc_sid}"

        ua = biz_cred.get("user_agent") or account_data.get("user_agent") or get_default_wechat_ua()
        headers = {
            "User-Agent": ua,
            "Cookie": clean_cookie,
            "Accept": "application/json, text/plain, */*",
        }
        proxies = get_proxies_dict()
        proxy_url = proxies.get("http") if proxies else None

        import base64, urllib.parse
        uin_str = urllib.parse.unquote(str(uin)).strip() if uin else ""
        if uin_str.endswith("==") or (len(uin_str) >= 12 and uin_str.isalnum()):
            try:
                decoded = base64.b64decode(uin_str).decode('utf-8', errors='ignore').strip()
                if decoded.isdigit():
                    uin_str = decoded
            except Exception:
                pass
        if uin_str and uin_str.isdigit():
            uin_encoded = base64.b64encode(uin_str.encode()).decode()
        else:
            uin_encoded = uin_str

        params = {
            "action": "getmsg",
            "__biz": fakeid,
            "f": "json",
            "offset": str(begin),
            "count": str(count),
            "is_ok": "1",
            "scene": "124",
            "uin": uin_encoded,
            "key": str(key) if key else "",
            "pass_ticket": str(pass_ticket) if pass_ticket else "",
            "wxtoken": str(wxtoken) if wxtoken else "777",
            "poc_token": str(poc_token) if poc_token else "",
            "x5": "0",
        }

        from backend.cred_redact import mask_secret
        print(f"[_fetch_articles_page] Sending HTTP GET to profile_ext for fakeid={fakeid} (uin={mask_secret(uin, 4)} key={'yes' if key else 'no'})...", flush=True)
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            s = req.Session()
            s.trust_env = False
            try:
                resp = s.get(
                    f"{BASE_URL}/mp/profile_ext",
                    params=params,
                    headers=headers,
                    timeout=8,
                    verify=False,
                )
                print(f"[_fetch_articles_page] profile_ext responded with HTTP {resp.status_code}", flush=True)
            except Exception as s_err:
                print(f"[_fetch_articles_page] s.get failed: {s_err}, trying curl_cffi...", flush=True)
                from curl_cffi import requests as c_req
                resp = c_req.get(
                    f"{BASE_URL}/mp/profile_ext",
                    params=params,
                    headers=headers,
                    timeout=8,
                    impersonate="chrome",
                    verify=False,
                )
                print(f"[_fetch_articles_page] c_req.get responded with HTTP {resp.status_code}", flush=True)
        except Exception as exc:
            print(f"[_fetch_articles_page] Both requests engines failed: {exc}", flush=True)
            report_proxy_status(proxy_url, success=False)
            account_pool.report(account_id, http_ok=False, error=str(exc))
            last_exc = exc
            continue

        if resp.status_code != 200:
            report_proxy_status(proxy_url, success=False)
            account_pool.report(account_id, http_ok=False, error=f"HTTP {resp.status_code}")
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
            continue

        report_proxy_status(proxy_url, success=True)
        resp_text = ""
        try:
            resp_text = resp.content.decode("utf-8", errors="replace") if hasattr(resp, "content") and resp.content else (resp.text or "")
            data = json.loads(resp_text)
        except Exception:
            # 响应护栏：微信在凭证被拒绝时会以 HTTP 200 返回 HTML 验证页/环境异常页，而非接口 JSON。
            sniff = resp_text[:3000] if resp_text else ""
            if any(kw in sniff for kw in ("环境异常", "去验证", "操作验证", "weui-msg", "wx_alert", "当前环境异常")):
                print(f"[_fetch_articles_page] 命中验证页护栏: 返回 HTML 验证页而非 JSON，凭证被服务端拒绝", flush=True)
                account_pool.report(account_id, ret=200003, error="返回验证页(环境异常)，客户端凭证已被微信拒绝")
                _enqueue_biz_refresh(fakeid, account_name or keyword, "验证页")
                last_exc = PermissionError("微信返回验证页（环境异常），当前凭证已被拒绝，已加入刷新队列，将在下轮采集自动重试！")
            else:
                account_pool.report(account_id, http_ok=True, error="返回数据非 JSON 格式")
                last_exc = RuntimeError("返回数据非 JSON 格式（可能需要重新在微信电脑版打开历史消息更新 key/token）")
            continue

        ret = data.get("ret", 0)

        if ret == 0:
            account_pool.report(account_id, ret=0)
        else:
            errmsg = data.get("errmsg", f"ret={ret}")

            # 客户端接口失败：优先全池查找公众平台 Web Token（纯数字）走 /cgi-bin/appmsg 备用通道
            web_token, web_cookie, _web_id = account_pool.get_web_token_session()
            if not web_token and str(appmsg_token or token).isdigit():
                web_token, web_cookie = (appmsg_token or token), cookie_str
            if web_token:
                try:
                    fallback_res = _fetch_articles_via_appmsg_fallback(fakeid, begin, count, keyword, web_token, web_cookie)
                    if fallback_res is not None:
                        articles, total_cnt, can_continue = fallback_res
                        account_pool.report(account_id, ret=0)
                        return articles, total_cnt, can_continue
                except Exception as fb_err:
                    print(f"Appmsg 备用通道尝试失败: {fb_err}")

            account_pool.report(account_id, ret=ret)

            if ret in (-3, -4, -5, -6, 200003):
                _enqueue_biz_refresh(fakeid, account_name or keyword, f"ret={ret}")
                hint = ""
                if not biz_cred.get("getmsg_ready"):
                    hint = "（当前捕获的是文章页凭证，不支持拉取列表；需打开该公众号『主页』以建立列表会话）"
                last_exc = PermissionError(f"当前公众号【{account_name or fakeid}】凭证未就绪或已过期 (ret={ret}, {errmsg}){hint}，已加入刷新队列，下轮采集自动重试！")
            elif ret == 200013 or "操作频繁" in str(errmsg):
                last_exc = RuntimeError("触发微信频次控制(200013: 操作频繁)，账号已自动进入冷却避让状态，请稍后再试！")
            else:
                last_exc = RuntimeError(f"微信历史消息接口错误: {errmsg}")
            break

        articles = []
        msg_list_str = data.get("general_msg_list", "")
        if msg_list_str:
            try:
                import html
                msg_data = json.loads(msg_list_str)
                for msg in msg_data.get("list", []):
                    comm_info = msg.get("comm_msg_info", {})
                    pub_time = comm_info.get("datetime", 0)
                    msg_id = str(comm_info.get("id", ""))

                    app_msg = msg.get("app_msg_ext_info", {})
                    if app_msg and app_msg.get("title"):
                        link = html.unescape(app_msg.get("content_url", "")).replace("\\/", "/").strip()
                        if link.startswith("//"):
                            link = "https:" + link

                        articles.append({
                            "title": app_msg.get("title", ""),
                            "link": link,
                            "cover": app_msg.get("cover", ""),
                            "digest": app_msg.get("digest", ""),
                            "author": app_msg.get("author", ""),
                            "update_time": pub_time,
                            "is_original": False,
                            "item_show_type": 0,
                            "id": msg_id,
                        })

                        # 多图文处理
                        for sub in app_msg.get("multi_app_msg_item_list", []):
                            if sub.get("title"):
                                sub_link = html.unescape(sub.get("content_url", "")).replace("\\/", "/").strip()
                                if sub_link.startswith("//"):
                                    sub_link = "https:" + sub_link
                                articles.append({
                                    "title": sub.get("title", ""),
                                    "link": sub_link,
                                    "cover": sub.get("cover", ""),
                                    "digest": sub.get("digest", ""),
                                    "author": sub.get("author", ""),
                                    "update_time": pub_time,
                                    "is_original": False,
                                    "item_show_type": 0,
                                    "id": msg_id,
                                })
            except Exception as parse_err:
                print(f"解析 general_msg_list 异常: {parse_err}")

        total_cnt = data.get("total_count", len(articles))
        can_continue = data.get("can_msg_continue", 1) if isinstance(data, dict) else (1 if len(articles) > 0 else 0)
        return articles, total_cnt, can_continue

    if isinstance(last_exc, PermissionError):
        raise last_exc
    raise last_exc or RuntimeError("账号池所有账号均不可用")


@articles_bp.route("/list/<fakeid>", methods=["GET"])
def get_articles(fakeid):
    """获取指定公众号的文章列表"""
    begin = request.args.get("begin", 0, type=int)
    count = request.args.get("count", 10, type=int)
    keyword = request.args.get("keyword", "").strip()

    t_start = time.time()
    print(f"[Articles API] Start fetching articles for fakeid={fakeid} begin={begin} count={count}", flush=True)

    try:
        articles, total_count, can_continue = _fetch_articles_page(fakeid, begin, count, keyword, account_name=keyword)
        print(f"[Articles API] Fetch finished in {time.time() - t_start:.2f}s, got {len(articles)} articles", flush=True)

        return jsonify({
            "articles": articles,
            "total": total_count,
            "can_msg_continue": can_continue,
            "begin": begin,
            "count": len(articles),
        })

    except PermissionError as e:
        print(f"[Articles API] PermissionError in {time.time() - t_start:.2f}s: {e}", flush=True)
        return jsonify({"error": str(e)}), 401
    except (RuntimeError, req.RequestException) as e:
        print(f"[Articles API] Error in {time.time() - t_start:.2f}s: {e}", flush=True)
        return jsonify({"error": f"网络请求失败: {str(e)}"}), 500


@articles_bp.route("/list-via-appmsg/<fakeid>", methods=["GET"])
def get_articles_appmsg(fakeid):
    """使用 appmsg 接口获取文章列表（备选方案）"""
    begin = request.args.get("begin", 0, type=int)
    count = request.args.get("count", 10, type=int)

    try:
        account_id, token, cookie_str = borrow_session()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    proxy_url = None
    try:
        headers = {**DEFAULT_HEADERS, "Cookie": cookie_str}
        proxies = get_proxies_dict()
        if proxies:
            proxy_url = proxies.get("http")

        resp = req.get(
            f"{BASE_URL}/cgi-bin/appmsg",
            params={
                "action": "list_ex",
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": "1",
                "type": "9",
                "query": "",
                "fakeid": fakeid,
                "begin": str(begin),
                "count": str(count),
            },
            headers=headers,
            proxies=proxies,
            timeout=25,
        )

        if resp.status_code != 200:
            report_proxy_status(proxy_url, success=False)
            account_pool.report(account_id, http_ok=False, error=f"HTTP {resp.status_code}")
            return jsonify({"error": f"HTTP {resp.status_code}"}), 500

        report_proxy_status(proxy_url, success=True)
        data = resp.json()
        ret = data.get("base_resp", {}).get("ret", 0)

        # 上报账号池
        account_pool.report(account_id, ret=ret)

        if ret == 200003:
            return jsonify({"error": "登录已过期"}), 401
        if ret != 0:
            return jsonify({"error": f"API错误 (ret={ret})"}), 500

        articles = []
        for item in data.get("app_msg_list", []):
            articles.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "cover": item.get("cover", ""),
                "digest": item.get("digest", ""),
                "author": item.get("author_name", ""),
                "update_time": item.get("update_time", item.get("create_time", 0)),
                "aid": item.get("aid", ""),
                "item_show_type": item.get("item_show_type", 0),
            })

        return jsonify({
            "articles": articles,
            "total": data.get("app_msg_cnt", 0),
            "begin": begin,
            "count": len(articles),
        })

    except req.RequestException as e:
        report_proxy_status(proxy_url, success=False)
        account_pool.report(account_id, http_ok=False, error=str(e))
        return jsonify({"error": f"网络请求失败: {str(e)}"}), 500


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
    """通过 URL 下载单篇文章"""
    data = request.get_json() or {}
    urls = data.get("urls", [])
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split("\n") if u.strip()]

    if not urls:
        return jsonify({"error": "请输入文章 URL"}), 400

    # 构造文章列表
    articles = [{"title": f"article_{i+1}", "link": url} for i, url in enumerate(urls)]

    task_id = f"url_{int(time.time())}"

    with _download_lock:
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
        args=(task_id, articles, "url_download"),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "message": f"已启动下载任务，共 {len(urls)} 篇"})


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
    """自动扫描本地下载文件夹，同步历史记录中缺失的条目"""
    settings = get_settings()
    download_dir = Path(settings.get("download_dir") or str(OUTPUT_DIR))
    if not download_dir.exists():
        return False

    existing_paths = {item.get("path") for item in history if isinstance(item, dict) and item.get("path")}
    changed = False

    try:
        for account_dir in download_dir.iterdir():
            if not account_dir.is_dir() or account_dir.name in ("channels", "douyin_downloads", "xhs_downloads", "temp_uploads"):
                continue

            for art_dir in account_dir.iterdir():
                if not art_dir.is_dir():
                    continue

                art_path_str = str(art_dir)
                if art_path_str in existing_paths:
                    continue

                meta_file = art_dir / "metadata.json"
                if not meta_file.exists():
                    continue

                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
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


@articles_bp.route("/history", methods=["GET"])
def get_history():
    """获取下载历史"""
    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    
    # 自动从磁盘同步丢失的历史记录
    if _sync_history_from_disk(history):
        save_json(DOWNLOAD_HISTORY_FILE, history)

    indexed_history = []
    for index, item in enumerate(history):
        if isinstance(item, dict):
            if item.get("account") == "微信视频号":
                continue
            indexed = dict(item)
            indexed["_index"] = index
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

    out_dir = OUTPUT_DIR / account_name
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

            link = article.get("link", "")
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

            for attempt in range(1, max_retries + 1):
                try:
                    result = download_single_article(link, out_dir, title)
                    if result.get("success"):
                        success = True
                        break
                    error_msg = result.get("error", "未知错误")
                except Exception as e:
                    error_msg = str(e)
                time.sleep(1)

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

            # 记录到下载历史
            history.append({
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
            })

            if i < len(articles) - 1:
                time.sleep(delay)

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


def _download_article_into_task(task_id: str, article: dict, account_name: str, history: list, index: int = 0):
    """Download one article and update task progress."""
    from backend.downloader import download_single_article

    settings = get_settings()
    max_retries = settings.get("max_retries", 3)
    out_dir = OUTPUT_DIR / account_name
    out_dir.mkdir(parents=True, exist_ok=True)

    link = article.get("link", "")
    title = article.get("title", f"article_{index + 1}")

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
    for _ in range(max_retries):
        try:
            result = download_single_article(link, out_dir, title)
            if result.get("success"):
                success = True
                break
            error_msg = result.get("error", "未知错误")
        except Exception as e:
            error_msg = str(e)
        time.sleep(1)

    if result.get("title"):
        title = result["title"]
    downloaded_path = result.get("path") if success else None

    with _download_lock:
        if success:
            _download_tasks[task_id]["completed"] += 1
            _download_tasks[task_id]["results"].append({
                "title": title, "success": True, "path": downloaded_path or str(out_dir / title)
            })
        else:
            _download_tasks[task_id]["failed"] += 1
            _download_tasks[task_id]["results"].append({
                "title": title, "success": False, "error": error_msg
            })

    history.append({
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
    })


def _do_range_download(
    task_id: str,
    fakeid: str,
    account_name: str,
    start_time: int,
    end_time: int,
    keyword: str,
    page_size: int,
):
    """分页拉取文章，下载时间范围内文章，遇到更早文章后停止。"""
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
                # update_time 为 0 表示时间未知，不做时间过滤（宁多勿漏）
                if article_time > 0:
                    if article_time > end_time:
                        continue
                    if article_time < start_time:
                        out_of_range_count += 1
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
                # 增加防风控随机抖动延迟 (1.2s - 2.5s) 模拟人类请求
                import random
                sleep_time = max(1.0, delay) + random.uniform(0.3, 1.2)
                time.sleep(sleep_time)

            # 当前页所有文章都早于 start_time，说明后续页也不会有范围内的文章了
            if out_of_range_count > 0 and out_of_range_count >= len(articles):
                stop = True
                with _download_lock:
                    _download_tasks[task_id]["stop_reason"] = "已到达所选时间范围之前的文章"

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


@articles_bp.route("/open-folder", methods=["POST"])
def open_folder():
    """在系统文件管理器中打开微信下载目录"""
    import subprocess
    import sys
    
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
        if sys.platform == "darwin":
            subprocess.run(["open", resolved_path])
        elif sys.platform == "win32":
            os.startfile(resolved_path)
        else:
            subprocess.run(["xdg-open", resolved_path])
        return jsonify({"message": "文件夹已打开"})
    except Exception as e:
        return jsonify({"error": f"打开文件夹失败: {str(e)}"}), 500


@articles_bp.route("/open-file", methods=["POST"])
def open_file():
    """在系统默认程序中打开特定的文件或文件夹，支持按类型定位 pdf/md"""
    import subprocess
    import sys

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
        if sys.platform == "darwin":
            subprocess.run(["open", resolved_path])
        elif sys.platform == "win32":
            os.startfile(resolved_path)
        else:
            subprocess.run(["xdg-open", resolved_path])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@articles_bp.route("/open-parent", methods=["POST"])
def open_parent():
    """打开文件所在的父目录并选中当前文件"""
    import subprocess
    import sys

    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400

    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件或文件夹不存在"}), 404
            
        resolved_path = str(path.resolve())
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
                os.startfile(resolved_path)
            else:
                subprocess.run(["xdg-open", resolved_path])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@articles_bp.route("/serve-file/<path:filepath>", methods=["GET"])
def serve_file(filepath):
    """
    Serve a file from the configured download directory.
    """
    from flask import send_from_directory
    from urllib.parse import unquote
    settings = get_settings()
    download_dir = Path(settings.get("download_dir") or str(OUTPUT_DIR))
    
    try:
        # Resolve target path safely
        target_path = (download_dir / unquote(filepath)).resolve()
        
        # Check exists
        if not target_path.exists():
            return "File not found", 404
            
        # Security check: ensure path is within download_dir
        if not str(target_path).startswith(str(download_dir.resolve())):
            return "Access denied", 403
            
        return send_from_directory(target_path.parent, target_path.name)
    except Exception as e:
        return f"Error serving file: {str(e)}", 500


@articles_bp.route("/rss", methods=["GET"])
@articles_bp.route("/rss/<account>", methods=["GET"])
def get_rss(account=None):
    """
    Generate an RSS 2.0 feed of successfully downloaded articles.
    Merges manually downloaded articles with auto-fetched RSS subscription articles.
    """
    import email.utils
    from urllib.parse import quote
    import html

    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    # Filter successful downloads and exclude channels
    items = [item for item in history if isinstance(item, dict) and item.get("success") and item.get("account") != "微信视频号"]

    # Filter by account if specified
    if account:
        items = [item for item in items if item.get("account") == account]
        feed_title = f"微信公众号 - {account} RSS"
        feed_desc = f"{account} 微信公众号文章订阅"
    else:
        feed_title = "微信公众号 RSS"
        feed_desc = "微信公众号文章订阅"

    # Merge auto-fetched RSS articles
    try:
        from backend.rss_scheduler import rss_scheduler
        if account:
            rss_arts = rss_scheduler.get_articles(account)
        else:
            # Get articles from all subscriptions
            rss_arts = []
            for sub in rss_scheduler.get_subscriptions():
                rss_arts.extend(rss_scheduler.get_articles(sub.get("nickname", "")))

        # Convert auto-fetched articles to the same format, dedup by link
        existing_links = {item.get("link") for item in items if item.get("link")}
        for art in rss_arts:
            link = art.get("link", "")
            if link and link not in existing_links:
                items.append({
                    "title": art.get("title", ""),
                    "link": link,
                    "account": account or art.get("author", ""),
                    "success": True,
                    "time": art.get("update_time", 0),
                    "publish_time": art.get("update_time", 0),
                    "cover_url": art.get("cover", ""),
                    "digest": art.get("digest", ""),
                    "path": art.get("path", ""),
                })
                existing_links.add(link)
    except Exception:
        pass  # If scheduler not available, just use download history
        
    # Get server host/port dynamically to construct local URLs
    host_url = request.host_url
    
    xml_items = []
    for item in items:
        # Defensively convert all values to strings to prevent AttributeError on html.escape(None)
        title_val = item.get("title") or ""
        title = html.escape(str(title_val))
        
        acc_name_val = item.get("account") or "unknown"
        acc_name = str(acc_name_val)
        
        safe_title = item.get("title") or ""
        
        # Check if local HTML file exists
        local_exists = False
        path_str = item.get("path")
        if path_str:
            try:
                local_path = Path(path_str)
                if local_path.exists():
                    local_exists = True
            except Exception:
                pass

        orig_url_val = item.get("link") or ""
        orig_url = html.escape(str(orig_url_val))

        if local_exists:
            p_obj = Path(path_str)
            local_url = f"{host_url}api/articles/serve-file/{quote(p_obj.parent.name)}/{quote(p_obj.name)}/{quote(p_obj.name)}.html"
            xml_link = html.escape(local_url)
        else:
            xml_link = orig_url
        
        pub_time_val = item.get("publish_time") or item.get("time") or time.time()
        try:
            pub_time = float(pub_time_val)
        except Exception:
            pub_time = time.time()
        pub_date = email.utils.formatdate(pub_time, usegmt=True)
        
        digest_val = item.get("digest") or ""
        digest = html.escape(str(digest_val))
        
        cover_url_val = item.get("cover_url") or ""
        cover_url = html.escape(str(cover_url_val))
        
        # Read content.txt if available for full text
        content_encoded = ""
        if path_str:
            try:
                txt_path = Path(path_str) / "content.txt"
                if txt_path.exists():
                    clean_text = txt_path.read_text(encoding="utf-8")
                    clean_text = clean_text.replace("]]>", "]]&gt;")
                    content_encoded = f"<content:encoded><![CDATA[{clean_text}]]></content:encoded>"
            except Exception:
                pass
                
        enclosure = f'<enclosure url="{cover_url}" type="image/jpeg" length="0"/>' if cover_url else ""
        
        xml_items.append(f"""    <item>
      <title>{title}</title>
      <link>{xml_link}</link>
      <guid isPermaLink="false">{orig_url or xml_link}</guid>
      <pubDate>{pub_date}</pubDate>
      <description>{digest}</description>
      {enclosure}
      {content_encoded}
    </item>""")
    
    items_str = "\n".join(xml_items)
    
    rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{html.escape(feed_title)}</title>
    <link>{host_url}</link>
    <description>{html.escape(feed_desc)}</description>
    <language>zh-CN</language>
    <lastBuildDate>{email.utils.formatdate(time.time(), usegmt=True)}</lastBuildDate>
{items_str}
  </channel>
</rss>"""

    return Response(rss_xml, mimetype="application/xml")
