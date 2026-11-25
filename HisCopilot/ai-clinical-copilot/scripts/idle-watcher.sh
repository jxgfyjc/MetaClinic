#!/usr/bin/env bash
# idle-watcher.sh -- watch HisCopilot activity; shut down GPU1 model servers
# after a configurable idle period, so AIComic can reclaim GPU1 for dual-GPU
# batch video rendering.
#
# Design:
#   - "Idle" is decided by the API app's activity heartbeat, which it writes to
#     data/logs/last_activity on every business request (POST, or logged-in GET
#     to clinical-data paths -- browser heartbeat polling is excluded). See the
#     _ActivityMiddleware in services/api/app.py.
#   - When last_activity is older than IDLE_MINUTES (default 120), the watcher
#     calls scripts/model-servers.sh stop to release GPU1.
#   - After stopping, the watcher keeps looping: it does NOT auto-restart. A
#     human runs scripts/model-servers.sh start when the copilot is needed
#     again (the watcher will then see fresh activity and reset its idle timer).
#   - Auto-stop can be disabled without stopping the watcher:
#       idle-watcher.sh disable   # creates data/logs/idle-watcher.disabled
#       idle-watcher.sh enable    # removes it
#     While disabled, the watcher still loops and reports status, but never
#     stops the model servers. Use this before a dual-GPU AIComic session to
#     pin GPU1 to ComfyUI regardless of HisCopilot activity.
#
# Run as a daemon:  idle-watcher.sh start   (nohup + pidfile)
# Stop the daemon:  idle-watcher.sh stop
# Foreground probe: idle-watcher.sh probe    (one-shot check + maybe stop)
# Status:           idle-watcher.sh status
#
# Env overrides: IDLE_MINUTES (default 120), POLL_SECONDS (default 60),
#   MODEL_SCRIPT (default: sibling model-servers.sh).
set -uo pipefail

PROJ="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot"
LOGDIR="${PROJ}/data/logs"
ACT_FILE="${LOGDIR}/last_activity"
DISABLED_FILE="${LOGDIR}/idle-watcher.disabled"
PIDFILE="${LOGDIR}/idle-watcher.pid"
LOGFILE="${LOGDIR}/idle-watcher.log"
mkdir -p "$LOGDIR"

MODEL_SCRIPT="${MODEL_SCRIPT:-${PROJ}/scripts/model-servers.sh}"
IDLE_MINUTES="${IDLE_MINUTES:-120}"
POLL_SECONDS="${POLL_SECONDS:-60}"

# Is the model stack currently up? (any of the 6 servers alive)
stack_up() {
  pgrep -f 'services/model-gateway/servers/(llm_server|asr_server|ocr_server|embedding_server|reranker_server)\.py' >/dev/null 2>&1
}

auto_disabled() { [ -f "$DISABLED_FILE" ]; }

# epoch seconds of last activity; 0 if file missing/unreadable
last_activity_epoch() {
  [ -f "$ACT_FILE" ] || { echo 0; return; }
  local v; v="$(cat "$ACT_FILE" 2>/dev/null | tr -dc '0-9')"
  echo "${v:-0}"
}

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOGFILE"; echo "[$(date '+%H:%M:%S')] $*"; }

# One idle check. Prints status; returns 0 if it acted (stopped the stack).
do_probe() {
  if auto_disabled; then
    echo "auto-stop DISABLED ($DISABLED_FILE present) -- skipping"
    return 1
  fi
  if ! stack_up; then
    echo "model stack already down -- nothing to do"
    return 1
  fi
  local now last idle_s idle_m
  now=$(date +%s)
  last=$(last_activity_epoch)
  idle_s=$((now - last))
  idle_m=$((idle_s / 60))
  if [ "$last" -eq 0 ]; then
    echo "no activity record yet (will stop in ${IDLE_MINUTES}m once API writes one)"
    log "probe: no activity record; stack up; waiting"
    return 1
  fi
  echo "last activity ${idle_m}m ago (threshold ${IDLE_MINUTES}m)"
  if [ "$idle_s" -ge "$((IDLE_MINUTES * 60))" ]; then
    log "idle ${idle_m}m >= ${IDLE_MINUTES}m -> stopping model stack"
    bash "$MODEL_SCRIPT" stop >>"$LOGFILE" 2>&1
    log "model stack stopped; GPU1 released. Manual start to resume copilot."
    return 0
  fi
  return 1
}

cmd_loop() {
  log "idle-watcher starting (idle=${IDLE_MINUTES}m poll=${POLL_SECONDS}s model=$MODEL_SCRIPT)"
  while true; do
    do_probe >/dev/null 2>&1 || true
    sleep "$POLL_SECONDS"
  done
}

cmd_start() {
  if [ -f "$PIDFILE" ] && [ -d "/proc/$(cat "$PIDFILE" 2>/dev/null)" ]; then
    echo "idle-watcher already running (pid $(cat "$PIDFILE"))"
    return
  fi
  setsid nohup bash "$0" loop >>"$LOGFILE" 2>&1 < /dev/null &
  echo $! > "$PIDFILE"
  echo "idle-watcher started (pid $(cat "$PIDFILE")) log=$LOGFILE"
  echo "  idle threshold=${IDLE_MINUTES}m  poll=${POLL_SECONDS}s"
}

cmd_stop() {
  if [ -f "$PIDFILE" ]; then
    local p; p="$(cat "$PIDFILE")"
    if [ -n "$p" ] && [ -d "/proc/$p" ]; then
      kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null || true
      for _ in $(seq 1 5); do [ -d "/proc/$p" ] || break; sleep 1; done
      [ -d "/proc/$p" ] && kill -9 -- "-$p" 2>/dev/null || true
      echo "idle-watcher stopped (pid $p)"
    else
      echo "idle-watcher pid $p not alive (stale pidfile)"
    fi
    rm -f "$PIDFILE"
  else
    echo "no pidfile -- idle-watcher not running"
  fi
}

cmd_status() {
  echo "=== idle-watcher ==="
  if [ -f "$PIDFILE" ] && [ -d "/proc/$(cat "$PIDFILE" 2>/dev/null)" ]; then
    echo "daemon: UP (pid $(cat "$PIDFILE"))"
  else
    echo "daemon: DOWN"
  fi
  if auto_disabled; then echo "auto-stop: DISABLED"; else echo "auto-stop: ENABLED"; fi
  local now last idle_m
  now=$(date +%s); last=$(last_activity_epoch)
  if [ "$last" -eq 0 ]; then
    echo "last activity: (none recorded)"
  else
    idle_m=$(( (now - last) / 60 ))
    echo "last activity: ${idle_m}m ago  (threshold ${IDLE_MINUTES}m)"
  fi
  echo "--- model stack ---"
  bash "$MODEL_SCRIPT" status 2>/dev/null || true
}

case "${1:-status}" in
  loop)    cmd_loop ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  probe)   do_probe ;;
  enable)  rm -f "$DISABLED_FILE"; echo "auto-stop ENABLED" ;;
  disable) touch "$DISABLED_FILE"; echo "auto-stop DISABLED (idle-watcher still runs but won't stop models)" ;;
  status)  cmd_status ;;
  restart) cmd_stop; cmd_start ;;
  *) echo "usage: $0 {start|stop|probe|enable|disable|status|restart}"; exit 1 ;;
esac
