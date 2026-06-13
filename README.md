# 微信公众号 / 视频号 / 抖音 / 小红书 下载与转码工具箱

一个本地运行的 Web/桌面工具箱，用于微信公众号文章离线下载、RSS 订阅、微信视频号下载、抖音资源下载、小红书资源下载，以及已下载视频的转码压缩。

项目支持通过浏览器访问 Flask 后端，也可以通过 `pywebview` 以桌面应用方式运行。

---

## 功能概览

### 📱 微信公众号
- **账号池管理**：支持多账号扫码登录、轮换爬取，并在账号失效/被平台踢出时提供全局前端通知警告。
- **文章离线下载**：支持通过搜索、收藏或粘贴文章 URL，离线下载并保存文章正文（支持 HTML、图片、音频和视频等）。
- **画廊与特殊排版**：支持画廊/贴图类文章排版解析。
- **异常容错机制**：智能捕获“被删除”、“系统故障”、“仅内部分享可见”（私密保护页面）等异常页面，在下载历史中精准回显。
- **RSS 订阅与自动采集**：支持将下载文章与自动抓取文章统一以标准 RSS 2.0 格式输出，且支持自动上传接口配置。

### 🎥 微信视频号
- 支持粘贴分享链接解析并下载单个视频。
- 采用元宝 Cookie 本地解析（支持云端/Worker 代理回退）。
- 支持收藏博主、获取作者主页作品、批量下载及取消。
- 内置微信本地代理助手，支持微信内置页面极速同步。

### 🎵 抖音视频
- 支持单条视频/图文链接解析并无水印下载。
- 支持用户主页、推荐流、搜索博主、喜欢与收藏列表的多线程批量下载。
- 支持扫码登录或 Cookie 登录，获取高画质内容。

### 📕 小红书
- 支持博主主页链接解析收藏，获取主页首屏笔记进行批量勾选下载。
- 支持粘贴笔记链接（含短链和分享文本）解析并下载无水印图片、视频及 Live 实况图。
- 支持 Playwright 扫码登录或手动粘贴 Cookie，以获取高画质资源。

### 🎬 视频转码
- 自动扫描已下载的视频流，或拖入本地视频进行转码。
- 支持 MP4、MKV、MOV、WebM 和 MP3 音频提取。
- 支持 H.264/H.265 编码，内置硬件加速（VideoToolbox/NVENC/QSV）及三阶段智能体积压缩兜底逻辑。
- **环境自愈**：启动时自动将 Homebrew 及项目内置的 `ffmpeg` 路径添加至 `PATH`，若缺少环境则自动在前端隐藏相关菜单与按钮。

---

## 源码运行

推荐 Python 3.10 / 3.11 / 3.12。

```bash
git clone https://github.com/x554960766/wechat-mp-tools.git
cd wechat-mp-tools

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
python3 -m playwright install chromium --no-shell
```

- **启动 Web 浏览器模式**：
  ```bash
  python3 app.py
  ```
  默认地址是 [http://localhost:5200](http://localhost:5200)。
- **启动桌面窗口模式**：
  ```bash
  python3 main.py
  ```

---

## CLI 脚本

如果只需要公众号文章相关的轻量脚本，可以直接运行根目录下的 CLI：

| 脚本 | 作用 |
| :--- | :--- |
| `wechat_mp_login.py` | 扫码登录并保存公众号后台凭证到 `data/wechat_mp_config.json` |
| `wechat_mp_article_fetcher.py` | 获取文章列表，并输出 JSON/Markdown |
| `wechat_mp_batch_downloader.py` | 批量下载文章离线正文 |

---

## 打包

项目已集成 PyInstaller，详细说明见 [BUILD.md](BUILD.md)。

- **本地打包 Full 版**（内置浏览器）：
  ```bash
  PLAYWRIGHT_BROWSERS_PATH=ms-playwright python3 -m playwright install chromium --no-shell
  pyinstaller wechat_mp_tools.spec
  ```
- **本地打包 Lite 版**（依赖系统 Chrome/Edge）：
  ```bash
  WECHAT_MP_TOOLS_BUNDLE_BROWSER=0 pyinstaller wechat_mp_tools.spec
  ```

---

## 数据目录

- macOS：`~/Library/Application Support/WeChat MP Tools/data/`
- Windows/Linux：可执行文件旁边的 `data/`
- 包含文件：登录 Cookie、收藏账号、下载历史、RSS 状态和下载下来的媒体资源。

---

## 常见问题

### 1. Playwright 提示找不到浏览器？
在终端运行：
```bash
python3 -m playwright install chromium --no-shell
```

### 2. macOS 运行打包应用提示损坏或无法打开？
由于未进行开发者签名，首次打开时请在 Finder 中**右键**点击 `WeChat MP Tools.app` 选择“打开”，在弹出的对话框中确认即可。

### 3. Windows 打开无反应？
请查看 `WeChat MP Tools\wechat_mp_tools.log`，部分精简版系统需要手动安装 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)。
