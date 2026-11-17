#!/usr/bin/env bash
# wechat-mp-tools 一键启停
#
#   宿主        公众号 / 视频号 / 抖音 / 小红书 / 快手 / B 站 下载与管理
#   媒体子服务  素材检索、图片鉴黄初筛、VR 全景预览、视频抽帧检索
#   NAS 归档    各平台下载成功后把作品副本归档到 NAS（不删本地）
#
# 用法: ./start.sh [命令]          默认 start
#   setup      首次安装 / 补齐两个 venv 的依赖（需要 uv）
#   doctor     环境自检（依赖 / 端口 / 模型 / ffmpeg / NAS 工具），只读，不改状态
#   start      后台启动宿主（媒体子服务由宿主自动拉起），等就绪后打印地址
#   stop       停止宿主与媒体子进程
#   restart    stop + start
#   status     运行状态 + 媒体子服务健康 + NAS 归档台账计数
#   logs       跟踪宿主日志
#   logs-media 跟踪媒体子服务日志
#
# 常用可覆盖环境变量:
#   HOST=127.0.0.1  PORT=5200  MODE=full|mp  OPEN_BROWSER=1
#   MEDIA_PORT=8765          媒体子服务端口；不设置时从 8765 起自动找空闲端口
#   WX_NAS_ROOT=/sata11/my/data/公众号归档   归档目标路径
#   WX_NAS_TARGET_ROOT=wx-archive            归档目标根 id（媒体服务侧的根名）
#   WX_NAS_ARCHIVE=1                        0 = 关闭「下载后自动归档」
#   AI_MEDIA_NSFW_SCAN=1                    0 = 关闭扫描期鉴黄初筛
#   AI_MEDIA_FRAME_INTERVAL=10              视频抽帧间隔（秒）
#
# 日志: logs/wmp.log（宿主）、logs/media.log（媒体子服务，含启动失败原因）
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5200}"
MODE="${MODE:-full}"
LOG_DIR="${LOG_DIR:-logs}"
LOG_FILE="$LOG_DIR/wmp.log"
MEDIA_LOG="$LOG_DIR/media.log"
PID_FILE="$LOG_DIR/wmp.pid"
MEDIA_PID_FILE="data/media/media.pid"
PYTHON="${PYTHON:-.venv/bin/python}"
MEDIA_PYTHON="media_service/.venv/bin/python"
OPEN_BROWSER="${OPEN_BROWSER:-1}"

# NAS 自动归档：目标根、路径与开关（写进子进程环境，宿主再透传给媒体子服务）
WX_NAS_ROOT="${WX_NAS_ROOT:-/sata11/my/data/公众号归档}"
WX_NAS_TARGET_ROOT="${WX_NAS_TARGET_ROOT:-wx-archive}"
WX_NAS_ARCHIVE="${WX_NAS_ARCHIVE:-1}"
AI_MEDIA_NSFW_SCAN="${AI_MEDIA_NSFW_SCAN:-1}"
AI_MEDIA_FRAME_INTERVAL="${AI_MEDIA_FRAME_INTERVAL:-10}"

case "$MODE" in
  full) APP_ENTRY="app.py" ;;
  mp)   APP_ENTRY="app_mp.py" ;;
  *) echo "MODE 只能是 full 或 mp（当前 $MODE）" >&2; exit 2 ;;
esac

BASE_URL="http://$HOST:$PORT"
mkdir -p "$LOG_DIR"

# ---------- 小工具 ----------

is_running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

pid_alive() {
  local pid="${1:-}"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

health_json() {
  curl -fsS -m 3 "$BASE_URL/healthz" 2>/dev/null || true
}

# 输出 "<state> <ready> <port>"；不可达则无输出
media_state() {
  health_json | "$PYTHON" -c 'import json, sys
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit
try:
    media = (json.loads(raw).get("media") or {})
except Exception:
    raise SystemExit
print(media.get("state", ""), "true" if media.get("ready") else "false", media.get("port", ""))' 2>/dev/null || true
}

media_ready() {
  local out state ready _
  out="$(media_state)" || return 1
  [ -n "$out" ] || return 1
  read -r state ready _ <<<"$out" || true
  [ "${state:-}" = "running" ] && [ "${ready:-}" = "true" ]
}

# NAS 归档台账摘要（宿主侧只读 REST，一行文案）
nas_status_line() {
  curl -fsS -m 3 "$BASE_URL/api/media/nas-archive/status" 2>/dev/null | "$PYTHON" -c 'import json, sys
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(1)
try:
    data = json.loads(raw)
except Exception:
    print("解析失败")
    raise SystemExit(0)
counts = data.get("counts") or {}
parts = [key + "=" + str(val) for key, val in sorted(counts.items()) if val]
state = "enabled" if data.get("enabled") else "disabled"
detail = ", ".join(parts) if parts else "暂无记录"
print(state + ", " + detail + ", target=" + str(data.get("target_root") or "-"))'
}

port_free() {
  # 不用 SO_REUSEADDR：macOS 上它会让「别的进程监听 0.0.0.0:port」被误判成空闲
  "$PYTHON" -c 'import socket, sys
sock = socket.socket()
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()' "$1" 2>/dev/null
}

# 媒体子服务端口：显式指定就尊重（占用只告警），否则从 8765 起找第一个空闲端口
pick_media_port() {
  local p
  if [ -n "${MEDIA_PORT:-}" ]; then
    if port_free "$MEDIA_PORT"; then
      printf '%s' "$MEDIA_PORT"
    else
      echo "警告: 指定的 MEDIA_PORT=$MEDIA_PORT 已被占用，仍按此端口启动" >&2
      printf '%s' "$MEDIA_PORT"
    fi
    return 0
  fi
  for p in $(seq 8765 8795); do
    if port_free "$p"; then
      printf '%s' "$p"
      return 0
    fi
  done
  echo "8765-8795 全部被占用，请用 MEDIA_PORT 指定一个空闲端口" >&2
  return 1
}

# 宿主可执行文件与两个 venv 是否齐备
preflight() {
  local ok=0
  if [ ! -x "$PYTHON" ]; then
    echo "缺少宿主 Python 环境: $PYTHON" >&2
    echo "  先运行: ./start.sh setup" >&2
    ok=1
  fi
  if [ ! -x "$MEDIA_PYTHON" ]; then
    echo "缺少媒体子服务 Python 环境: $MEDIA_PYTHON" >&2
    echo "  先运行: ./start.sh setup" >&2
    ok=1
  fi
  return "$ok"
}

# ---------- 命令 ----------

cmd_setup() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "缺少 uv，请先安装：brew install uv" >&2
    return 1
  fi
  if [ ! -x "$PYTHON" ]; then
    echo "== 创建宿主 venv =="
    uv venv --python 3.12 .venv
  fi
  echo "== 安装宿主依赖 =="
  uv pip install --python "$PYTHON" -r requirements.txt
  # pytest 只用于本地跑测试，不进 requirements，避免把开发依赖带进部署
  uv pip install --python "$PYTHON" pytest
  echo "== 安装媒体子服务依赖（含鉴黄/测试 extra）=="
  ( cd media_service && uv sync --extra review --extra test )
  echo
  echo "完成。接下来: ./start.sh doctor"
}

cmd_doctor() {
  local problems=0
  say() { printf '  %s：%s\n' "$1" "$2"; }

  echo "== 运行环境 =="
  if [ -x "$PYTHON" ]; then
    say "宿主 Python" "✓ $("$PYTHON" -V 2>&1)"
  else
    say "宿主 Python" "✗ 缺 $PYTHON（./start.sh setup）"; problems=$((problems + 1))
  fi
  if [ -x "$MEDIA_PYTHON" ]; then
    say "媒体子服务 Python" "✓ $("$MEDIA_PYTHON" -V 2>&1)"
  else
    say "媒体子服务 Python" "✗ 缺 $MEDIA_PYTHON（./start.sh setup）"; problems=$((problems + 1))
  fi
  if [ -f media_service/app/main.py ]; then
    say "媒体子服务代码" "✓ media_service/app/main.py"
  else
    say "媒体子服务代码" "✗ 缺 media_service/"; problems=$((problems + 1))
  fi

  echo
  echo "== 媒体能力依赖 =="
  if command -v ffmpeg >/dev/null 2>&1; then
    say "ffmpeg（视频抽帧）" "✓ $(command -v ffmpeg)"
  else
    say "ffmpeg（视频抽帧）" "! 未安装，视频画面检索不可用（brew install ffmpeg）"
  fi
  if [ -f data/media/nsfw.onnx ]; then
    say "鉴黄模型" "✓ data/media/nsfw.onnx ($(du -h data/media/nsfw.onnx | cut -f1))"
  else
    say "鉴黄模型" "! 缺 data/media/nsfw.onnx，扫描不跑鉴黄（功能降级，不影响其它）"
  fi
  if [ -x "$MEDIA_PYTHON" ] && "$MEDIA_PYTHON" -c 'import onnxruntime' 2>/dev/null; then
    say "onnxruntime" "✓ 已装"
  else
    say "onnxruntime" "! 未装，鉴黄初筛不可用（cd media_service && uv sync --extra review）"
  fi

  echo
  echo "== NAS 归档 =="
  local cli="${WX_NAS_CLI:-$HOME/.agents/skills/zspace-nas-agent-toolkit/scripts/zspace-cli}"
  if [ -x "$cli" ]; then
    say "zspace-cli" "✓ $cli"
    local probe
    if probe="$("$cli" status 2>&1)"; then
      if printf '%s' "$probe" | grep -q '"logged_in": *true'; then
        say "NAS 登录态" "✓ 已登录"
      else
        say "NAS 登录态" "! 未登录，归档会失败重试（不影响下载）"
      fi
    else
      say "NAS 登录态" "! status 调用失败，归档会失败重试（不影响下载）"
    fi
  else
    say "zspace-cli" "! 未找到，NAS 归档不可用（下载仍正常）"
  fi
  say "归档目标" "$WX_NAS_TARGET_ROOT → $WX_NAS_ROOT"
  say "自动归档开关" "WX_NAS_ARCHIVE=$WX_NAS_ARCHIVE"
  if [ -f data/nas_archive.jsonl ]; then
    say "归档台账" "data/nas_archive.jsonl ($(wc -l <data/nas_archive.jsonl | tr -d ' ') 条)"
  else
    say "归档台账" "尚未产生（首次下载成功后创建）"
  fi

  echo
  echo "== 端口 =="
  if port_free "$PORT"; then
    say "宿主端口 $PORT" "✓ 空闲"
  else
    say "宿主端口 $PORT" "✗ 被占用（换 PORT=xxxx）"; problems=$((problems + 1))
  fi
  if [ -n "${MEDIA_PORT:-}" ]; then
    if port_free "$MEDIA_PORT"; then
      say "媒体端口 $MEDIA_PORT" "✓ 空闲"
    else
      say "媒体端口 $MEDIA_PORT" "! 被占用（不设 MEDIA_PORT 可自动挑端口）"
    fi
  else
    local auto
    if auto="$(pick_media_port)"; then
      say "媒体端口" "✓ 将自动使用 $auto"
    else
      say "媒体端口" "✗ 8765-8795 全占"; problems=$((problems + 1))
    fi
  fi

  echo
  if [ "$problems" -gt 0 ]; then
    echo "有 $problems 个阻断项，先解决再 start。"
    return 1
  fi
  echo "自检通过。启动: ./start.sh start"
}

cmd_start() {
  if is_running; then
    echo "已在运行 (pid "$(cat "$PID_FILE")") -> $BASE_URL"
    media_ready && echo "媒体子服务就绪"
    return 0
  fi
  preflight || return 1

  local media_port
  media_port="$(pick_media_port)" || return 1

  local args=("$APP_ENTRY" --host "$HOST" --port "$PORT")
  [ "$OPEN_BROWSER" = "1" ] || args+=(--no-browser)

  MEDIA_PORT="$media_port" \
  MEDIA_LOG_PATH="$PWD/$MEDIA_LOG" \
  WX_NAS_ROOT="$WX_NAS_ROOT" \
  WX_NAS_TARGET_ROOT="$WX_NAS_TARGET_ROOT" \
  WX_NAS_ARCHIVE="$WX_NAS_ARCHIVE" \
  AI_MEDIA_NSFW_SCAN="$AI_MEDIA_NSFW_SCAN" \
  AI_MEDIA_FRAME_INTERVAL="$AI_MEDIA_FRAME_INTERVAL" \
  nohup "$PYTHON" "${args[@]}" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "启动中: 宿主 pid "$(cat "$PID_FILE")" ($APP_ENTRY, $BASE_URL)，媒体端口 $media_port"
  echo "日志: $LOG_FILE / $MEDIA_LOG"

  # 媒体子服务要起 uvicorn + 加载索引，宿主内部最多等 120s，这里给 130s
  local i
  for i in $(seq 1 260); do
    if ! is_running; then
      echo "启动失败：宿主进程已退出，日志尾部：" >&2
      tail -n 20 "$LOG_FILE" >&2 || true
      rm -f "$PID_FILE"
      return 1
    fi
    if media_ready; then
      echo "就绪 -> $BASE_URL"
      cmd_status
      return 0
    fi
    sleep 0.5
  done

  echo "130 秒内媒体子服务未就绪，日志尾部：" >&2
  tail -n 20 "$LOG_FILE" >&2 || true
  tail -n 20 "$MEDIA_LOG" >&2 || true
  return 1
}

cmd_stop() {
  local pid media_pid
  if is_running; then
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      pid_alive "$pid" || break
      sleep 0.5
    done
    pid_alive "$pid" && kill -9 "$pid" 2>/dev/null || true
    echo "已停止宿主 pid $pid"
  else
    echo "宿主未在运行"
  fi
  rm -f "$PID_FILE"

  # 宿主被强杀时子进程可能残留，按 pid 文件兜底回收
  if [ -f "$MEDIA_PID_FILE" ]; then
    media_pid="$(cat "$MEDIA_PID_FILE" 2>/dev/null || true)"
    if pid_alive "$media_pid"; then
      kill "$media_pid" 2>/dev/null || true
      for _ in $(seq 1 10); do
        pid_alive "$media_pid" || break
        sleep 0.3
      done
      pid_alive "$media_pid" && kill -9 "$media_pid" 2>/dev/null || true
      echo "已停止残留媒体子进程 pid $media_pid"
    fi
    rm -f "$MEDIA_PID_FILE"
  fi
}

cmd_status() {
  if is_running; then
    echo "宿主: 运行中 pid "$(cat "$PID_FILE")" -> $BASE_URL"
  else
    echo "宿主: 未运行"
  fi

  local state ready port
  read -r state ready port <<<"$(media_state)"
  if [ -n "${state:-}" ]; then
    echo "媒体子服务: ${state}（ready=${ready}, port=${port:-?}）"
  else
    echo "媒体子服务: 不可达（宿主未启动或反代未就绪）"
  fi

  echo "NAS 归档: $(nas_status_line || echo 不可达)"

  ls -lh "$LOG_FILE" "$MEDIA_LOG" 2>/dev/null || true
}

case "${1:-start}" in
  setup) cmd_setup ;;
  doctor) cmd_doctor ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status) cmd_status ;;
  logs) tail -n 50 -f "$LOG_FILE" ;;
  logs-media) tail -n 50 -f "$MEDIA_LOG" ;;
  *)
    echo "用法: ./start.sh [setup|doctor|start|stop|restart|status|logs|logs-media]" >&2
    exit 2
    ;;
esac
