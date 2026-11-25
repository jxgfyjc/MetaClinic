#!/usr/bin/env bash
# comfy-local.sh -- start / stop / status for the SECOND LOCAL ComfyUI instance
# on GPU1 (port 8190), used by AIComic's dual-GPU batch video rendering.
#
# WHY PORT 8190 (not 8189)
#   :8189 is reserved as the landing port of a REMOTE reverse-SSH tunnel
#   (remote box's ComfyUI :8188 forwarded here). The user provides that extra
#   GPU as a read-only backend. The LOCAL second ComfyUI we launch on GPU1 when
#   HisCopilot is idle MUST use a different port (:8190) so the two never
#   collide for the :8189 socket. The gateway fans out across whichever
#   backends it's configured with (8188, 8189-remote, 8190-local, ...).
#
# WHY THIS EXISTS
#   GPU0 runs ComfyUI :8188 for AIComic. GPU1 normally runs HisCopilot's model
#   servers (7870-7875). When the copilot is idle, GPU1 can instead run a SECOND
#   ComfyUI on :8190 so AIComic's gateway fans the shot queue across 2 GPUs.
#   Two full H3 worksets (~81G each) + HisCopilot (~77G) cannot coexist on two
#   80G cards, so starting 8190 requires HisCopilot's model group to be stopped
#   first -- this script does NOT do that automatically; the panel orchestrates
#   the mutual exclusion. Use `comfy-local.sh prereq` to check GPU1 is free.
#
# SAFETY
#   - Pinned to GPU1 via CUDA_VISIBLE_DEVICES=1. NEVER touches GPU0 / :8188.
#   - Reuses the SAME ComfyUI checkout + models dir as :8188 (284G on disk),
#     only the visible device differs. No second model download.
#   - Uses the comfui conda env (same as :8188).
set -uo pipefail

CONDA_BASE="/home/ma-user/modelarts/user-job-dir/yujc/anaconda3"
COMFY_DIR="/home/ma-user/modelarts/user-job-dir/yujc/ComfyUI"
PY="${CONDA_BASE}/envs/comfui/bin/python"
PORT="${COMFY_LOCAL_PORT:-8190}"
PIDFILE="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot/data/logs/comfy-local.pid"
LOGFILE="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot/data/logs/comfy-local.log"
NEEDLE="main.py --listen 0.0.0.0 --port ${PORT}"
mkdir -p "$(dirname "$PIDFILE")"

alive() {
  [ -f "$PIDFILE" ] || return 1
  local p; p="$(cat "$PIDFILE" 2>/dev/null || true)"
  [ -n "$p" ] && [ -d "/proc/$p" ] && tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q -- "$NEEDLE"
}

find_proc() { pgrep -f -- "$NEEDLE" 2>/dev/null | head -1; }

# Is GPU1 actually free enough for an H3 workset (~75G)?
gpu1_free_mb() {
  nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits -i 1 2>/dev/null | tr -d '[:space:]'
}

cmd_prereq() {
  local free; free=$(gpu1_free_mb)
  echo "GPU1 free: ${free:-?} MiB (need >= 70000 for H3 workset)"
  if [ -n "$free" ] && [ "$free" -ge 70000 ] 2>/dev/null; then
    echo "OK: GPU1 has room"
    return 0
  else
    echo "BLOCKED: GPU1 not free -- stop HisCopilot model group first (scripts/model-servers.sh stop)"
    return 1
  fi
}

cmd_start() {
  if alive; then echo "comfy-local already running (pid $(cat "$PIDFILE"))"; return 0; fi
  # hard guard: don't even try if GPU1 is occupied
  local free; free=$(gpu1_free_mb)
  if [ -n "$free" ] && [ "$free" -lt 70000 ] 2>/dev/null; then
    echo "BLOCKED: GPU1 only has ${free} MiB free (need >=70000). Stop HisCopilot model group first."
    echo "  -> scripts/model-servers.sh stop"
    return 2
  fi
  [ -d "$COMFY_DIR" ] || { echo "BLOCKED: ComfyUI dir not found: $COMFY_DIR"; return 3; }
  echo "[$(date +%H:%M:%S)] starting ComfyUI on GPU1 :${PORT} ..."
  (
    cd "$COMFY_DIR"
    export CUDA_VISIBLE_DEVICES=1
    export PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync
    setsid nohup "$PY" main.py --listen 0.0.0.0 --port "$PORT" --enable-manager \
      > "$LOGFILE" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
  )
  sleep 2
  echo "[$(date +%H:%M:%S)] launched (pid $(cat "$PIDFILE")). Waiting for /system_stats ..."
  for i in $(seq 1 120); do
    if curl -sf -m 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
      echo "[$(date +%H:%M:%S)] comfy-local ready after ${i}s"
      return 0
    fi
    pgrep -f -- "$NEEDLE" >/dev/null 2>&1 || { echo "[$(date +%H:%M:%S)] process exited during startup -- see $LOGFILE"; return 4; }
    sleep 1
  done
  echo "[$(date +%H:%M:%S)] not ready after 120s -- see $LOGFILE"
  return 5
}

cmd_stop() {
  local pid=""
  if alive; then pid="$(cat "$PIDFILE")"; else pid="$(find_proc)"; fi
  if [ -n "$pid" ] && [ -d "/proc/$pid" ]; then
    echo "[$(date +%H:%M:%S)] stopping comfy-local (pid $pid)"
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do [ -d "/proc/$pid" ] || break; sleep 1; done
    [ -d "/proc/$pid" ] && { echo "  SIGKILL"; kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true; }
    sleep 2
    echo "[$(date +%H:%M:%S)] stopped"
  else
    echo "comfy-local not running"
  fi
  rm -f "$PIDFILE"
}

cmd_status() {
  local pid="" state="DOWN"
  if alive; then pid="$(cat "$PIDFILE")"; state="UP"; else pid="$(find_proc)"; [ -n "$pid" ] && state="UP(no-pf)"; fi
  local health="down"
  curl -sf -m 2 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1 && health="up"
  local free; free=$(gpu1_free_mb)
  printf "%-16s %-8s %-10s %-6s %s\n" "comfy-local" ":${PORT}" "$state" "pid=${pid:--}" "health=$health GPU1free=${free:-?}M"
}

case "${1:-status}" in
  start)    cmd_start ;;
  stop)     cmd_stop ;;
  status)   cmd_status ;;
  prereq)   cmd_prereq ;;
  restart)  cmd_stop; cmd_start ;;
  *) echo "usage: $0 {start|stop|status|prereq|restart}"; exit 1 ;;
esac
