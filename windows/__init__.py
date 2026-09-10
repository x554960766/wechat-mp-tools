"""Windows 微信 PC 客户端 UI 自动化模块 (与 mac/ 目录对等)。

设计思路参考 Access_wechat_article：
- 窗口发现使用纯 Win32 API (ctypes 调用 user32.dll)，不引入硬依赖；
- 点击/按键使用 PostMessage 合成消息（不占用真实鼠标、不抢用户焦点）；
- uiautomation 库为可选依赖，仅用于定位界面元素坐标（未安装时自动降级）。
"""
