#
# 只打包「公众号 + RSS」的独立服务（入口 app_mp.py）。
# 抖音 / 快手 / 小红书 / B 站 / 视频号 / 转码 / 桌面壳 的代码在构建阶段就被删掉，
# 最终镜像里根本不存在这些文件；依赖从 12 个降到 6 个。
#
# 实测体积（arm64）：
#   默认（无浏览器）解压 244 MB / 拉取 51 MB
#   带 Chromium     解压 1.96 GB / 拉取 536 MB
#
# 构建（在仓库根目录）：
#   docker build -f deploy/docker/Dockerfile -t wechat-mp-tools-mp:latest .
#
# 需要 PDF 导出时才装浏览器（+1.7 GB，换来 PDF 与风控兜底）：
#   docker build -f deploy/docker/Dockerfile --build-arg INSTALL_BROWSERS=1 -t wechat-mp-tools-mp:pdf .

# ── 阶段 1：裁剪 backend ──────────────────────────────────────────────
# 公众号版用不到的模块在这里删掉，最终镜像里不存在。
# 不用「主镜像里 RUN rm」是因为那只会在上层标记删除，文件仍留在 COPY 那一层，
# 解包镜像还能捞出来；用独立阶段 + COPY --from 才是真的没有。
FROM python:3.12-slim AS backend-src
WORKDIR /src
COPY backend ./backend
RUN rm -f \
        backend/channels.py backend/mitm_proxy.py \
        backend/douyin.py backend/douyin_auth.py backend/douyin_login.py \
        backend/douyin_mstoken.py backend/douyin_sign.py \
        backend/kuaishou.py backend/kuaishou_auth.py \
        backend/xiaohongshu.py backend/xiaohongshu_login.py \
        backend/bilibili.py backend/bilibili_login.py backend/bilibili_sign.py \
        backend/transcode.py backend/updater.py backend/weread_browser.py \
        backend/cred_redact.py backend/proxy_worker.py backend/refresh_queue.py \
        backend/sign.py \
    && find backend -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && echo "保留模块 $(ls backend/*.py | wc -l) 个"

FROM python:3.12-slim

ARG INSTALL_BROWSERS=0

# PLAYWRIGHT_BROWSERS_PATH 必须落在 /app 下，
# backend/runtime.py 的 bundled_browsers_available() 只认 resource_dir()/ms-playwright
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MALLOC_ARENA_MAX=2 \
    TZ=Asia/Shanghai \
    PORT=5200 \
    APP_ENTRY=app_mp.py \
    PLAYWRIGHT_BROWSERS_PATH=/app/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tini tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-mp.txt ./
# 精简模式不装 playwright：它自带 driver + node，光 Python 包就 136MB，
# 而下载/抓取走的是 requests，缺了它只会让「风控兜底」这条路径返回错误（已被 except 捕获）
RUN if [ "$INSTALL_BROWSERS" = "1" ]; then \
        pip install -r requirements-mp.txt ; \
    else \
        echo "精简模式：跳过 playwright" \
        && grep -viE '^[[:space:]]*playwright' requirements-mp.txt > /tmp/req-lite.txt \
        && pip install -r /tmp/req-lite.txt \
        && rm -f /tmp/req-lite.txt ; \
    fi

# 浏览器：PDF 导出走 Chrome 命令行的 --print-to-pdf，无头兜底走 Playwright
# 目录名随架构变化（amd64: chrome-linux / arm64: chrome-linux-arm64），所以用 glob 定位，
# 并软链到 /usr/local/bin/chromium，供 downloader.find_chromium_executable() 从 PATH 找到
RUN if [ "$INSTALL_BROWSERS" = "1" ]; then \
        python -m playwright install --with-deps chromium \
        && chmod -R a+rX /app/ms-playwright \
        && CHROME_BIN="$(ls -d /app/ms-playwright/chromium-*/chrome-linux*/chrome | head -n 1)" \
        && ln -sf "$CHROME_BIN" /usr/local/bin/chromium \
        && echo "chromium -> $CHROME_BIN" ; \
    else \
        echo "skip chromium: PDF 导出与无头浏览器兜底将不可用" ; \
    fi

COPY app_mp.py ./
# 从裁剪阶段拿 backend，镜像里不含抖音/快手/小红书/B站/视频号等模块
COPY --from=backend-src /src/backend ./backend
COPY frontend ./frontend

COPY deploy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /app/data

EXPOSE 5200

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
