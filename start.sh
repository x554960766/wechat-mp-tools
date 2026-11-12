#!/usr/bin/env bash
# wmp 宿主（含 ai-media 媒体子服务）启停脚本
# 用法: ./start.sh [start|stop|restart|status|logs|logs-media]   默认 start
# 日志: logs/wmp.log（宿主）、logs/media.log（媒体子服务，含启动失败原因）
set -euo pipefail
cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5200}"
LOG_DIR="${LOG_DIR:-logs}"
LOG_FILE="$LOG_DIR/wmp.log"
MEDIA_LOG="$LOG_DIR/media.log"
PID_FILE="$LOG_DIR/wmp.pid"
PYTHON="${PYTHON:-.venv/bin/python}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
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
  if [ ! -x "$PYTHON" ]; then
    echo "找不到可执行 Python: $PYTHON" >&2
    echo "先装依赖: uv pip install --python .venv/bin/python -r requirements.txt" >&2
    return 1
  fi

  local args=(app.py --host "$HOST" --port "$PORT")
  [ "$OPEN_BROWSER" = "1" ] || args+=(--no-browser)

  MEDIA_PORT="${MEDIA_PORT:-8765}" \
  MEDIA_LOG_PATH="$PWD/$MEDIA_LOG" \
  nohup "$PYTHON" "${args[@]}" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "启动中: 宿主 pid $(cat "$PID_FILE")，日志 $LOG_FILE / $MEDIA_LOG"

  local i
  for i in $(seq 1 60); do
    if curl -fsS -m 3 "$BASE_URL/healthz" >/dev/null 2>&1; then
      echo "就绪 -> $BASE_URL"
      cmd_status
      return 0
    fi
    if ! is_running; then
      echo "启动失败，宿主日志尾部：" >&2
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
  local pid media_pid
  if is_running; then
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "已停止宿主 pid $pid"
  else
    echo "宿主未在运行"
  fi
  rm -f "$PID_FILE"

  # 宿主被强杀时子进程可能残留，按 pid 文件兜底回收
  if [ -f data/media/media.pid ]; then
    media_pid="$(cat data/media/media.pid 2>/dev/null || true)"
    if [ -n "$media_pid" ] && kill -0 "$media_pid" 2>/dev/null; then
      kill "$media_pid" 2>/dev/null || true
      echo "已停止残留媒体子进程 pid $media_pid"
    fi
    rm -f data/media/media.pid
  fi
}

cmd_status() {
  if is_running; then
    echo "宿主: 运行中 pid $(cat "$PID_FILE")"
  else
    echo "宿主: 未运行"
  fi
  local h
  if h="$(curl -fsS -m 3 "$BASE_URL/healthz" 2>/dev/null)"; then
    echo "健康: $h"
  else
    echo "健康: 不可达"
  fi
  ls -lh "$LOG_FILE" "$MEDIA_LOG" 2>/dev/null || true
}

case "${1:-start}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status) cmd_status ;;
  logs) tail -n 50 -f "$LOG_FILE" ;;
  logs-media) tail -n 50 -f "$MEDIA_LOG" ;;
  *)
    echo "用法: ./start.sh [start|stop|restart|status|logs|logs-media]" >&2
    exit 2
    ;;
esac
