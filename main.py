#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号文章下载管理工具 — 桌面端应用启动器
利用 pywebview 渲染原生窗口，彻底摆脱控制台黑窗口和外部浏览器跳转
"""

import os
import sys
import socket
import threading
import time
import multiprocessing

from backend.runtime import configure_runtime, log_file, write_startup_error

# PyInstaller 打包时防止多进程死循环（Playwright 依赖 multiprocessing）
multiprocessing.freeze_support()
configure_runtime()

# ── 修复打包后白屏：当 console=False 时 stdout/stderr 为 None，
#    部分第三方库（如 Werkzeug）写入 None 流会直接崩溃导致白屏。
#    将其重定向到系统空设备以保证安全。
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

# ── 路径与端口初始化 ──────────────────────────────────────

def find_free_port(start=5200, end=5220):
    """动态查找可用端口，防冲突"""
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
            return port
        except OSError:
            continue
    return start  # fallback

def wait_for_server(port, timeout=15):
    """安全轮询等待本地 Flask 服务器就绪"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            import urllib.request
            urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False

def on_closing():
    """窗口关闭回调，彻底杀死后台线程与整个进程"""
    os._exit(0)


# ── 主进程启动 ────────────────────────────────────────────

if __name__ == '__main__':
    try:
        # 启用环境变量标明运行在 PyWebview 容器下（备用逻辑）
        os.environ['USE_PYWEBVIEW'] = '1'

        # 从主程序 app 导入 Flask 实例与初始化
        from app import app
        from backend.config import ensure_dirs
        import logging

        ensure_dirs()

        # 抑制 Werkzeug 请求日志，防止在无控制台环境下写入空流导致崩溃
        logging.getLogger('werkzeug').setLevel(logging.ERROR)

        # 动态获取可用端口
        port = find_free_port()

        # 在后台线程中极速拉起 Flask 服务
        def start_flask():
            try:
                app.run(
                    host="127.0.0.1",
                    port=port,
                    debug=False,      # 生产模式，防止热重载在打包后报错
                    threaded=True,
                )
            except Exception as e:
                print(f"Flask 启动失败: {e}")
                write_startup_error(e)
                os._exit(1)

        server_thread = threading.Thread(target=start_flask, daemon=True)
        server_thread.start()

        # 等待 Flask 完全就绪
        if not wait_for_server(port):
            import webview
            # 如果启动超时，弹窗告知用户
            webview.create_window(
                title='服务启动失败',
                html=f'<h2>应用初始化失败</h2><p>本地服务端口 {port} 启动超时，请尝试重新打开软件。</p><p>日志文件：{log_file()}</p>',
                width=520,
                height=260
            )
            webview.start()
            os._exit(1)

        # 服务就绪后额外等待 0.5 秒，确保 Socket 完全稳定，减少白屏概率
        time.sleep(0.5)

        # 延迟导入 webview，防止初始化干扰
        import webview

        # 创建桌面端原生容器窗口
        window = webview.create_window(
            title='微信公众号文章下载管理工具',
            url=f'http://127.0.0.1:{port}/',
            width=1280,
            height=800,
            resizable=True,
            text_select=True,
            zoomable=True,
        )

        # 监听关闭事件以完整关闭后台服务
        window.events.closing += on_closing

        # 启动 pywebview GUI 循环（阻塞主线程）
        # debug=False 确保在生成发布版本时完全静默无控制台
        webview.start(debug=False)
    except Exception as e:
        write_startup_error(e)
        try:
            import webview
            webview.create_window(
                title='启动失败',
                html=f'<h2>应用启动失败</h2><p>{str(e)}</p><p>日志文件：{log_file()}</p>',
                width=640,
                height=320
            )
            webview.start()
        except Exception:
            raise
