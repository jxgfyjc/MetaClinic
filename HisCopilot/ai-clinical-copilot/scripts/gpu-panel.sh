#!/usr/bin/env bash
# gpu-panel.sh — start / stop / status for the GPU control panel (port 8099).
# Thin launcher around scripts/gpu-panel.py. Binds 127.0.0.1 only.
set -uo pipefail
PROJ="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot"
PY="/home/ma-user/modelarts/user-job-dir/yujc/anaconda3/envs/hiscopilot/bin/python"
PIDFILE="${PROJ}/data/logs/gpu-panel.pid"
LOGFILE="${PROJ}/data/logs/gpu-panel.log"
PORT="${GPU_PANEL_PORT:-8099}"
mkdir -p "${PROJ}/data/logs"

alive() { [ -f "$1" ] || return 1; local p; p="$(cat "$1")"; [ -d "/proc/$p" ]; }

cmd_start() {
  if alive "$PIDFILE"; then echo "gpu-panel already running (pid $(cat "$PIDFILE")) on :$PORT"; return; fi
  setsid nohup "$PY" "${PROJ}/scripts/gpu-panel.py" >>"$LOGFILE" 2>&1 < /dev/null &
  echo $! > "$PIDFILE"
  sleep 2
  if curl -sf -m 2 "http://127.0.0.1:${PORT}/api/state" >/dev/null 2>&1; then
    echo "gpu-panel up on http://127.0.0.1:${PORT} (pid $(cat "$PIDFILE"))"
  else
    echo "gpu-panel started (pid $(cat "$PIDFILE")) but not responding yet — see $LOGFILE"
  fi
}
cmd_stop() {
  if alive "$PIDFILE"; then
    local p; p="$(cat "$PIDFILE")"
    kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null || true
    for _ in $(seq 1 5); do [ -d "/proc/$p" ] || break; sleep 1; done
    [ -d "/proc/$p" ] && kill -9 -- "-$p" 2>/dev/null || true
    echo "gpu-panel stopped (pid $p)"
  else echo "gpu-panel not running"; fi
  rm -f "$PIDFILE"
}
cmd_status() {
  if alive "$PIDFILE"; then
    echo "gpu-panel: UP (pid $(cat "$PIDFILE")) :$PORT"
    curl -sf -m 2 "http://127.0.0.1:${PORT}/api/state" | python3 -m json.tool 2>/dev/null | head -5 || true
  else echo "gpu-panel: DOWN"; fi
}

case "${1:-status}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  restart) cmd_stop; cmd_start ;;
  *) echo "usage: $0 {start|stop|status|restart}"; exit 1 ;;
esac
