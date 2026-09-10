"""步骤 1: 启动/附着微信客户端并确认主窗口就绪。"""
from __future__ import annotations

import logging
from mac.mac_win import MacWindow, activate_wechat, find_main_window, launch_wechat
from mac.mac_input import human_sleep
from mac.waiter import wait_until, WaitTimeout

logger = logging.getLogger("wechat_auto_mac.step1")


def ensure_wechat_ready(timeout: float = 15.0) -> MacWindow:
    """确保微信在运行且主窗口处于可用状态。"""
    main = find_main_window(timeout=1.0)
    if main:
        logger.info("微信已在运行中: %s", main)
        activate_wechat()
        human_sleep(0.4, 0.8)
        return main

    logger.info("正在拉起微信客户端...")
    launch_wechat()
    try:
        main = wait_until(lambda: find_main_window(timeout=1.0), timeout=timeout, interval=1.0, desc="微信主窗口")
        activate_wechat()
        human_sleep(0.8, 1.5)
        return main
    except WaitTimeout:
        raise RuntimeError("微信启动超时。如果停留在登录/扫码界面，请先登录微信 Mac 版。")
