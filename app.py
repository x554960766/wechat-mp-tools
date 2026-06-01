#!/usr/bin/env python3
"""
微信公众号文章下载管理工具 — 桌面端应用
Flask 后端 + Web 前端

启动方式：
    python3 app.py              # 默认启动（浏览器模式）
    python3 app.py --port 5100  # 指定端口
"""

import os
import sys
import argparse
import webbrowser
import threading
from pathlib import Path

from flask import Flask, send_from_directory
from flask_cors import CORS

from backend.runtime import configure_runtime, resource_dir

configure_runtime()

from backend.config import ensure_dirs
from backend.auth import auth_bp
from backend.accounts import accounts_bp
from backend.articles import articles_bp
from backend.proxy import proxy_bp
from backend.douyin import douyin_bp
from backend.douyin_login import douyin_login_bp
from backend.douyin_auth import douyin_auth_bp

# ── Flask 应用 ────────────────────────────────────────────
static_folder_path = resource_dir() / "frontend"

app = Flask(
    __name__,
    static_folder=str(static_folder_path),
    static_url_path="",
)
CORS(app)

# 注册蓝图
app.register_blueprint(auth_bp)
app.register_blueprint(accounts_bp)
app.register_blueprint(articles_bp)
app.register_blueprint(proxy_bp)
app.register_blueprint(douyin_bp)
app.register_blueprint(douyin_login_bp)
app.register_blueprint(douyin_auth_bp)


# ── 前端路由 ──────────────────────────────────────────────

@app.route("/")
def serve_index():
    """SPA 主页面"""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    """静态文件"""
    file_path = Path(app.static_folder) / path
    if file_path.exists():
        return send_from_directory(app.static_folder, path)
    # SPA fallback
    return send_from_directory(app.static_folder, "index.html")


# ── 应用设置 API ──────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    from backend.config import get_settings as _get
    from flask import jsonify
    return jsonify(_get())


@app.route("/api/settings", methods=["POST"])
def save_settings():
    from backend.config import get_settings as _get, save_settings as _save
    from flask import request, jsonify
    data = request.get_json() or {}
    settings = _get()
    settings.update(data)
    _save(settings)
    return jsonify({"message": "设置已保存"})


# ── 启动 ──────────────────────────────────────────────────

def open_browser(port: int):
    """延迟 1 秒后打开浏览器"""
    import time
    time.sleep(1)
    webbrowser.open(f"http://localhost:{port}")


def main():
    parser = argparse.ArgumentParser(description="微信公众号文章下载管理工具")
    parser.add_argument("--port", type=int, default=5200, help="服务端口 (默认 5200)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    ensure_dirs()

    print()
    print("=" * 56)
    print("  📱 微信公众号文章下载管理工具")
    print(f"  🌐 http://{args.host}:{args.port}")
    print("=" * 56)
    print()

    if not args.no_browser:
        threading.Thread(target=open_browser, args=(args.port,), daemon=True).start()

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,
    )


if __name__ == "__main__":
    main()
