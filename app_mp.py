#!/usr/bin/env python3
"""
微信公众号 + RSS 独立服务

只注册公众号相关的蓝图：登录、账号、文章、下载、代理、RSS。
抖音 / 快手 / 小红书 / B 站 / 视频号 / 转码 / 自动更新等模块完全不导入，
因此第三方依赖从 10+ 个降到 4 个，镜像可以做得很小。

启动：
    python app_mp.py --host 0.0.0.0 --port 5200

与 app.py 的关系：
    app.py 是桌面版的完整入口（含所有平台）；本文件是它的子集，
    只用于「公众号 + RSS」的服务端部署。两者共用 backend/ 代码，不重复实现。
"""

import argparse
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from backend.runtime import configure_runtime, resource_dir

configure_runtime()

from flask_cors import CORS

from backend.account_pool import migrate_legacy_config
from backend.account_pool_api import account_pool_bp
from backend.accounts import accounts_bp
from backend.articles import articles_bp
from backend.auth import auth_bp
from backend.config import APP_VERSION, ensure_dirs, get_settings, save_settings, init_admin_auth
from backend.proxy import proxy_bp

ENABLED_MODULES = [
    "公众号登录（微信读书扫码）",
    "公众号账号与文章列表",
    "文章下载 / 导出（HTML / Markdown / PDF）",
    "RSS 订阅与定时增量抓取",
    "代理配置",
]

app = Flask(
    __name__,
    static_folder=str(resource_dir() / "frontend"),
    static_url_path="",
)
CORS(app)
init_admin_auth(app)

app.register_blueprint(auth_bp)
app.register_blueprint(accounts_bp)
app.register_blueprint(articles_bp)
app.register_blueprint(account_pool_bp)
app.register_blueprint(proxy_bp)

# ── AI 媒体服务反代（必须在 SPA 兜底 /<path:path> 之前注册）──
from backend.media_host import MediaHost, init_blueprint
media_host = MediaHost()
app.config["MEDIA_HOST_OBJ"] = media_host
app.register_blueprint(init_blueprint(app))
media_host.start_in_thread()

ensure_dirs()
migrate_legacy_config()

# RSS 定时抓取（后台线程，失败不影响服务启动）
try:
    from backend.rss_scheduler import rss_scheduler

    rss_scheduler.start()
except Exception as exc:  # noqa: BLE001
    print(f"[warn] RSS 调度器启动失败：{exc}", file=sys.stderr)


# ── 基础接口 ──────────────────────────────────────────────

@app.route("/healthz")
def healthz():
    return jsonify({
        "status": "ok",
        "version": APP_VERSION,
        "modules": len(ENABLED_MODULES),
        "media": media_host.status(),
    })


@app.route("/api/capabilities")
def capabilities():
    """告诉前端/调用方当前部署启用了哪些能力。"""
    return jsonify({
        "mode": "mp-only",
        "version": APP_VERSION,
        "enabled": ENABLED_MODULES,
        "disabled": ["抖音", "快手", "小红书", "哔哩哔哩", "微信视频号", "字幕擦除", "客户端自动更新"],
    })


@app.route("/api/settings", methods=["GET"])
def read_settings():
    return jsonify(get_settings())


@app.route("/api/settings", methods=["POST"])
def update_settings():
    from flask import request

    data = request.get_json() or {}
    settings = get_settings()
    settings.update(data)
    save_settings(settings)
    return jsonify({"message": "设置已保存"})


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.errorhandler(404)
def handle_404(err):
    """未注册的 API 返回可读提示，而不是 HTML 404。"""
    from flask import request

    if request.path.startswith("/api/"):
        return jsonify({
            "error": "当前部署仅包含公众号与 RSS 功能，该接口未启用",
            "path": request.path,
        }), 501
    index = Path(app.static_folder) / "index.html"
    if index.exists():
        return send_from_directory(app.static_folder, "index.html")
    return jsonify({"error": "前端资源缺失，请确认 frontend/ 已打包进镜像"}), 500


@app.route("/")
def serve_index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    target = Path(app.static_folder) / path
    if target.is_file():
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


def main():
    parser = argparse.ArgumentParser(description="微信公众号 + RSS 服务")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5200)))
    parser.add_argument("--host", type=str, default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--debug", action="store_true")
    # 与 app.py 保持同一套启动参数，容器入口脚本可以共用
    parser.add_argument("--no-browser", action="store_true", help="本服务不打开浏览器（占位参数）")
    args = parser.parse_args()

    ensure_dirs()

    print("=" * 56)
    print(f"  微信公众号 + RSS 服务  v{APP_VERSION}")
    print(f"  http://{args.host}:{args.port}")
    print("  已启用：公众号登录 / 文章 / 下载导出 / RSS / 代理")
    print("=" * 56)

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
