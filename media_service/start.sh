#!/usr/bin/env zsh
# ai-media 媒体子服务（图片检索 + 鉴黄 + 全景预览）启停脚本
# 用法: ./start.sh [start|stop|restart|status|logs]   默认 start
# 端口: ${WX_PORT:-8765}（也可用 PORT 环境变量覆盖，两者取 WX_PORT 优先）
# 日志: logs/media.log；健康检查: /api/health
set -euo pipefail
cd "${0:A:h}"

HOST="${HOST:-127.0.0.1}"
PORT="${WX_PORT:-${PORT:-8765}}"
LOG_DIR="logs"
LOG_FILE="$LOG_DIR/media.log"
PID_FILE="$LOG_DIR/media.pid"
BASE_URL="http://$HOST:$PORT"

mkdir -p "$LOG_DIR"

is_running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

cmd_start() {
  if is_running; then
    echo "已在运行 (pid $(cat "$PID_FILE")) -> $BASE_URL"
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    echo "找不到 uv，请先安装（brew install uv）" >&2
    return 1
  fi

  # 后台启动 uvicorn，日志追加写入 media.log；就绪前等待 /api/health
  nohup uv run uvicorn app.main:app --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "启动中: 媒体子服务 pid $(cat "$PID_FILE")，日志 $LOG_FILE"

  local i
  for i in $(seq 1 60); do
    if curl -fsS -m 3 "$BASE_URL/api/health" >/dev/null 2>&1; then
      echo "就绪 -> $BASE_URL"
      cmd_status
      return 0
    fi
    if ! is_running; then
      echo "启动失败，日志尾部：" >&2
      tail -n 20 "$LOG_FILE" >&2 || true
      rm -f "$PID_FILE"
      return 1
    fi
    sleep 0.5
  done
  echo "30 秒内未就绪，日志尾部：" >&2
  tail -n 20 "$LOG_FILE" >&2 || true
  return 1
}

cmd_stop() {
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "已停止媒体子服务 pid $pid"
  else
    echo "媒体子服务未在运行"
  fi
  rm -f "$PID_FILE"
}

cmd_status() {
  if is_running; then
    echo "媒体子服务: 运行中 pid $(cat "$PID_FILE")"
  else
    echo "媒体子服务: 未运行"
  fi
  if h="$(curl -fsS -m 3 "$BASE_URL/api/health" 2>/dev/null)"; then
    echo "健康: $h"
  else
    echo "健康: 不可达"
  fi
  ls -lh "$LOG_FILE" 2>/dev/null || true
}

case "${1:-start}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status) cmd_status ;;
  logs) tail -n 50 -f "$LOG_FILE" ;;
  *)
    echo "用法: ./start.sh [start|stop|restart|status|logs]" >&2
    exit 2
    ;;
esac
