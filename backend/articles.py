"""
文章获取与管理模块
获取文章列表、搜索文章、管理下载任务
"""

import json
import time
import threading
import requests as req
from flask import Blueprint, jsonify, request, Response
from datetime import datetime
from pathlib import Path

from backend.config import (
    CONFIG_FILE, BASE_URL, DEFAULT_HEADERS, OUTPUT_DIR,
    DOWNLOAD_HISTORY_FILE,
    load_json, save_json, get_settings, get_proxies_dict, report_proxy_status
)

articles_bp = Blueprint("articles", __name__, url_prefix="/api/articles")

# 下载进度管理
_download_tasks = {}
_download_lock = threading.Lock()


def _get_session():
    """获取凭证"""
    config = load_json(CONFIG_FILE)
    if not config or not config.get("token"):
        raise RuntimeError("未登录，请先扫码登录")
    return config["token"], config["cookie_str"]


@articles_bp.route("/list/<fakeid>", methods=["GET"])
def get_articles(fakeid):
    """获取指定公众号的文章列表"""
    begin = request.args.get("begin", 0, type=int)
    count = request.args.get("count", 10, type=int)
    keyword = request.args.get("keyword", "").strip()

    try:
        token, cookie_str = _get_session()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 401

    proxy_url = None
    try:
        headers = {**DEFAULT_HEADERS, "Cookie": cookie_str}
        proxies = get_proxies_dict()
        if proxies:
            proxy_url = proxies.get("http")

        # 使用 appmsgpublish 接口
        is_searching = bool(keyword)
        params = {
            "sub": "search" if is_searching else "list",
            "search_field": "7" if is_searching else "null",
            "begin": str(begin),
            "count": str(count),
            "query": keyword,
            "fakeid": fakeid,
            "type": "101_1",
            "free_publish_type": "1",
            "sub_action": "list_ex",
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }

        resp = req.get(
            f"{BASE_URL}/cgi-bin/appmsgpublish",
            params=params,
            headers=headers,
            proxies=proxies,
            timeout=30,
        )

        if resp.status_code != 200:
            report_proxy_status(proxy_url, success=False)
            return jsonify({"error": f"HTTP {resp.status_code}"}), 500

        report_proxy_status(proxy_url, success=True)
        data = resp.json()
        base_resp = data.get("base_resp", {})
        ret = base_resp.get("ret", 0)

        if ret == 200003:
            return jsonify({"error": "登录已过期，请重新扫码登录"}), 401
        if ret != 0:
            err_msg = base_resp.get("err_msg", "未知错误")
            return jsonify({"error": f"API错误 (ret={ret}): {err_msg}"}), 500

        # 解析文章列表
        articles, total_count = _parse_publish_response(data)

        return jsonify({
            "articles": articles,
            "total": total_count,
            "begin": begin,
            "count": len(articles),
        })

    except req.RequestException as e:
        report_proxy_status(proxy_url, success=False)
        return jsonify({"error": f"网络请求失败: {str(e)}"}), 500


@articles_bp.route("/list-via-appmsg/<fakeid>", methods=["GET"])
def get_articles_appmsg(fakeid):
    """使用 appmsg 接口获取文章列表（备选方案）"""
    begin = request.args.get("begin", 0, type=int)
    count = request.args.get("count", 10, type=int)

    try:
        token, cookie_str = _get_session()
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
            return jsonify({"error": f"HTTP {resp.status_code}"}), 500

        report_proxy_status(proxy_url, success=True)
        data = resp.json()
        ret = data.get("base_resp", {}).get("ret", 0)

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


@articles_bp.route("/history", methods=["GET"])
def get_history():
    """获取下载历史"""
    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    # 按时间倒序
    history.sort(key=lambda x: x.get("time", 0), reverse=True)
    # 限制返回数量
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"history": history[:limit], "total": len(history)})


@articles_bp.route("/history", methods=["DELETE"])
def clear_history():
    """清空下载历史"""
    save_json(DOWNLOAD_HISTORY_FILE, [])
    return jsonify({"message": "历史已清空"})


def _do_batch_download(task_id: str, articles: list, account_name: str):
    """执行批量下载（后台线程）"""
    from backend.downloader import download_single_article

    settings = get_settings()
    delay = settings.get("request_delay", 0.8)
    max_retries = settings.get("max_retries", 3)

    out_dir = OUTPUT_DIR / account_name
    out_dir.mkdir(parents=True, exist_ok=True)

    history = load_json(DOWNLOAD_HISTORY_FILE, [])

    for i, article in enumerate(articles):
        link = article.get("link", "")
        title = article.get("title", f"article_{i+1}")

        with _download_lock:
            _download_tasks[task_id]["current"] = title

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
                _download_tasks[task_id]["results"].append({
                    "title": title, "success": True, "path": downloaded_path or str(out_dir / title)
                })
            else:
                _download_tasks[task_id]["failed"] += 1
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
        })

        if i < len(articles) - 1:
            time.sleep(delay)

    # 保存历史
    save_json(DOWNLOAD_HISTORY_FILE, history)

    with _download_lock:
        task = _download_tasks[task_id]
        task["status"] = "completed"
        task["current"] = ""
        task["end_time"] = time.time()


@articles_bp.route("/open-folder", methods=["POST"])
def open_folder():
    """在系统文件管理器中打开微信下载目录"""
    import subprocess
    import sys
    
    settings = get_settings()
    download_dir_str = settings.get("download_dir", str(OUTPUT_DIR))
    path = Path(download_dir_str)
    
    try:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "文件夹已打开"})
    except Exception as e:
        return jsonify({"error": f"打开文件夹失败: {str(e)}"}), 500


@articles_bp.route("/open-file", methods=["POST"])
def open_file():
    """在系统默认程序中打开特定的文件或文件夹"""
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

        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500


@articles_bp.route("/open-parent", methods=["POST"])
def open_parent():
    """打开文件所在的父目录"""
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
            
        parent_path = path.parent if path.is_file() else path

        if sys.platform == "darwin":
            subprocess.run(["open", str(parent_path)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(parent_path)])
        else:
            subprocess.run(["xdg-open", str(parent_path)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500
