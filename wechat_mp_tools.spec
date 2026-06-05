# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

project_root = os.path.abspath('.')

# ── 资源文件配置 ──────────────────────────────────────────
# 收集静态前端文件夹到包中
datas = [
    (os.path.join(project_root, 'frontend'), 'frontend'),
]

playwright_browsers = os.path.join(project_root, 'ms-playwright')
if os.path.isdir(playwright_browsers):
    datas.append((playwright_browsers, 'ms-playwright'))

# ── 依赖配置 ──────────────────────────────────────────────
hiddenimports = [
    'flask',
    'flask_cors',
    'requests',
    'urllib3',
    'playwright',
    'playwright.sync_api',
    'pysocks',
    'jinja2',
    'werkzeug',
    'click',
    'itsdangerous',
    'backend',
    'backend.config',
    'backend.auth',
    'backend.accounts',
    'backend.articles',
    'backend.proxy',
    'backend.douyin',
    'backend.douyin_login',
    'backend.douyin_auth',
    'backend.douyin_sign',
    'backend.downloader',
    'backend.sign',
    'backend.runtime',
    'backend.channels',
    'backend.rss_scheduler',
    
    # pywebview 核心支持
    'webview',
    'webview.platforms',
]

# macOS Cocoa 支持
if sys.platform == 'darwin':
    hiddenimports.extend([
        'webview.platforms.cocoa',
        'objc',
        'Cocoa',
        'Foundation',
        'WebKit',
    ])

# Windows WebView2 (winforms) 支持
if sys.platform == 'win32':
    hiddenimports.extend([
        'pythonnet',
        'clr',
        'clr_loader',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
    ])

block_cipher = None

a = Analysis(
    ['main.py'],  # ── 入口点修改为 main.py ──
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        '_tkinter',
        'tcl',
        'tk',
        'FixTk',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── 平台打包输出配置 ───────────────────────────────────────

if sys.platform == 'darwin':
    # macOS 打包配置：打包为双击运行的 .app 捆绑包
    exe = EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        name='WeChat MP Tools',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,  # 完全不显示后台终端窗口，纯 native 体验
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = BUNDLE(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='WeChat MP Tools.app',
        bundle_identifier='com.wechat-mp.tools',
        info_plist={
            'NSPrincipalClass': 'NSApplication',
            'NSHighResolutionCapable': 'True',
            'CFBundleName': 'WeChat MP Tools',
            'CFBundleDisplayName': 'WeChat MP Tools',
        }
    )
elif sys.platform == 'win32':
    # Windows 打包配置：打包为绿色免安装文件夹
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='WeChat MP Tools',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        console=False,  # ── 关键修改：Windows 端也完全关闭黑窗口控制台 ──
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='WeChat MP Tools',
    )
else:
    # Linux 平台打包配置
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='wechat_mp_tools',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,  # 纯窗口运行
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='wechat_mp_tools',
    )
