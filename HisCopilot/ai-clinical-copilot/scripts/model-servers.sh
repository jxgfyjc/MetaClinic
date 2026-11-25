#!/usr/bin/env bash
# model-servers.sh -- start / stop / status for the GPU1 model servers (7870-7875).
#
# Canonical control script for the HisCopilot GPU1 model stack.
# The idle-watcher daemon calls "stop" to free GPU1 for AIComic dual-GPU batch
# rendering; "start" is always manual so a human decides when the copilot is
# needed again.
#
# Safety contract (matches CLAUDE.md and instruct #5/#72):
#   - ONLY ever touches the 6 model-server processes below. NEVER kills by bare
#     port or bare "python"; each kill is scoped to a specific command needle
#     (llm_server.py --port 7870, asr_server.py --port 7872, ...), so the API
#     (:8001), frontend (:3000), ComfyUI (:8188) and aivideo (:8000) are never
#     affected.
#   - CUDA_VISIBLE_DEVICES pinned to 1 inside start (GPU0 reserved for ComfyUI).
#
# Ports: 7870 clinical_main | 7871 tcm | 7872 asr | 7873 embedding | 7874 reranker | 7875 ocr
set -uo pipefail

CONDA_BASE="/home/ma-user/modelarts/user-job-dir/yujc/anaconda3"
PROJ="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot"
MODELS_ROOT="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/models"
LOGDIR="${PROJ}/data/logs"
mkdir -p "$LOGDIR"

# Each entry: "name|needle"  -- needle is a unique substring of the process cmdline.
CLINICAL_MAIN_N="llm_server.py --port 7870"
TCM_N="llm_server.py --port 7871"
ASR_N="asr_server.py --port 7872"
EMB_N="embedding_server.py --port 7873"
RERANK_N="reranker_server.py --port 7874"
OCR_N="ocr_server.py --port 7875"

# pidfile's process is "ours" only if alive AND cmdline contains the needle.
alive() {
  local f="$1" needle="$2"
  [ -f "$f" ] || return 1
  local p; p="$(cat "$f" 2>/dev/null || true)"
  [ -n "$p" ] && [ -d "/proc/$p" ] && tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q -- "$needle"
}

find_by_needle() {
  pgrep -f -- "$1" 2>/dev/null | head -1
}

# launch one background server, recording its pid. args: name cmd...
start_bg() {
  local name="$1"; shift
  echo "[$(date +%H:%M:%S)] starting $name ..."
  nohup "$@" >"$LOGDIR/$name.log" 2>&1 &
  echo $! > "$LOGDIR/$name.pid"
}

# wait for a server's /health to respond (weights loaded). args: port timeout_s name
wait_healthy() {
  local port="$1" timeout="$2" name="$3"
  echo "[$(date +%H:%M:%S)] waiting for $name to load (port $port, up to ${timeout}s) ..."
  local i
  for i in $(seq 1 "$timeout"); do
    if curl -sf -m 2 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      echo "[$(date +%H:%M:%S)] $name ready after ${i}s"
      return 0
    fi
    # if the process died, don't keep waiting
    pgrep -f -- "port ${port}" >/dev/null 2>&1 || { echo "[$(date +%H:%M:%S)] $name process exited during load"; return 1; }
    sleep 1
  done
  echo "[$(date +%H:%M:%S)] $name not ready after ${timeout}s (continuing anyway)"
  return 1
}

start_one() {  # name needle cmd...
  local name="$1" needle="$2"; shift 2
  if ! alive "$LOGDIR/$name.pid" "$needle"; then
    start_bg "$name" "$@"
  else
    echo "$name already running"
  fi
}

stop_one() {  # name needle
  local name="$1" needle="$2"
  local pidfile="$LOGDIR/$name.pid"
  local pid=""
  if alive "$pidfile" "$needle"; then
    pid="$(cat "$pidfile")"
  else
    pid="$(find_by_needle "$needle")"
  fi
  if [ -n "$pid" ] && [ -d "/proc/$pid" ]; then
    echo "  stopping $name pid=$pid"
    kill "$pid" 2>/dev/null || true
  else
    echo "  $name not running"
  fi
  rm -f "$pidfile"
}

# ---------------------------------------------------------------------------
# Load order matters: the big models (clinical_main ~54G, tcm ~14G) MUST load
# first while GPU1 is empty, otherwise the smaller 8B models (embedding/reranker)
# fill the card and the 27B OOMs. embedding+reranker load last and will fail
# gracefully if GPU1 is full -- this matches the original working state where
# only clinical_main/tcm/asr/ocr fit on one 80G card.
cmd_start() {
  source "${CONDA_BASE}/etc/profile.d/conda.sh"
  cd "$PROJ"
  export CUDA_VISIBLE_DEVICES=1
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false

  # 1. Clinical main (Qwen3.8-27B ~54G, comfui env, port 7870) -- load first
  conda activate comfui
  start_one clinical-main-server "$CLINICAL_MAIN_N" \
    "${CONDA_BASE}/envs/comfui/bin/python" \
    "${PROJ}/services/model-gateway/servers/llm_server.py" \
    --port 7870 --model-path "${MODELS_ROOT}/Qwen3.8-27B" --role clinical_main
  # 27B weights take ~60s to load; wait before launching the next big model so
  # they don't both grab memory and OOM.
  wait_healthy 7870 90 clinical-main || echo "  WARN: clinical-main failed to come up"

  # 2. TCM (ShizhenGPT-7B ~14G, hiscopilot-tcm env, port 7871)
  conda activate hiscopilot-tcm
  start_one tcm-server "$TCM_N" \
    "${CONDA_BASE}/envs/hiscopilot-tcm/bin/python" \
    "${PROJ}/services/model-gateway/servers/llm_server.py" \
    --port 7871 --model-path "${MODELS_ROOT}/ShizhenGPT-7B-Omni" --role tcm
  wait_healthy 7871 90 tcm || echo "  WARN: tcm failed to come up"

  # 3. ASR (Qwen3-ASR-1.7B gguf, small, port 7872) -- env note: needs comfui env
  # for Qwen3ASRChatHandler (hiscopilot env's llama_cpp is too old). See [[asr-empty-output]].
  conda activate comfui
  start_one asr-server "$ASR_N" \
    "${CONDA_BASE}/envs/comfui/bin/python" \
    "${PROJ}/services/model-gateway/servers/asr_server.py" \
    --port 7872 --model-path "${MODELS_ROOT}/Qwen3-ASR-1.7B/Qwen3-ASR-1.7B-Q8_0.gguf"

  # 4. OCR (PaddleOCR-VL gguf, small, port 7875)
  start_one ocr-server "$OCR_N" \
    "${CONDA_BASE}/envs/comfui/bin/python" \
    "${PROJ}/services/model-gateway/servers/ocr_server.py" \
    --port 7875 --model-path "${MODELS_ROOT}/PaddleOCR-VL-1.6/PaddleOCR-VL-1.6-Q8_0.gguf"

  # 5/6. Embedding + Reranker (8B each ~17G). These two + the 27B + 7B above
  # total ~102G -- too big for one 80G card. They are launched last and will
  # OOM-fail if GPU1 is full; that's the same state as the original deployment
  # (knowledge RAG degrades to no-embedding). If you need them, stop
  # clinical-main first.
  conda activate hiscopilot
  start_one embedding-server "$EMB_N" \
    "${CONDA_BASE}/envs/hiscopilot/bin/python" \
    "${PROJ}/services/model-gateway/servers/embedding_server.py" \
    --port 7873 --model-path "${MODELS_ROOT}/Qwen3-Embedding-8B"
  start_one reranker-server "$RERANK_N" \
    "${CONDA_BASE}/envs/hiscopilot/bin/python" \
    "${PROJ}/services/model-gateway/servers/reranker_server.py" \
    --port 7874 --model-path "${MODELS_ROOT}/Qwen3-Reranker-8B"

  # A manual start IS activity: reset the idle timer so the watcher doesn't
  # immediately re-stop a freshly launched stack (it reads last_activity, which
  # may be hours old while weights are still loading). This gives a 120m window
  # for a doctor to actually start using the copilot.
  mkdir -p "$LOGDIR"
  date +%s > "$LOGDIR/last_activity"

  echo "[$(date +%H:%M:%S)] launch done. Logs in $LOGDIR"
  echo "Wait ~60-90s for weights to load, then: $0 status"
}

# ---------------------------------------------------------------------------
cmd_stop() {
  echo "[$(date +%H:%M:%S)] stopping GPU1 model servers ..."
  stop_one clinical-main-server "$CLINICAL_MAIN_N"
  stop_one tcm-server "$TCM_N"
  stop_one asr-server "$ASR_N"
  stop_one embedding-server "$EMB_N"
  stop_one reranker-server "$RERANK_N"
  stop_one ocr-server "$OCR_N"

  # Wait for processes to actually exit so GPU memory frees before we return.
  local needles=("$CLINICAL_MAIN_N" "$TCM_N" "$ASR_N" "$EMB_N" "$RERANK_N" "$OCR_N")
  echo "  waiting for processes to exit ..."
  local i n
  for i in $(seq 1 30); do
    local still=0
    for n in "${needles[@]}"; do pgrep -f -- "$n" >/dev/null 2>&1 && still=$((still+1)); done
    [ "$still" -eq 0 ] && break
    sleep 1
  done
  # SIGKILL any stragglers (rare; transformers can hang on CUDA teardown)
  for n in "${needles[@]}"; do
    local p; p="$(pgrep -f -- "$n" 2>/dev/null | head -1)"
    [ -n "$p" ] && { echo "  $n still alive pid=$pid -> SIGKILL"; kill -9 "$p" 2>/dev/null || true; }
  done
  sleep 2
  echo "[$(date +%H:%M:%S)] stop done."
}

# ---------------------------------------------------------------------------
cmd_status() {
  printf "%-22s %-7s %-10s %s\n" "NAME" "PORT" "STATE" "PID"
  show_one "clinical-main-server" "$CLINICAL_MAIN_N" 7870
  show_one "tcm-server" "$TCM_N" 7871
  show_one "asr-server" "$ASR_N" 7872
  show_one "embedding-server" "$EMB_N" 7873
  show_one "reranker-server" "$RERANK_N" 7874
  show_one "ocr-server" "$OCR_N" 7875
  echo "---"
  echo "GPU1: $(nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader -i 1 2>/dev/null || echo n/a)"
}

show_one() {  # name needle port
  local name="$1" needle="$2" port="$3"
  local pidfile="$LOGDIR/$name.pid"
  local pid="" state="DOWN"
  if alive "$pidfile" "$needle"; then
    pid="$(cat "$pidfile")"; state="UP"
  else
    pid="$(find_by_needle "$needle")"
    [ -n "$pid" ] && state="UP(no-pf)"
  fi
  printf "%-22s %-7s %-10s %s\n" "$name" "$port" "$state" "${pid:--}"
}

case "${1:-status}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  restart) cmd_stop; cmd_start ;;
  *) echo "usage: $0 {start|stop|status|restart}"; exit 1 ;;
esac
