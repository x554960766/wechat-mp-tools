# 仓库架构指南

本文面向需要维护、调试或扩展本工具的程序员。它以当前源码为准：`main.py` 与 `app.py` 是入口，`backend/` 提供 Flask 蓝图与领域服务，`frontend/` 是无构建步骤的哈希路由 SPA，`injection_scripts/src/` 只服务视频号页面自动化，`wechat_mp_tools.spec` 与 `.github/workflows/build.yml` 负责桌面打包和自动化。`backend/subtitle_remover/` 与 `injection_scripts/lib/` 是 vendored 代码，不是本文的阅读入口；`graphify-out/` 是外部分析输出，也不是第一方架构依据。

## 架构视图

- 组件和依赖：[docs/diagrams/software-architecture.mmd](docs/diagrams/software-architecture.mmd)
- 启动、请求与任务生命周期：[docs/diagrams/application-flow.mmd](docs/diagrams/application-flow.mmd)
- Windows 与 macOS 打包流程：[docs/diagrams/build-flow.mmd](docs/diagrams/build-flow.mmd)

## 库存与边界

当前 Git 跟踪文件共 256 个，按主要用途分类如下：

| 类别 | 数量 | 代表路径 | 结论 |
|---|---:|---|---|
| 第一方运行时代码 | 86 | `main.py`、`app.py`、`backend/`、`frontend/`、`injection_scripts/src/` | 日常功能修改集中在这里 |
| vendored 代码 | 134 | `backend/subtitle_remover/`、`injection_scripts/lib/` | 保留原样，不作为扩展模板 |
| 架构文档与图表 | 15 | `docs/ARCHITECTURE.md`、`docs/diagrams/` | 学习、维护和沟通入口 |
| 构建/CI/测试/Skill | 14 | `.github/workflows/build.yml`、`tests/`、`skills/ljt-repo-architect/` | 行为验证与自动化 |
| 根目录配置与工具 | 6 | `README.md`、`BUILD.md`、`requirements.txt`、`scripts/verify_macos_bundle.py` | 使用说明、依赖和检查工具 |
| PyInstaller spec | 1 | `wechat_mp_tools.spec` | 桌面打包单一定义 |

这个分类只描述 Git 跟踪内容；用户本机 `data/`、`dist/`、`build/`、`__pycache__/` 和未跟踪的 `graphify-out/` 不属于发布库存。

## 心智模型

应用是一个“本地服务 + SPA UI + 多平台下载器”的组合：

1. `main.py` 把 Flask 放进本地线程，再用 pywebview 打开原生窗口。
2. `app.py` 也可以直接作为浏览器模式入口运行，二者共享同一组 Flask 蓝图和后台服务。
3. `frontend/js/router.js` 只切换页面对象；页面用 `frontend/js/api.js` 统一调用本地 REST API。
4. 各平台蓝图负责解析、任务状态和文件落盘；共享配置、代理、账号池和运行时助手由 `backend/config.py`、`backend/account_pool.py`、`backend/runtime.py` 提供。
5. 下载通常立即返回 task id，由 daemon thread 执行，前端轮询进度；RSS 和账号保活是独立循环线程。

## 模块地图

### 入口与应用层

| 模块 | 责任 | 关键接口与调用关系 |
|---|---|---|
| `main.py` | 桌面应用启动器 | `ensure_virtualenv()`、`find_free_port()`、`wait_for_server()`、`on_closing()`；导入 `app.py` 的 `app`，在 daemon thread 中 `app.run()`，再启动 pywebview |
| `app.py` | Flask 应用、静态 SPA、浏览器模式入口 | 注册 17 个蓝图；`/` 与 `/<path:path>` 返回 `frontend/index.html` 或静态文件；`main()` 解析 `--host/--port/--no-browser/--debug` 并 `app.run(threaded=True)` |
| `backend/runtime.py` | 源码与 PyInstaller 运行时差异适配 | `resource_dir()`、`app_dir()`、`log_file()`、`configure_runtime()`、`launch_chromium()`、`launch_persistent_context()`、`write_startup_error()`；修正 CA、Playwright 浏览器路径、ffmpeg 搜索路径 |
| `backend/config.py` | 设置、存储路径和代理调度 | `DEFAULT_SETTINGS`、`ensure_dirs()`、`get_settings()/save_settings()`、`get_proxy_url()`、`report_proxy_status()`；所有主要 JSON 状态文件路径在此集中声明 |

`app.py:78-94` 依次注册认证、公众号、文章、代理、账号池、抖音、快手、视频号、转码、小红书、B 站和更新蓝图。每个 `backend/*.py` 蓝图自带 `url_prefix`，因此路由归属可以直接从模块开头的 `Blueprint(...)` 判断。

### 共享领域服务

| 模块 | 责任 | 重要行为 |
|---|---|---|
| `backend/account_pool.py` | 微信读书凭证池 | `acquire()` 按 `(failures, last_used)` 选择 active 账号；`report()` 处理 401/429/200013 与普通失败；`start_keepalive()` 每 15 分钟心跳，超过 45 分钟做浏览器刷新；全局 `account_pool` 在模块导入时启动 |
| `backend/rss_scheduler.py` | 公众号订阅抓取与上传 | 全局 `rss_scheduler` daemon 循环每 30 秒 `_tick()`；`ThreadPoolExecutor(max_workers=50)` 执行 `_fetch_for_account()`；下载历史中未上传条目可批量 POST 到设置的上传网关 |
| `backend/downloader.py` | 微信公众号文章离线化 | `download_single_article()` 用 requests 拉文章、解析正文/封面/视频、把资源写到 `media/`，生成 `*.html`、`*_raw.html`、`data.json`、`content.txt`、`metadata.json` |
| `backend/mitm_proxy.py` | 视频号 HTTPS 拦截与页面注入 | `ChannelsAddon` 只拦截 `channels.weixin.qq.com`、`mp.weixin.qq.com`、`res.wx.qq.com`；`ProxyManager.start()/stop()` 管理 CA、系统代理、NO_PROXY、mitmproxy 线程 |
| `backend/transcode.py` | FFmpeg 元信息与转码队列 | `get_video_metadata()` 调 ffprobe；`job_queue` 是 `queue.Queue`，`start_worker()` 启动单线程 `_transcode_worker()`，`_execute_transcode()` 构造 ffmpeg 命令并解析 `-progress` 输出 |

### 平台客户端

| 平台 | 第一方入口 | 架构特征 |
|---|---|---|
| 微信公众号/微信读书 | `backend/auth.py`、`backend/accounts.py`、`backend/articles.py`、`backend/downloader.py` | 扫码登录得到 token 并写入账号池；文章列表经 `WEREAD_PLATFORM_URL`，下载任务在 `backend/articles.py` 的字典中保存状态，实际文件生成委托给 `backend/downloader.py` |
| 微信视频号 | `backend/channels.py`、`backend/mitm_proxy.py`、`injection_scripts/src/` | 解析有元宝 Cookie、自定义 Worker 和 CDN 兜底路径；采集页面靠 mitmproxy 注入；同步数据落 JSON，下载线程用 ISAAC64 key 解密前 128 KiB |
| 抖音 | `backend/douyin.py`、`backend/douyin_auth.py`、`backend/douyin_login.py`、`backend/douyin_sign.py` | 直接 HTTP API；`DouyinClient` 负责 URL 解析、a_bogus 签名和分页；全局单任务状态、取消 Event 和 daemon thread 支持单条/主页/喜欢/合集/直播 |
| 快手 | `backend/kuaishou.py`、`backend/kuaishou_auth.py` | `KuaishouClient` 调 live_api 与 `rest/v/profile/feed`；单作品可匿名解析，主页列表依赖 Cookie，批量下载为单任务状态 |
| 小红书 | `backend/xiaohongshu.py`、`backend/xiaohongshu_login.py` | `XhsClient` 解析短链和 `window.__INITIAL_STATE__`；浏览器会话管理器支持登录与分页；下载任务按 task id 隔离，输出文案、HTML 和媒体 |
| B 站 | `backend/bilibili.py`、`backend/bilibili_login.py`、`backend/bilibili_sign.py` | `BilibiliClient` 调 view/playurl/space API，WBI 签名在 `backend/bilibili_sign.py`；DASH 音视频可下载后用 ffmpeg 合并，弹幕可转 ASS/SRT |

这六个平台都遵守同一个边界：蓝图和全局任务状态在平台模块内，HTTP 代理状态回写 `backend/config.py`，文件和历史只进入 `backend/config.py::DATA_DIR` 下的平台目录，页面切换和状态轮询留在 `frontend/js/`。

### 前端

| 模块 | 责任 |
|---|---|
| `frontend/index.html` | 唯一 SPA 宿主，按顺序加载 toast/modal、API 客户端、页面组件、`frontend/js/router.js`、`frontend/js/app.js` |
| `frontend/js/app.js` | `App.init()` 初始化组件、认证状态、ffmpeg 状态、路由、侧栏和更新检查；每 30 秒刷新登录状态，3 秒后检查版本 |
| `frontend/js/router.js` | `routes` 把 hash key 映射到页面对象；`handleRouting()` 解析查询参数、处理缓存和 `onShow()`；未知路由重定向 `#login` |
| `frontend/js/api.js` | `API.request()` 统一 fetch、JSON 解析、错误 Toast 和网络错误；各命名空间暴露平台端点 |
| `frontend/js/components/` | 平台页面组件，页面对象提供 `render()/init()/onShow()/destroy()` 钩子 |

### 注入运行时

`injection_scripts/src/eventbus.js` 基于 mitt 定义视频号事件；`utils.js` 提供 `WXU` API 包装、格式化和下载动作；`components.js` 提供界面组件；`home.js`、`feed.js`、`profile.js` 分别适配首页、详情页和作者主页；`automation.js` 用增量、令牌桶、熔断和会话预算采集关注作者。`backend/mitm_proxy.py::ChannelsAddon.response()` 按页面路径选择这些源码并注入 HTML，`save_synced_feeds()` 把注入脚本 POST 回来的 feed 合并进 JSON 存储。

## 启动流

### 桌面模式

1. `main.py::ensure_virtualenv()` 在非 frozen 环境下优先切换本仓库 `venv312`。
2. `main.py` 调 `multiprocessing.freeze_support()` 和 `backend/runtime.py::configure_runtime()`，避免 PyInstaller/Playwright 多进程问题。
3. Windows 缺 WebView2 时，`main.py` 查找 `wechat_mp_tools.spec` 打包的 bootstrapper，可安装或降级浏览器模式。
4. `from app import app` 触发蓝图注册、`backend/account_pool.py` 保活线程启动、旧配置迁移和 `rss_scheduler.start()`。
5. `main.py::start_flask()` 在 daemon thread 中监听 `127.0.0.1:5200-5220` 的可用端口，`wait_for_server()` 轮询 `/`。
6. 成功后 pywebview 打开 SPA；失败时经 `backend/runtime.py::write_startup_error()` 写 `wechat_mp_tools.log`；窗口关闭回调停止 mitmproxy 并结束进程。

### 浏览器模式

`python3 app.py` 直接执行 `app.py::main()`：同样先 `configure_runtime()`、导入所有蓝图和后台服务，`ensure_dirs()` 创建数据目录，可选线程打开浏览器，然后 `app.run(host, port, threaded=True)`；`finally` 中停止 `ProxyManager`。因此调试 API 时不需要 pywebview，但要注意导入阶段已经启动保活和 RSS 循环。

## 请求流

### SPA 到 Flask

`frontend/index.html` DOMContentLoaded → `frontend/js/app.js::App.init()` → `frontend/js/router.js::Router.handleRouting()` → 页面组件方法 → `frontend/js/api.js::API.request()` → `app.py` 蓝本路由 → 平台模块或共享服务。SPA fallback 由 `app.py::serve_static()` 保证，未知静态路径仍返回 `frontend/index.html`。

### 微信文章下载

1. `frontend/js/api.js::articles.download()` POST `/api/articles/download`。
2. `backend/articles.py::start_download()` 创建 `_download_tasks[task_id]` 并启动 `_do_batch_download` daemon thread，立即返回 task id。
3. `_do_batch_download()` 读取 `backend/config.py` 的重试与间隔设置，逐篇调用 `backend/downloader.py::download_single_article()`。
4. 下载器拉微信页面和 CDN 资源，重写本地 HTML，写文章目录和 `metadata.json`。
5. 任务线程把结果写入 `data/download_history.json`；如果开启上传，`rss_scheduler.force_upload_all()` 允许后台网关继续处理。
6. 前端通过 `frontend/js/api.js::articles.downloadStatus()` 或 SSE 轮询状态，不阻塞 UI。

### 视频号采集

1. 用户启动 `backend/channels.py` 的代理接口，`backend/mitm_proxy.py::ProxyManager.start()` 生成/信任 CA、设置系统代理并启动 mitmproxy。
2. `ChannelsAddon.response()` 只对允许域名改写响应，把 `injection_scripts/src/` 脚本插入页面。
3. 注入页面调用视频号内部接口，`injection_scripts/src/automation.js` 限流并增量分页，再 POST `/__wx_channels_api/sync-feed`。
4. `ChannelsAddon.request()` 本地响应该伪端点，`save_synced_feeds()` 合并 `channels_favorites.json` 与 `channels_parsed_feeds.json`。
5. SPA 从 `backend/channels.py` 读取作者/作品；`/download/start` 创建带 `threading.Event` 的下载线程，下载后按 key 解密并写历史。

## 后台工作模型

| 后台机制 | 启动位置 | 线程/进程模型 | 结束行为 |
|---|---|---|---|
| Flask 桌面服务 | `main.py::start_flask()` | daemon thread，threaded server | 窗口关闭 `os._exit(0)` |
| 浏览器自动打开 | `app.py::open_browser()` | daemon thread 延迟 1 秒 | 一次性 |
| 微信读书保活 | `backend/account_pool.py` 导入全局单例 | daemon thread，15 秒后每 15 分钟 | 进程内守护，不独立持久化 |
| RSS 调度 | `app.py` 导入时 `rss_scheduler.start()` | daemon loop 每 30 秒 + 最多 50 worker 线程 | `stop()` 设置 Event 并 shutdown executor |
| 文章下载 | `backend/articles.py` | 每个任务一个 daemon thread，状态字典加锁 | completed/failed/cancelled |
| 平台批量下载 | `backend/douyin.py`、`backend/kuaishou.py`、`backend/xiaohongshu.py`、`backend/bilibili.py`、`backend/channels.py` | 平台级单任务或 task id 字典 + daemon thread/Event | 可取消，写平台历史 |
| 视频号代理 | `backend/mitm_proxy.py::ProxyManager` | 单例状态 + mitmproxy daemon thread | 还原系统代理、关闭 master、恢复 NO_PROXY |
| 转码 | `backend/transcode.py::start_worker()` | `queue.Queue` + 单 daemon worker，一次一个 ffmpeg 子进程 | 任务状态持久在进程字典 |

这些线程都不跨进程共享状态。重启应用会重新读取 JSON 文件，但仍在执行中的下载/转码和内存 task id 会消失；已完成的文件与历史 JSON 保留。

## 存储与配置

`backend/config.py` 在源码模式把 `DATA_DIR` 放在仓库根 `data/`；PyInstaller 后依赖 `backend/runtime.py::app_dir()`：macOS 使用 `~/Library/Application Support/WeChat MP Tools`，Windows/Linux 把数据放在可执行文件旁。`backend/runtime.py::log_file()` 统一返回启动日志路径。

| 位置 | 内容 | 主要写入者 |
|---|---|---|
| `data/app_settings.json`、`data/proxy_config.json` | 应用设置、代理节点与轮换配置 | `backend/config.py`、`backend/proxy.py` |
| `data/account_pool.json`、`data/wechat_mp_config.json` | 微信读书账号池与旧配置 | `backend/account_pool.py`、`backend/auth.py` |
| `data/accounts.json` | 收藏公众号与 RSS 订阅 | `backend/accounts.py` |
| `data/download_history.json` | 公众号文章历史 | `backend/articles.py` |
| `data/articles_full/` | 文章目录、媒体、离线 HTML、纯文本和元数据 | `backend/downloader.py` |
| `data/rss_articles.json`、`data/rss_subscriptions.json`、`data/rss_upload_log.json` | RSS 缓存、订阅、上传审计 | `backend/rss_scheduler.py` |
| `data/channels_*` | 视频号历史、收藏、feed、调用日志 | `backend/channels.py`、`backend/mitm_proxy.py` |
| `data/douyin_downloads/`、`data/douyin_history.json` | 抖音文件与历史 | `backend/douyin.py` |
| `data/kuaishou_downloads/`、`data/kuaishou_history.json` | 快手文件与历史 | `backend/kuaishou.py` |
| `data/xhs_downloads/`、`data/xhs_accounts.json`、`data/xhs_history.json` | 小红书输出、博主与历史 | `backend/xiaohongshu.py` |
| `data/bilibili_downloads/`、`data/bilibili_accounts.json`、`data/bilibili_history.json` | B 站视频/字幕、UP 主与历史 | `backend/bilibili.py` |
| `data/temp_uploads/`、`data/transcoded/` | 外部上传缓存和转码输出 | `backend/transcode.py` |
| `data/ca.*`、`data/certs/`、`data/mitm/`、`data/frontend_errors.log`、`data/parse_debug.log` | mitmproxy 证书、配置、前端错误和解析诊断 | `backend/mitm_proxy.py`、`backend/channels.py` |

除 SQLite/数据库没有使用外，持久化主要是普通 JSON 和文件目录；并发保护是模块内 `threading.Lock`，不是文件锁。多个应用实例同时写同一个 `DATA_DIR` 时应先避免这种操作。

## 构建与自动化

`wechat_mp_tools.spec` 是 PyInstaller 单一定义：打包 `frontend/`、`injection_scripts/`，Full 构建额外打包 `ms-playwright/`，Windows Full 还包含 WebView2 bootstrapper；`collect_all("mitmproxy")` 补齐动态依赖；macOS 生成 `.app`，Windows 生成无控制台可执行目录。

`.github/workflows/build.yml` 安装 Python 3.12 和 PyInstaller，为 Full 构建安装 Playwright Chromium，执行同一个 spec。当前流水线产出六个平台变体：Windows Full/Lite、macOS ARM64 Full/Lite、macOS x86_64 Full/Lite。macOS job 使用矩阵分别选择 `macos-latest`（ARM64）和 `macos-15-intel`（x86_64）原生 runner，先校验 `uname -m`，再为 Full/Lite 设置 `WECHAT_MP_TOOLS_TARGET_ARCH`；构建后运行 `scripts/verify_macos_bundle.py` 检查主程序与原生扩展，Full 构建还带 `--require-chromium` 强制确认内置 Chromium 的 Mach-O 架构，Lite 则不要求浏览器，最后用 ditto 打包并发布 artifact 或 tag release。也就是说，macOS 双架构不是通用二进制合并，而是 ARM64 与 x86_64 各自的可复测流水线。

## 扩展路径

### 添加一个 API

1. 在合适的 `backend/*.py` 蓝图内定义路由；新模块则创建 `Blueprint(...)` 并在 `app.py` import/register。
2. 如需文件输出，路径从 `backend/config.py::DATA_DIR` 派生，不要写死用户目录。
3. 长任务立即返回 task id，用锁保护状态字典，并提供 status/cancel 路由，参考 `backend/articles.py` 与 `backend/channels.py`。
4. 在 `frontend/js/api.js` 增加命名空间方法，让错误和 JSON 解析继续走 `API.request()`。

### 添加一个页面

1. 在 `frontend/js/components/` 建页面对象，实现 `render()/init()`，可选 `onShow()/destroy()`。
2. 在 `frontend/index.html` 加导航项与 `<script>`，并保持 `frontend/js/router.js` 之后才加载 `frontend/js/app.js`。
3. 在 `frontend/js/router.js::routes` 注册 hash key；带动态参数时利用现有“有参数则重建缓存”的行为。

### 添加一个平台

1. 先建客户端和单元边界，再建蓝图，参考 `backend/bilibili.py::BilibiliClient` 与路由分离的结构。
2. 所有外呼统一接受 `backend/config.py::get_proxies_dict()` 并调用 `report_proxy_status()` 回写节点健康。
3. 登录模块单独放 `*_login.py` 或 `*_auth.py`，下载状态、历史和输出目录按平台命名。
4. 前端组件放对应子目录，并保持 API URL 与蓝图 `url_prefix` 一一对应。

## Bug 路由表

| 症状 | 首先读 | 关键证据 |
|---|---|---|
| 桌面窗口白屏/无法启动 | `main.py`、`backend/runtime.py::log_file()` | `wechat_mp_tools.log`、端口 5200-5220 是否被占用、WebView2 状态 |
| 页面 404 或未知路由回登录页 | `frontend/index.html`、`frontend/js/router.js` | 组件 script 是否加载、`routes` key 是否存在 |
| API 返回 HTML 而不是 JSON | `app.py::serve_static()`、对应蓝图 | URL 是否漏 `/api/` 前缀、浏览器 Network 响应类型 |
| 认证失效或 401/429 | `backend/auth.py`、`backend/account_pool.py`、`backend/articles.py::_fetch_articles_page()` | `account_pool.json` 的 status/risk_hits/failures/last_error |
| 代理不稳定 | `backend/config.py`、`backend/proxy.py` | `proxy_config.json`、`report_proxy_status()` 冷却状态、目标平台模块回写点 |
| 文章资源缺失 | `backend/downloader.py`、`backend/articles.py` | 文章目录 `metadata.json` 的 `leftover_urls/resources_count`、任务 results |
| RSS 没抓取/没上传 | `backend/rss_scheduler.py`、`backend/accounts.py` | `rss_subscriptions.json` 的 next_fetch_time/enabled、`rss_upload_log.json` |
| 视频号按钮不出现或 feed 不落盘 | `backend/mitm_proxy.py`、`injection_scripts/src/`、`backend/channels.py` | CA 信任、系统代理、`frontend_errors.log`、`channels_parsed_feeds.json` |
| 视频号文件无法播放 | `backend/channels.py::decrypt_channels_data()`、下载路由 | decrypt key 是否为空、文件是否只在头部 128 KiB 解密 |
| 抖音/快手签名或风控失败 | `backend/douyin_sign.py`、`backend/douyin.py`、`backend/kuaishou.py` | URL resolve 结果、API 响应体、Cookie 状态和任务日志 |
| B 站音视频/字幕异常 | `backend/bilibili.py`、`backend/bilibili_sign.py` | view/playurl 返回、DASH 视频/音频 URL、ffmpeg 合并日志 |
| 转码卡住或体积变大 | `backend/transcode.py` | `/status` 中实际编码策略、stderr 最后 500 字符、ffprobe 输出 |
| 打包缺文件或浏览器不可用 | `wechat_mp_tools.spec`、`backend/runtime.py`、`.github/workflows/build.yml` | `frontend`/`injection_scripts`/`ms-playwright` 是否进入 datas、运行时 PATH 与 PLAYWRIGHT_BROWSERS_PATH |
| mac 包在错误架构机器上运行 | `scripts/verify_macos_bundle.py`、`.github/workflows/build.yml` | 主程序/扩展/Chromium 架构 gate、ARM64/x86_64 原生 job 产物 |

调试原则：先确定请求已经到达哪一层，再看对应 JSON 状态文件；平台解析失败优先抓响应体，文件异常先看磁盘产物和 metadata，UI 状态异常再看 `frontend/js/api.js` 是否把错误吞掉或降级。
