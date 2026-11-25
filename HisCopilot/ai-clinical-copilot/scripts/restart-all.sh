#!/usr/bin/env bash
# restart-all.sh — 一键重启 GPU 相关服务（出问题时用这个）。
#
# 处理三类常见故障：
#   1. 面板 8099 挂了/卡住           → 重启 gpu-panel
#   2. 本地 ComfyUI 崩了             → 重启 8188(GPU0) 和/或 8190(GPU1)
#      （OOM 是常见原因，峰值显存挤爆会 Fatal Aborted）
#   3. 模型组 / watcher 状态对不上   → 不动，只报告（门诊模式正在用时别乱停）
#
# 远程隧道 :8189 是只读的，本脚本绝不碰它（它是用户那边反向 SSH 过来的，
# 挂了得在远程机上重启，这里只能检测报告）。
#
# 用法：
#   restart-all.sh            # 重启面板 + 8188 + 8190（默认，最常用）
#   restart-all.sh panel      # 只重启面板
#   restart-all.sh comfy      # 重启 8188 + 8190
#   restart-all.sh 8188       # 只重启 GPU0 主 ComfyUI
#   restart-all.sh 8190       # 只重启 GPU1 本地第二卡
#   restart-all.sh all        # 重启面板 + 8188 + 8190
#   restart-all.sh status     # 全量状态速览
set -uo pipefail
PROJ="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot"
cd "$PROJ"
PANEL=scripts/gpu-panel.sh
COMFY=scripts/comfy-local.sh
MODEL=scripts/model-servers.sh
WATCHER=scripts/idle-watcher.sh
# GPU0 primary ComfyUI (:8188) lives in the AIComic tree — launched via serve.sh
# but we manage it directly here so we don't have to restart the gateway (which
# would drop the 8189/8190 backends from COMFYUI_URL).
AICOMIC="/home/ma-user/modelarts/user-job-dir/yujc/AIComic"
COMFYUI_DIR="/home/ma-user/modelarts/user-job-dir/yujc/ComfyUI"
COMFY_LOG_DIR="$AICOMIC/local-gateway/log"
CONDA_SH="/home/ma-user/modelarts/user-job-dir/yujc/anaconda3/etc/profile.d/conda.sh"

hr(){ echo; echo "━━━ $1 ━━━"; }

restart_panel(){
  hr "面板 :8099"
  bash "$PANEL" restart 2>&1 | tail -2
}

# Launch/restart the GPU0 primary ComfyUI on :8188. Does NOT touch the gateway
# (the gateway's health_loop re-marks 8188 ok=true within ~5s once it answers).
restart_8188(){
  hr "本地 ComfyUI :8188 (GPU0)"
  local pf="$COMFY_LOG_DIR/comfy-gpu0.pid"
  # kill any stale listener/process first (OOM-crashed process may linger)
  local old=""
  [ -f "$pf" ] && old=$(cat "$pf" 2>/dev/null || true)
  if [ -n "$old" ] && [ -d "/proc/$old" ]; then
    echo "  stopping old pid $old"
    kill "$old" 2>/dev/null || true
    for _ in $(seq 1 10); do [ -d "/proc/$old" ] || break; sleep 1; done
    [ -d "/proc/$old" ] && kill -9 "$old" 2>/dev/null || true
  fi
  # also catch any stray main.py --port 8188 not in pidfile
  local stray; stray=$(pgrep -f "main.py.*--port 8188" 2>/dev/null | head -1)
  [ -n "$stray" ] && { echo "  killing stray pid $stray"; kill -9 "$stray" 2>/dev/null || true; }
  rm -f "$pf"
  mkdir -p "$COMFY_LOG_DIR"
  source "$CONDA_SH" 2>/dev/null || true
  conda activate comfui 2>/dev/null || true
  echo "  launching ComfyUI on GPU0 :8188 (cuda-device 0)..."
  ( cd "$COMFYUI_DIR" && nohup python main.py \
      --listen 0.0.0.0 --port 8188 --cuda-device 0 --enable-manager \
      > "$COMFY_LOG_DIR/comfy-gpu0.log" 2>&1 & echo $! > "$pf" )
  local newpid; newpid=$(cat "$pf")
  echo "  pid=$newpid, waiting for /system_stats (up to 120s)..."
  local i
  for i in $(seq 1 120); do
    if curl -sf -m 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
      echo "  ✓ 8188 ready after ${i}s"
      return 0
    fi
    [ -d "/proc/$newpid" ] || { echo "  ✗ process exited — last log lines:"; tail -15 "$COMFY_LOG_DIR/comfy-gpu0.log"; return 1; }
    sleep 1
  done
  echo "  ✗ not ready after 120s — see $COMFY_LOG_DIR/comfy-gpu0.log"
  return 1
}

restart_comfy(){
  hr "本地 ComfyUI :8190 (GPU1)"
  # 清掉可能残留的僵尸 pidfile（进程已崩但文件还在），否则 restart 误判 already running
  local pf=data/logs/comfy-local.pid
  if [ -f "$pf" ]; then
    local p; p=$(cat "$pf" 2>/dev/null || true)
    if [ -z "$p" ] || [ ! -d "/proc/$p" ]; then
      echo "  (stale pidfile pid=${p:-none}, 进程已不在 → 清理)"
      rm -f "$pf"
    fi
  fi
  # 先 stop 兜底（杀残留进程），再 start
  bash "$COMFY" stop >/dev/null 2>&1 || true
  bash "$COMFY" start 2>&1 | tail -6
}

cmd_status(){
  hr "GPU 状态速览"
  bash "$PANEL" status 2>&1 | head -3
  bash "$COMFY" status 2>&1
  bash "$MODEL" status 2>&1 | head -10
  echo
  bash "$WATCHER" status 2>&1 | head -3
  echo
  echo "端口监听 8188-8195:"
  for p in 8188 8189 8190 8191 8192; do
    local l; l=$(lsof -iTCP:$p -sTCP:LISTEN -n -P 2>/dev/null | tail -1)
    if [ -n "$l" ]; then printf "  :%s %s\n" "$p" "$(echo "$l" | awk '{print $1, $2}')"; else printf "  :%s (none)\n" "$p"; fi
  done
  echo
  curl -sf -m 4 http://127.0.0.1:8099/api/state 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('面板 mode:', d['mode'], '| 模型组:', d['model_up_count'], '/6')
for c in d['comfy_ports']:
    print(f\"  :{c['port']} {c['state']}/{c['kind']}{' (本地第二卡)' if c.get('ours') else ''}\")
" 2>/dev/null || echo "(面板 /api/state 不可达)"
}

target="${1:-all}"
case "$target" in
  panel) restart_panel ;;
  comfy) restart_8188; restart_comfy ;;
  8188) restart_8188 ;;
  8190) restart_comfy ;;
  all)
    restart_panel
    restart_8188
    restart_comfy
    hr "状态"
    bash "$COMFY" status 2>&1
    ;;
  status) cmd_status ;;
  *) echo "usage: $0 {all|panel|comfy|8188|8190|status}"; echo "  all    = 重启面板 + 8188 + 8190（默认）"; echo "  panel  = 只重启面板"; echo "  comfy  = 重启 8188 + 8190"; echo "  8188   = 只重启 GPU0 主 ComfyUI"; echo "  8190   = 只重启 GPU1 本地第二卡"; echo "  status = 全量状态速览"; exit 1 ;;
esac
echo
echo "✓ 完成。详细状态：$0 status"
