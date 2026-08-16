# 程序员学习路径

这条路径按“入口 → API → 微信文章链路 → 平台与自动化 → 转码打包”的顺序推进。每个练习都给出可直接执行的命令和预期观察；请在仓库根目录运行。练习只做静态阅读或本地安全验证，不要求真实账号或外网资源。

配套视图：

- [软件架构图](docs/diagrams/software-architecture.mmd)
- [应用与请求流图](docs/diagrams/application-flow.mmd)
- [构建流图](docs/diagrams/build-flow.mmd)

## 阅读策略

- 新 contributor 先读 `main.py`、`app.py`、`frontend/js/router.js`、`frontend/js/api.js`，不要从 vendored 代码开始。
- 功能开发者再读目标平台模块和 `backend/config.py`、`backend/runtime.py`。
- 调试者优先定位症状到 UI、蓝图、共享服务、存储或打包层。
- 架构调整者最后比较 `wechat_mp_tools.spec`、`.github/workflows/build.yml` 和 `docs/diagrams/build-flow.mmd`。

## 练习 1：建立目录与组件地图

目标：把 249 个跟踪文件压缩成少数几个真实架构边界，并确认没有把 vendored 代码当作第一方教学材料。

```bash
git ls-files | wc -l
printf '\nTop-level groups:\n'
git ls-files | cut -d/ -f1 | sort | uniq -c | sort -nr
printf '\nFirst-party backend files:\n'
git ls-files 'backend/*.py' | grep -v '^backend/subtitle_remover/' | sort
printf '\nFrontend entry and components:\n'
git ls-files 'frontend/js/*.js' 'frontend/js/components/*.js' | sort
```

**预期观察**：第一行输出 `249`。`backend/` 文件最多，但核心 Python 模块排除 `backend/subtitle_remover/` 后只有 28 个左右；前端入口只有 `frontend/js/app.js`、`frontend/js/router.js`、`frontend/js/api.js` 三个全局文件。这个结果与 [软件架构图](docs/diagrams/software-architecture.mmd) 的 frontend、Flask、domain、clients 分层一致。

继续打开 `docs/diagrams/software-architecture.mmd`，查找 `main`、`registry`、`blueprints`、`account_pool`、`rss`、`proxy_manager`、`transcode`、`injection` 八个节点。预期它们分别指向本文列出的具体源码路径，而不是抽象组织名。

## 练习 2：跟踪两种启动流

目标：理解 `main.py` 与 `app.py` 的分工，以及导入 `app.py` 时会启动哪些后台循环。

```bash
printf '%s\n' '--- main startup chain ---'
grep -nE 'freeze_support|configure_runtime|find_free_port|start_flask|wait_for_server|create_window|events.closing' main.py
printf '%s\n' '--- app import side effects ---'
grep -nE 'configure_runtime|register_blueprint|migrate_legacy_config|rss_scheduler.start|serve_index|def main' app.py
printf '%s\n' '--- runtime writable locations ---'
grep -nE 'def (resource_dir|app_dir|log_file|configure_runtime)|Application Support|Path.sys.executable' backend/runtime.py
```

**预期观察**：`main.py` 负责 frozen 适配、端口探测、Flask daemon thread、就绪轮询和 pywebview；`app.py` 负责蓝图、静态 SPA、RSS 启动和浏览器模式。`backend/runtime.py::app_dir()` 显示源码模式在仓库根，macOS frozen 模式在 `Application Support/WeChat MP Tools`，Windows frozen 模式在可执行文件旁。

可选本地验证（另开一个终端执行 curl）：

```bash
python3 app.py --host 127.0.0.1 --port 5210 --no-browser
```

```bash
curl -i http://127.0.0.1:5210/
curl -i http://127.0.0.1:5210/api/settings
curl -i http://127.0.0.1:5210/api/transcode/check-ffmpeg
```

**预期观察**：第一个响应是 `frontend/index.html`；`/api/settings` 是 JSON 且包含 `download_dir`、`request_delay`、`rss_upload_enabled` 等默认配置；`check-ffmpeg` 返回 `available` 布尔值。停止第一个终端即可结束本次 Flask、账号保活和 RSS 线程。

## 练习 3：走通 SPA 请求链

目标：能从导航 hash 追到页面组件，再追到 Flask 蓝图。

```bash
printf '%s\n' '--- app boot and periodic checks ---'
grep -nE 'DOMContentLoaded|async init|checkAuthStatus|checkFFmpegStatus|Router.init|setInterval|checkForUpdates' frontend/js/app.js
printf '%s\n' '--- route registration ---'
grep -nE "'login'|'articles'|'channels'|'dy_parse'|'xhs_notes'|'bili_videos'|handleRouting|pageCache|updateNavUI" frontend/js/router.js
printf '%s\n' '--- API wrapper and route client ---'
sed -n '1,70p' frontend/js/api.js
grep -nE 'articles:|accountPool:|channels:|bili:' frontend/js/api.js
printf '%s\n' '--- Flask blueprint prefixes ---'
grep -RhoE 'Blueprint\("[^"]+", __name__, url_prefix="[^"]+"\)' backend/*.py | sort
```

**预期观察**：`frontend/js/app.js` 在 DOMContentLoaded 后初始化并每 30 秒检查登录态；`frontend/js/router.js` 的 hash key 与页面全局对象对应；`frontend/js/api.js::request()` 是所有 fetch 的公共错误边界；蓝图 prefix 与前端 URL 前缀一一对应，例如 `/api/articles`、`/api/account-pool`、`/api/channels`。

调试方法：浏览器 DevTools Network 中先确认状态码和响应 Content-Type。若响应是 HTML，多半是 URL 拼错后被 `app.py::serve_static()` 回退到 SPA；若 fetch TypeError，则先检查本地 Flask 是否仍在监听。

## 练习 4：解剖微信公众号文章下载与 RSS

目标：把账号、列表、任务、文件、历史和上传串成一条可调试链路。

```bash
printf '%s\n' '--- account pool transitions ---'
grep -nE 'COOLDOWN_SECONDS|RISK_KICK_THRESHOLD|def acquire|def report|def start_keepalive|def _run_keepalive_round|def borrow_session' backend/account_pool.py
printf '%s\n' '--- article list retries ---'
sed -n '59,150p' backend/articles.py
printf '%s\n' '--- download task lifecycle ---'
grep -nE 'def start_download|def cancel_download|def get_download_status|def _do_batch_download|def _download_article_into_task' backend/articles.py
printf '%s\n' '--- downloader outputs ---'
grep -nE 'def download_single_article|def download_resource|raw_full_html|data.json|content.txt|metadata.json' backend/downloader.py
printf '%s\n' '--- scheduler timing ---'
grep -nE 'MAX_FETCH_WORKERS|GLOBAL_UPLOAD_SWEEP_MINUTES|def submit_fetch|def _fetch_for_account|def _tick|def start' backend/rss_scheduler.py
```

**预期观察**：`backend/account_pool.py::acquire()` 选 active 且失败少、久未用的账号；`report()` 会把 401 标 invalid、429/200013 先 cooldown，累计风控后 banned。`backend/articles.py` 下载路由立即返回 task id；worker 线程重试 `backend/downloader.py::download_single_article()`；`backend/downloader.py` 输出本地 HTML、raw HTML、media、`data.json`、`content.txt` 和 `metadata.json`；`backend/rss_scheduler.py` 每 30 秒 tick，抓取最多 50 并发，上传最多每批 100 且单篇失败三次隔离。

排查顺序：先看 `data/account_pool.json` 是否有 active 账号，再看任务 status/results，再看文章目录 metadata，最后看 `data/rss_upload_log.json`。网络异常还要回到 `backend/config.py::report_proxy_status()`。

## 练习 5：比较视频平台模块与视频号注入链

目标：识别各平台共同任务模型，并理解视频号为什么需要 Playwright、mitmproxy 和注入脚本协作。

```bash
printf '%s\n' '--- representative API clients ---'
grep -nE '^class DouyinClient|API_VIDEO_DETAIL|API_USER_POST|def download_media' backend/douyin.py
grep -nE '^class KuaishouClient|API_DETAIL|API_USER_FEED|def get_user_feed' backend/kuaishou.py
grep -nE '^class XhsClient|def get_initial_state|def get_note_detail' backend/xiaohongshu.py
grep -nE '^class BilibiliClient|def get_video_detail|def get_playurl|def download_video_item' backend/bilibili.py
printf '%s\n' '--- channels local parse and task ---'
grep -nE 'class ISAAC64|def decrypt_channels_data|def local_parse_with_yuanbao|def _do_cookie_acquisition|def _do_async_download_video|download/start' backend/channels.py
printf '%s\n' '--- mitm allowlist and injection ---'
grep -nE 'TARGET_HOSTS|class ChannelsAddon|def request|def response|def save_synced_feeds|def get_instance|def start|def stop|allow_hosts' backend/mitm_proxy.py
grep -nE 'eventbus|RateLimit|rateGate|finderGetFollowList|finderUserPage|sync-feed|synced-feed-ids' injection_scripts/src/*.js
```

**预期观察**：抖音、快手、小红书和 B 站都以“客户端类 + 蓝图路由 + 任务状态/取消 + 平台历史 JSON”为骨架，但认证和协议不同：抖音有 a_bogus 签名，快手主页依赖 Cookie，小红书解析 SSR 状态，B 站使用 WBI 签名和 DASH。视频号是唯一强依赖 `backend/mitm_proxy.py` 的链路：只允许三个微信域名解密，伪 API 在本地响应，页面脚本被注入，feed 合并进 JSON，下载后用 ISAAC64 key 解密文件头。

调试时优先读 `data/frontend_errors.log`、`data/parse_debug.log`、`data/channels_call_log.jsonl` 和 `data/channels_parsed_feeds.json`；代理问题先调用停止接口恢复系统代理，再检查 CA 和 `NO_PROXY`。

## 练习 6：验证转码队列与打包架构

目标：掌握本地子进程、PyInstaller datas 和 CI 产物的验证方式。

```bash
printf '%s\n' '--- transcode queue and ffmpeg policy ---'
grep -nE 'job_queue|def start_worker|def _transcode_worker|def _execute_transcode|run_ffmpeg_cmd|VideoToolbox|libx264|libx265' backend/transcode.py
printf '%s\n' '--- PyInstaller inputs ---'
sed -n '1,60p' wechat_mp_tools.spec
grep -nE 'Analysis\(|datas=|name=.WeChat MP Tools.|console=False' wechat_mp_tools.spec
printf '%s\n' '--- workflow variants ---'
grep -nE 'runs-on:|python-version|playwright install|Build (Windows|macOS)|WECHAT_MP_TOOLS_BUNDLE_BROWSER|pyinstaller|codesign|ditto|artifact' .github/workflows/build.yml
printf '%s\n' '--- intended mac architecture branches ---'
grep -nE 'macOS ARM64|macOS x86_64|architecture is (arm64|x86_64)|architecture check' docs/diagrams/build-flow.mmd
printf '%s\n' '--- repository documentation tests ---'
python3 -m pytest tests/test_architecture_docs.py tests/test_architecture_diagrams.py -q
```

**预期观察**：`backend/transcode.py` 使用单 worker 队列避免并发 ffmpeg；macOS 可选 VideoToolbox，低码率源会强制 CRF 软编，输出变大时还有软件兜底压缩阶段。`wechat_mp_tools.spec` 至少包含 `frontend/` 与 `injection_scripts/`，Full 版包含 Playwright Chromium；workflow 当前产出 Windows/macOS Full 与 Lite。`docs/diagrams/build-flow.mmd` 的目标 macOS 路径是 ARM64 和 x86_64 各自原生构建、签名、ditto 打包并做 Mach-O 架构检查，全部通过才发布。

## 验收清单

```bash
python3 -m pytest tests -q
git diff --check
```

**预期观察**：全部测试通过，`git diff --check` 没有输出。提交前再确认改动只涉及文档、文档测试和任务报告，没有改动 `docs/diagrams/*.mmd` 或运行时代码。
