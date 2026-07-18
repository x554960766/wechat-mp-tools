# B站模块设计实现文档

> **目标读者**：负责具体编码的 Agent / 开发者。本文档自包含，按本文档即可完成编码，无需其他上下文。
> **参考样板**：本项目 **"微信公众号"模块** 与 **"小红书"模块**（登录管理 / 账号/博主管理 / 视频/笔记下载 / 链接下载 / 下载历史 五个页面）。
> **数据采集原理**：基于开源项目 [yutto](https://github.com/yutto-dev/yutto) 提供的 API 接口和数据结构设计，利用直调 B 站 API 来实现视频详情提取、DASH 轨道合并以及弹幕和字幕转化。

---

## 1. 总体目标

在现有工具（Flask 后端 + 原生 JS Hash 路由 SPA）中新增 **"B站视频"侧边栏菜单分组**，对标已有的抖音/小红书模块的页面与功能。原则：**能实现的都要有，充分借鉴 yutto 项目中的异步流式下载及 FFmpeg 混流合并机制**。

### 1.1 页面映射关系（抖音/小红书 → B站）

| 对应页面 | B站对应页面 | 路由 key | 可行性 | 差异说明 |
|---|---|---|---|---|
| 登录管理（扫码/Cookie，`login.js`） | 登录管理 | `bili_login` | ✅ | 支持 B 站官方的「二维码扫码登录」（轮询 Passport 接口）以及「手动粘贴 Cookie」两种方式，获取到的 Cookie（核心为 `SESSDATA` 与 `bili_jct`）存入设置中。 |
| 博主/博主管理（`accounts.js`） | UP主管理 | `bili_accounts` | ✅ | 粘贴 UP 主的主页链接（如 `https://space.bilibili.com/<mid>`）或直接输入 MID，解析 UP 主的基本信息（头像、昵称、粉丝、签名）并收藏。 |
| 笔记/文章下载（`notes.js` / `articles.js`） | 视频下载 | `bili_videos` | ✅ | 选择已收藏的 UP 主 → 列出其空间最新投稿（调用 `/x/space/wbi/arc/search`）并支持 WBI 签名 → 勾选进行批量下载（包含多 P 视频选择）。 |
| 链接下载（粘贴 URL 批量下载，`download.js`） | 链接下载 | `bili_download` | ✅ | 粘贴视频链接（支持单视频 `BV`/`AV`、番剧 `ep`/`ss`、多分P `p=n`、收藏夹 `fid`、合集列表），解析后在后台多线程/异步合并下载。 |
| 下载历史（`history.js`） | 下载历史 | `bili_history` | ✅ | 与抖音/小红书下载历史同构：表格 + 打开文件/目录 + 删除单条（连同视频、弹幕、字幕、海报等文件）。 |

### 1.2 明确支持的资源下载
- **无水印视频+音频**：通过 `qn=127` 及 `fnval=4048` 参数直接获取 DASH 格式的高清视频（HEVC/AVC）和高音质音频（FLAC/AAC/Dolby），支持自动检测大会员（VIP）状态以获取 1080P 高码率、4K 甚至 8K 资源。
- **弹幕转换**：支持下载 B 站 XML/Protobuf 弹幕并自动转换为高级 ASS 弹幕格式（可直接被本地播放器如 IINA、PotPlayer 加载）。
- **字幕下载**：支持提取视频内自带的 CC 字幕，自动转换为标准 SRT 或 ASS 格式。
- **元数据 NFO 与封面海报**：自动保存视频封面作为海报（`-poster.jpg`），并可选输出符合 Jellyfin/Plex 规范的 `.nfo` 格式描述文件。

---

## 2. 现有项目架构参考

新模块的代码风格、结构必须与以下已有规范完全对齐：

| 已有文件 | 作用 | B站模块如何参考与集成 |
|---|---|---|
| `backend/config.py` | 全局数据目录、通用 JSON 读写、代理参数 | B 站模块直接调用其 `get_settings()`、`get_proxies_dict()` 等方法，将 Cookie 存在全局 `bilibili_cookie` 中。 |
| `backend/runtime.py` | 用于扫码登录的浏览器实例配置 | 虽然 B 站提供了纯 API 二维码生成和轮询，但在做防风控或调试时可使用 `launch_chromium` 联动。 |
| `app.py` | 蓝图路由注册 | 在 `app.py` 中注册 `bilibili_bp` 与 `bilibili_login_bp` 两个蓝图。 |
| `frontend/js/router.js` | 路由跳转与主题风格控制 | 在 `routes` 注册 `bili_login` 等 5 个新路由，统一使用 **wechat-theme**（浅色系）或者在 `css` 里配置其 B 站专属粉色。 |
| `frontend/index.html` | 页面侧边栏与脚本导入 | 引入新编写的 5 个前端 JS 文件，并在侧边栏新增「B站视频」菜单组。 |

---

## 3. 后端设计

### 3.1 新增文件

```text
backend/bilibili.py        # 核心：BilibiliClient 数据解析类 + /api/bilibili 蓝图路由
backend/bilibili_login.py  # 蓝图：/api/bilibili-auth 二维码生成、状态轮询与 Cookie 保存
```

### 3.2 数据存储设计

| 数据项 | 存储路径 / 配置 Key | 说明 |
|---|---|---|
| 视频下载主目录 | `DATA_DIR / "bilibili_downloads"` | 文件树：`bilibili_downloads/<UP主昵称>/<视频标题>_BVxxx/<视频文件名>.mp4` |
| 收藏UP主文件 | `DATA_DIR / "bilibili_accounts.json"` | UP主信息列表（以 `mid` 作为唯一标识） |
| 下载历史记录 | `DATA_DIR / "bilibili_history.json"` | 下载记录数组 `{title, bvid, type(视频), author, path, size, time, success, error}` |
| Cookie 配置键 | `app_settings.json` 中 `bilibili_cookie` | 存入整个 Cookie 字符串（包含 `SESSDATA`、`bili_jct`、`DedeUserID`） |
| 下载清晰度倾向 | `app_settings.json` 中 `bili_video_quality` | 默认 `qn=80`（1080P 高清），有 VIP 时支持 `qn=120` (4K) 等 |
| 是否下载弹幕 | `app_settings.json` 中 `bili_download_danmaku` | 默认 `true`（自动转为 ASS） |
| 是否下载字幕 | `app_settings.json` 中 `bili_download_subtitle` | 默认 `true`（自动转为 SRT/ASS） |

---

## 4. 关键 API 原理与接口逻辑（基于 yutto）

### 4.1 WBI 签名计算原理

B 站很多重要 API（例如 UP 主主页搜索视频 `/x/space/wbi/arc/search`）都需要使用 WBI 签名。WBI 签名的计算流程如下：
1. 请求 `https://api.bilibili.com/x/web-interface/nav` 获取当前用户的 WBI 密钥：`data.wbi_img.img_url` 与 `data.wbi_img.sub_url`。
2. 从两个 URL 的末尾截取文件名，组合成 `img_key` 和 `sub_key`（例如：URL `.../hash_img.png`，截取得到 `hash_img`）。
3. 按照预设的映射规则打乱 `img_key + sub_key`，取前 32 位生成 `mixin_key`。
4. 将所有请求参数（加上当前时间戳 `wts`）按 Key 的字典序排列，使用 `&` 拼接，过滤掉 `!'()*` 等非法字符。
5. 将拼接后的查询字符串与 `mixin_key` 相连，进行 MD5 校验得到 `w_rid`，将其作为签名参数追加至请求中。

*实现提示：可以直接参考 `scratch/yutto/src/yutto/api/user_info.py` 中的 `encode_wbi` 函数。*

### 4.2 音视频下载与合并机制

B 站视频为 DASH 格式时，视频轨与音频轨完全分离。
1. **DASH 地址获取**：
   请求 `https://api.bilibili.com/x/player/playurl`，传入 `bvid`、`cid`、`qn=127` 及 `fnval=4048`。
   解析 JSON 中 `data.dash.video` 获取视频切片地址，`data.dash.audio` 获取音频切片地址。
2. **下载**：
   配置 `Referer: https://www.bilibili.com/` 请求头以防盗链。支持多线程 Range 下载。将视频轨存为 `<filename>_video.m4s`，音频轨存为 `<filename>_audio.m4s`。
3. **合并**：
   下载完成后，调用系统 `ffmpeg` 对轨道进行无损混流：
   ```bash
   ffmpeg -y -i <filename>_video.m4s -i <filename>_audio.m4s -c:v copy -c:a copy <output_filename>.mp4
   ```
4. **清理**：
   合并成功后自动删除 `.m4s` 临时文件。

### 4.3 弹幕与字幕下载流程

- **弹幕获取**：
  请求 `https://comment.bilibili.com/{cid}.xml` 获取基础弹幕。
  使用 Python 解析 XML 文件中各 `<d>` 标签（属性中含有出现时间、模式、字号、颜色等参数），输出为标准的 ASS 弹幕字幕文件，字体大小及滚动速度规则可参照 yutto 中 `write_danmaku` 逻辑。
- **字幕获取**：
  在 `x/web-interface/view` 接口的 `subtitle.subtitles` 列表中可以读取各个语言的 JSON 格式字幕地址。
  直接下载该 JSON 并还原为标准 SRT 格式文件。

---

## 5. 后端 API 路由规范

### 5.1 登录管理蓝图 (`/api/bilibili-auth`)

- **`GET /status`**
  - **说明**：获取当前 B 站登录状态。
  - **实现**：请求 B 站接口 `https://api.bilibili.com/x/web-interface/nav`（带上 local cookie）。如果 `code == 0`，则表明已登录，返回用户信息（用户名、头像、VIP状态）。
- **`GET /qrcode/generate`**
  - **说明**：获取扫码登录所需的二维码。
  - **实现**：调用 `https://passport.bilibili.com/x/passport-login/web/qrcode/generate`，获取返回的 `url`（用于生成二维码）和 `qrcode_key`（用于后续状态轮询）。
- **`POST /qrcode/poll`**
  - **说明**：轮询扫码状态。
  - **参数**：`{ qrcode_key }`
  - **实现**：请求 `https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=xxx`。
    - 返回 `code=86101`（未扫码）、`code=86090`（已扫码未确认）、`code=86038`（二维码超时已失效）。
    - 返回 `code=0` 表示扫码成功。在响应体的 Headers 中提取 `Set-Cookie`（包含 `SESSDATA` 和 `bili_jct` 等），并自动写入到 `app_settings.json`。
- **`POST /save-cookie`**
  - **说明**：手动粘贴 Cookie。
  - **参数**：`{ cookie }`
  - **实现**：解析传入的 Cookie 字符串，校验核心字段并将其保存至设置中。
- **`POST /logout`**
  - **说明**：退出登录。
  - **实现**：清除本地设置中的 `bilibili_cookie`。

### 5.2 核心业务蓝图 (`/api/bilibili`)

- **`POST /detect-url`**
  - **说明**：识别输入的 B 站链接类型并返回预览基本信息。
  - **实现**：匹配输入的正则判定是单视频 (AV/BV)、番剧剧集 (ep/ss)、个人空间主页 (space)、收藏夹 (fid) 还是合集。返回类型识别结果及基本封面、标题信息。
- **`POST /download-single`**
  - **说明**：解析并下载单个视频（支持多 P 选集）。
  - **参数**：`{ url, selected_pages: [1, 2...] }`
  - **实现**：启动后台线程处理任务：获取视频详情 `get_ugc_video_list` → 获取 DASH 地址 → 下载 M4S 轨道 → 使用 FFmpeg 合并 → 异步写入下载历史，并保存弹幕与字幕。
- **`GET /accounts`**
  - **说明**：获取收藏的 UP 主列表。
  - **实现**：读取并解析 `bilibili_accounts.json`。
- **`POST /accounts/parse`**
  - **说明**：解析输入的 UP 主空间链接，以供预览展示。
  - **参数**：`{ url }`
  - **实现**：从 URL 提取 MID，若未登录或未带 Cookie 则直接以 WBI 请求 `https://api.bilibili.com/x/space/wbi/acc/info?mid=xxx` 获取 UP 主昵称、头像和个人简介。
- **`POST /accounts`**
  - **说明**：将 UP 主添加至收藏列表。
- **`DELETE /accounts/<mid>`**
  - **说明**：取消收藏某个 UP 主。
- **`GET /accounts/<mid>/videos`**
  - **说明**：分页获取某个已收藏 UP 主的投稿视频列表（用于界面勾选下载）。
  - **参数**：`?page=1&page_size=30`
  - **实现**：请求 `https://api.bilibili.com/x/space/wbi/arc/search?mid=xxx&pn=xxx&ps=30`（需要 WBI 签名），返回稿件的 `bvid`、`title`、`pic` 等。
- **`POST /download-batch`**
  - **说明**：批量提交下载视频任务。
  - **参数**：`{ urls: [...] }`
- **`GET /download-status/<task_id>`**
  - **说明**：轮询后台下载任务执行状态。
- **`POST /download-cancel/<task_id>`**
  - **说明**：向后台执行线程发送取消信号。
- **`GET /history`** 与 **`DELETE /history`**
  - **说明**：获取及清理 B 站的下载历史。

---

## 6. 前端 SPA 页面结构设计

前端新增 5 个 JS 组件文件，遵循原有的 React-like 原生 JS 页面组件结构：

### 6.1 登录管理页 (`bili_login.js`)
- **UI 布局**：
  - 左侧展示扫码登录面板，支持动态生成二维码。扫码成功后自动展示用户昵称、头像以及 VIP 会员状态标识。
  - 右侧提供「手动粘贴 Cookie」卡片，并附带保姆级教程说明。
- **数据交互**：
  - 点击「扫码登录」按钮触发二维码生成接口，前端定时器以 1.5 秒间隔轮询 `/qrcode/poll` 状态，更新提示（如：“已扫码，请在手机确认”）。
  - 成功登录后清除定时器，调用 `/status` 刷新主页状态卡片。

### 6.2 UP主管理页 (`bili_accounts.js`)
- **UI 布局**：
  - 顶部搜索区域：输入 B 站主页 URL（如 `space.bilibili.com/2`）或者直接输入 MID。
  - 输入后弹出对话框预览目标 UP 主，点击确认将其加入收藏。
  - 下方卡片式展示已收藏的 UP 主列表，支持一键点击「进入投稿列表」和「取消收藏」。
- **防盗链处理**：
  - B 站的头像图片带有防盗链策略。前端可通过给 `<img>` 标签加入属性 `referrerpolicy="no-referrer"` 或配置全局 Referrer Policy 来保证图像正确显示。

### 6.3 视频下载页 (`bili_videos.js`)
- **UI 布局**：
  - 顶部使用下拉列表快速切换已收藏的 UP 主。
  - 选定 UP 主后，以大卡片/封面图的网格布局渲染其最新投稿。
  - 每个卡片包含多选框（Checkbox）、标题、发布时间、视频时长。
  - 支持全选、反选操作。点击「下载选中」可合并触发 `/download-batch` 请求并弹出通用任务进度条。

### 6.4 链接下载页 (`bili_download.js`)
- **UI 布局**：
  - 一个大型文本域（Textarea），支持用户粘贴混合文本（支持多行粘贴，自动过滤提取其中的 `BV`/`AV` 等关键标识）。
  - 支持在下方选择解析偏好配置（如：“仅下载音频”、“保留临时音视频轨”、“下载弹幕/字幕”）。
  - 点击「解析下载」，开启后台批量任务，并同步开启 1 秒间隔的任务进度轮询面板。

### 6.5 下载历史页 (`bili_history.js`)
- **UI 布局**：
  - 展示所有 B 站已完成的视频文件列表（包括视频名称、体积、下载时间）。
  - 操作区域支持：
    - **打开目录**：调用后端 `/open-folder` 在本地 Finder/资源管理器中选中并打开目标文件夹。
    - **删除记录**：删除在 JSON 中的下载记录；并提示用户是否连带删除本地磁盘上的实际视频及弹幕文件。

---

## 7. 异常处理与防风控建议

1. **接口异常友好提示**：
   - 遭遇 B 站接口返回 `-401`（非法 / 未登录）、`-412`（请求过于频繁被拦截/触发风控提示）时，应拦截错误，告知用户：“触发安全验证，请在登录管理页更新 Cookie 或更换代理 IP”。
2. **下载频率间隔控制**：
   - 在后台多线程并发下载时，严禁使用极高线程压测 B 站服务器。
   - 建议批量下载之间休眠 `1.5 - 3` 秒，分片 Range 获取加入抖动，尽可能伪装为普通网页端观看请求（携带正确的 `Sec-Ch-Ua`、`User-Agent`、`Referer` 头信息）。
3. **FFmpeg 检测**：
   - 在首次进入 B 站功能时，调用本地系统命令行 `ffmpeg -version` 检测是否正确配置了 FFmpeg 工具链。如果系统没有 FFmpeg，则将合并步骤降级，并提醒用户：“本地未安装 FFmpeg，仅下载了音视频分离的 M4S 切片，请参考 BUILD.md 安装 FFmpeg”。
