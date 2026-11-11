# 微信公众号 / 视频号 / 抖音 / 快手 / 小红书 / 哔哩哔哩 下载与转码工具箱

一款功能强大的全能多平台内容下载、归档备份与 RSS 订阅工具箱。支持 Windows / macOS 客户端桌面开箱即用，同时支持 Docker 容器化服务端部署。

> [!TIP]
> **桌面端开箱即用，无需配置 Python 开发环境！**
> 本项目已通过 GitHub Actions 自动构建 Windows 与 macOS（同时支持 Apple Silicon ARM64 及 Intel x86_64 架构）客户端。可直接前往 [👉 GitHub Releases 下载最新客户端](https://github.com/x554960766/wechat-mp-tools/releases)。

---

## 🌟 核心功能特性

### 📱 微信公众号

> [!TIP]
> **降低风控建议**：强烈建议在「账号池」中扫码添加 2~3 个账号（小号/备用号）。系统会自动多账号轮换、负载均衡并智能退避，大幅提升大批量抓取稳定性并规避频控限制。

- **批量与一键增量采集**：支持按时间范围/数量全量抓取，支持单号或全部关注公众号一键采集最新 50 篇，自动跳过本地已下载文章。
- **多格式导出与精准展示**：支持离线 HTML、语义 Markdown 与矢量 PDF 导出；推文发布时间精确到分钟，精准识别原创标记；内置广告与引导关注内容清洗。
- **标准 RSS 2.0 订阅**：为各公众号提供独立与全聚合专属 Feed，支持后台定时增量抓取、全文/摘要输出及访问令牌鉴权。
- **多级风控自愈与双数据库**：适配上游频控（-2014）与人机验证（-2041）自愈；默认内置零配置 SQLite，支持无缝切换 MySQL 且具备平滑容灾回退。

### 🎥 微信视频号
- **单视频解析下载**：粘贴分享链接一键无水印下载高清视频。
- **作者主页与关注列表采集**：支持收藏博主，批量下载作者全部作品；支持一键增量同步已关注博主的新发布作品。
- **本地历史自动同步**：智能识别本地已下载文件并自动补齐下载记录。

### 🎵 抖音
- **无水印视频与实况照片**：支持单视频、高清图集以及 Live Photo 实况照片（原图 + 动态短视频 + 原生 BGM）无水印下载。
- **评论深度抓取**：完整保留一级主评与二级楼中楼回复，支持自定义抓取条数并同目录导出 JSON 数据。
- **批量采集与收藏同步**：支持创作者主页全量作品采集、个人公开/私密喜欢列表下载、收藏夹及合集播单批量备份。
- **直播间与回放解析**：支持直播间实时流解析及历史直播回放列表采集。

### ⚡ 快手
- **高清音视频无水印解析**：支持单视频、图集无水印下载及批量采集。
- **断点续传与重试自愈**：内置分段流式下载与断点续传机制，大文件下载稳定可靠。

### 📕 小红书
- **多类型笔记解析**：支持图文笔记、视频以及 Live 实况图无水印下载，同步导出正文文案与离线网页。
- **博主主页深度采集**：一键获取博主主页所有笔记作品，支持多候选 CDN 自动重试与高清流优选。

### 📺 哔哩哔哩
- **单视频 / 多分P / 番剧下载**：支持 BV/AV 号输入，按需选择或批量下载多分P剧集。
- **UP 主空间投稿批量采集**：一键拉取 UP 主全部作品列表，支持增量下载与自动去重。
- **最高画质混流与弹幕字幕**：自动合并最高清画质音视频轨；支持将弹幕导出为 ASS 格式，支持提取 CC 字幕为 SRT/ASS 文件。

### 🎬 视频转码与空间优化
- **全格式互转与音频提取**：支持 MP4、MKV、MOV、WebM、AVI、FLV、TS 等格式互转及提取 MP3 音频。
- **硬件加速与高压缩比**：支持 H.264 / H.265 硬件加速转码，大幅节省磁盘空间并实时显示压缩比率。
- **一键批量扫描**：自动递归扫描本地所有已下载媒体，一键批量处理。

---

## 🐳 Docker 部署（服务端 / NAS / VPS）

本项目全面支持容器化部署，提供轻量版（专注公众号 + RSS）与全功能版两种运行形态。

### 1. ⚡ 一键极速部署（推荐）

#### 方式 A：Docker CLI 一行命令启动（开箱即用）
无需先配置复杂的环境或目录，只需执行一行命令即可在后台启动：
```bash
docker run -d \
  --name wechat-mp-tools \
  --restart unless-stopped \
  -p 5200:5200 \
  -v $(pwd)/data:/app/data \
  -e TZ=Asia/Shanghai \
  -e BIND_ADDR=0.0.0.0 \
  -e DB_TYPE=sqlite \
  ghcr.io/x554960766/wechat-mp-tools:latest
```

#### 方式 B：Docker Compose 一键构建与启动
```bash
# 1. 克隆代码进入部署目录并一键启动
git clone https://github.com/x554960766/wechat-mp-tools.git
cd wechat-mp-tools/deploy/docker
cp -n .env.example .env && mkdir -p data
docker compose up -d --build
```
启动完成后，在浏览器中访问：`http://<服务器IP>:5200` 即可开始使用。

---

### 2. 🗄️ 数据库配置（SQLite 与 MySQL）

本系统在 Docker 容器化环境下完美支持 **SQLite** 与 **MySQL** 双引擎：

- **方式 1：默认本地 SQLite（推荐，零配置）**
  无需任何额外设置，容器默认使用内置 SQLite 存储，数据库文件直接落在宿主机持久化卷 `./data/wechat_tools.db`，容器重启或重建数据丝毫不丢。

- **方式 2：连接外部/局域网 MySQL（NAS、RDS、自建数据库）**
  在 `deploy/docker/.env` 或运行参数中传入以下环境变量即可自动切换：
  ```ini
  DB_TYPE=mysql
  DB_HOST=192.168.1.100    # MySQL 宿主机或服务 IP
  DB_PORT=3306
  DB_USER=root
  DB_PASSWORD=your_password
  DB_NAME=wechat_tools
  ```

- **方式 3：Docker Compose 一键附带启动 MySQL 8 服务**
  `docker-compose.yaml` 内置了独立的 MySQL 8 服务，执行以下命令即可一键同时拉起应用容器与 MySQL 容器：
  ```bash
  # 启用 with-mysql profile 一键启动应用与 MySQL
  docker compose --profile with-mysql up -d
  ```
  在 `.env` 中指定 `DB_TYPE=mysql`、`DB_HOST=db` 即可直接容器间内网互联。

*安全保障：系统内置多级平滑容灾机制。若 MySQL 服务在启动初期尚未就绪或网络闪断，程序会自动平滑降级至本地 SQLite 守护业务，待 MySQL 恢复后自动重新握手。*

---

### 3. 镜像形态切换说明

编辑 `deploy/docker/.env` 文件可灵活切换部署形态：

| 形态 | 配置参数 (`.env`) | 特点说明 |
| :--- | :--- | :--- |
| **公众号 + RSS 版**<br>*(默认)* | `DOCKERFILE=deploy/docker/Dockerfile`<br>`IMAGE=wechat-mp-tools-mp:latest` | **镜像小（约 100MB）、内存占用极低**；包含公众号登录、文章拉取、HTML/Markdown 导出、内容过滤与 RSS 服务。 |
| **全功能版** | `DOCKERFILE=deploy/docker/Dockerfile.full`<br>`IMAGE=wechat-mp-tools-full:latest` | 包含所有平台功能（公众号 + 抖音 + 快手 + 小红书 + B站 + 视频号 + 转码工具）。 |
| **PDF 导出支持** | `INSTALL_BROWSERS=1` | 安装 Chromium 内核以启用公众号文章导出为 PDF 功能（体积增大约 1.5GB）。 |

### 4. 数据持久化与常用参数

容器运行仅需持久化挂载 `./data` 目录（存放登录凭据、系统配置、下载历史、RSS 缓存及下载文章）：
- `PORT`：服务端监听端口（默认 `5200`）
- `BIND_ADDR`：绑定地址（如需局域网或公网直接访问可设为 `0.0.0.0`，默认 `127.0.0.1` 配合 Nginx 反向代理）
- `RSS_BASE_URL`：可选，反向代理时指定对外公开的 RSS 基准网址（前端复制订阅链接及 XML 导出的图片、链接将统一使用该域名）
- `RSS_TOKEN`：可选，为 RSS 订阅源设置访问密钥（设置后需在订阅链接后附加 `?token=your_secret`）
- `ADMIN_PASSWORD`：可选，Web 管理后台访问认证密码。若直接暴露公网，建议配置启用 HTTP Basic 密码保护（注：RSS 订阅不受此影响，继续由 `RSS_TOKEN` 独立保护）
- `DB_TYPE`：数据库引擎类型（`sqlite` 或 `mysql`，默认 `sqlite`）

### 5. 反向代理与公网安全最佳实践 (Lucky / Nginx / Caddy)

当你在家里的 NAS 或服务器部署并通过 Lucky / Nginx 暴露至公网供外部 RSS 阅读器使用时，建议采用以下安全实践：

- **方案 A：最小暴露面隔离（强烈推荐）**
  - **Lucky / Nginx 规则中仅反代以下 3 个路径**：
    1. `/api/articles/rss`（RSS 订阅源）
    2. `/api/articles/avatars/`（公众号头像静态资源）
    3. `/api/articles/serve-file/`（文章离线图片与 HTML 静态资源）
  - **管理后台完全留在内网**：根路径 `/`、账号管理、扫码登录等接口不配置公网转发，公网无法接触到后台管理，实现物理级网络隔离。
- **方案 B：全量反代 + ADMIN_PASSWORD 防御纵深**
  - 若反代了整个站点根路径，务必在 `.env` 或系统设置中配置强密码 `ADMIN_PASSWORD`。系统会自动拦截未授权的后台管理访问，外部 RSS 阅读器抓取则自动放行。

---

## 💻 桌面端客户端安装

在 [Releases 发布页面](https://github.com/x554960766/wechat-mp-tools/releases) 提供以下安装包：

| 版本类型 | 体积大小 | 内置浏览器内核 | 运行要求 | 推荐场景 |
| :--- | :--- | :--- | :--- | :--- |
| **Full (完整版)** | 约 200 MB ~ 240 MB | ✅ 内置独立 Chromium | 零外部依赖，解压即用 | **强烈推荐大部分用户选择**，运行最稳定 |
| **Lite (轻量版)** | 约 40 MB ~ 60 MB | ❌ 不内置 | 电脑已装有 Chrome 或 Edge | 适合网络带宽有限或追求小体积的用户 |

---

## 🛠️ 本地源码运行与开发

如果你希望直接运行源码：

```bash
# 1. 克隆代码
git clone https://github.com/x554960766/wechat-mp-tools.git
cd wechat-mp-tools

# 2. 安装 Python 依赖
pip install -r requirements.txt
playwright install chromium

# 3. 运行服务
python app.py     # 完整版 Web 界面（访问 http://localhost:5200）
# 或
python app_mp.py  # 仅公众号与 RSS 服务端（访问 http://localhost:5200）
```

---

## 📂 数据存储目录

- **桌面客户端**：
  - macOS: `~/Library/Application Support/WeChat MP Tools/data/`
  - Windows: 程序同级目录下的 `data/`
- **Docker 容器**：映射到宿主机挂载的 `./data` 目录

---

## ⚠️ 免责声明

1. 本项目所有功能仅供个人学习、技术研究与离线备份使用，严禁用于任何商业用途或非法牟利行为。
2. 使用本工具下载内容时，请严格遵守相关平台的用户服务协议及法律法规。因不当使用所引发的账号限制或法律纠纷，由使用者本人承担全部责任。
3. 本项目为开源软件，按“现状”提供，不包含任何形式的明示或暗示担保。
