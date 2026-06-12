# 微信公众号 / 视频号 / 抖音下载与转码工具箱

一个本地运行的 Web/桌面工具箱，用于微信公众号文章离线下载、RSS 订阅、微信视频号下载、抖音资源下载，以及已下载视频的转码压缩。

项目可以用浏览器访问 Flask 后端，也可以通过 `pywebview` 以桌面应用方式运行。打包规则基于 PyInstaller，并支持 Full / Lite 两种产物：Full 内置 Playwright Chromium，Lite 不内置浏览器、依赖用户电脑已有 Chrome 或 Edge。

---

## 功能概览

### 微信公众号

- 扫码登录微信公众平台，保存 `data/wechat_mp_config.json` 凭证。
- 搜索、收藏公众号，并获取文章列表。
- 支持按列表、范围或单篇 URL 下载公众号文章，离线保存 HTML、图片、音视频等资源。
- 支持画廊/贴图类文章解析，例如 `item_show_type == 8` 的上下贴图和描述文字。
- 支持 RSS Feed：已下载文章和 RSS 自动抓取文章会合并输出到 `/api/articles/rss`，也支持按公众号输出。
- RSS 自动订阅按随机间隔抓取新文章，例如 1 小时档会在约 45-75 分钟范围内安排下一次抓取。
- RSS 采集时间段（起止时间）独立于上传开关，始终可见可配；时间窗口控制整体自动抓取。
- RSS 新文章上传服务器默认关闭，上传接口地址需用户自行配置，项目代码不含任何默认地址。
- 开启上传后，RSS 订阅弹窗可看到待上传数量，并支持一键「上传历史文章」（含 pending 队列 + 下载历史中未上传的文章）。
- 支持全局公众号登录**账号池管理**（`/api/account-pool`），具备多账号并发爬取轮换，并在账号失效或被平台踢出时，在前端提供全局警告通知并自动移出池中。

### 微信视频号

- 支持粘贴视频号分享链接解析并下载。
- 支持腾讯元宝 Cookie 本地解析，也支持云端/Worker 回退配置。
- 支持收藏视频号作者、查看作者作品、批量下载、取消任务和下载历史管理。
- 内置微信极速同步助手，可配置证书、开启本地代理，并在微信内置页面中同步作者作品。

### 抖音

- 支持单条视频/图文链接下载。
- 支持用户主页批量下载、推荐流、用户搜索、喜欢/收藏列表读取。
- 支持扫码或手动 Cookie 登录，以获取需要登录态的内容。
- 下载历史可查看、定位、清空。

### 视频转码

- 支持扫描视频号和抖音已下载视频，也支持从本地拖入视频。
- 支持 MP4、MKV、MOV、WebM 和 MP3 音频提取。
- 支持 H.264/H.265 转码、音频转码、CRF/码率控制，以及 VideoToolbox / NVENC / QSV 等硬件编码器。
- 内置三阶段体积压缩兜底逻辑。
- **环境自愈与防呆机制**：启动时自动检测 macOS Homebrew、系统默认目录以及项目内置的 `ffmpeg` 路径并注入 `PATH` 环境变量（解决 GUI/冷启动下找不到 `ffprobe`/`ffmpeg` 的报错）；当检测不到可用的 FFmpeg 时，自动在前端隐藏“视频转码”侧边栏菜单及所有“导入转码”操作按钮，且对强行通过 hash 路由访问的行为进行拦截并自动跳回登录页。

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

启动 Web 管理面板：

```bash
python3 app.py
```

默认地址是 [http://localhost:5200](http://localhost:5200)。也可以指定端口或不自动打开浏览器：

```bash
python3 app.py --port 5100 --no-browser
```

启动桌面窗口：

```bash
python3 main.py
```

---

## CLI 脚本

如果只需要公众号文章相关的轻量脚本，可以直接运行根目录下的 CLI：

| 脚本 | 作用 |
| :--- | :--- |
| `wechat_mp_login.py` | 扫码登录并保存公众号后台凭证到 `data/wechat_mp_config.json` |
| `wechat_mp_article_fetcher.py` | 根据脚本内 `TARGET_ACCOUNTS` 获取文章列表，并输出 JSON/Markdown |
| `wechat_mp_batch_downloader.py` | 根据脚本内 `TARGET_ACCOUNTS` 批量下载文章离线正文 |

使用 CLI 前请先编辑对应脚本顶部的 `TARGET_ACCOUNTS`、`MAX_ARTICLES` 等配置。

---

## 打包

项目已集成 PyInstaller，详细说明见 [BUILD.md](BUILD.md)。

本地 macOS 打包 Full 版：

```bash
PLAYWRIGHT_BROWSERS_PATH=ms-playwright python3 -m playwright install chromium --no-shell
pyinstaller wechat_mp_tools.spec
```

本地 macOS 打包 Lite 版：

```bash
WECHAT_MP_TOOLS_BUNDLE_BROWSER=0 pyinstaller wechat_mp_tools.spec
```

GitHub Actions 会在推送到 `main` / `master` 时自动打包 Windows 和 macOS 的 Full / Lite 产物，也可以在 Actions 页面手动触发。若只是上传代码、不希望触发打包，可以在提交信息中加入 `[skip ci]`。

---

## 数据目录

源码运行时，数据默认保存在项目根目录下的 `data/`。

打包后数据会保存到用户可写目录：

- macOS：`~/Library/Application Support/WeChat MP Tools/data/`
- Windows：可执行文件旁边的 `data/`

常见数据包括登录 Cookie、收藏账号、下载历史、RSS 订阅状态和下载文件。

---

## 常见问题

### Playwright 提示找不到浏览器？

源码运行时执行：

```bash
python3 -m playwright install chromium --no-shell
```

Lite 打包版不会内置 Chromium，需要用户电脑已安装 Google Chrome 或 Microsoft Edge。

### 视频号解析失败怎么办？

可以在设置中配置腾讯元宝 Cookie，走本地解析；也可以配置私有 Worker。若登录态过期，请重新获取 Cookie。

### RSS 随机间隔能避免平台风控吗？

随机间隔只能减少固定周期特征，不能保证规避平台风控。建议控制订阅数量、抓取频率，并保留合理的采集时间段。

### RSS 上传为什么默认没开？

RSS 新文章上传服务器默认关闭，且不预置任何上传地址。如需使用，请在系统设置中勾选开关并填写你自己的上传接口地址。

### macOS 打包应用打不开？

如果没有 Apple Developer ID 签名，首次运行可能被系统拦截。请右键 `WeChat MP Tools.app`，选择“打开”。如果启动后秒退，查看 `~/Library/Application Support/WeChat MP Tools/wechat_mp_tools.log`。

### Windows 打包版打开没反应？

查看 `WeChat MP Tools\wechat_mp_tools.log`。极少数精简系统可能需要安装 Microsoft Edge WebView2 Runtime。
