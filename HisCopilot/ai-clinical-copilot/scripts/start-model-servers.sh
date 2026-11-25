#!/usr/bin/env bash
# start-model-servers.sh — launch real model servers on GPU1 (host process mode).
# Each model in its own process / conda env as needed. OpenAI-compatible endpoints.
# Ports: 7870 clinical_main | 7871 tcm | 7872 asr | 7873 embedding | 7874 reranker | 7875 ocr
# instruct #5/#72: business layer only knows these URLs, never the runtime.
set -euo pipefail

CONDA_BASE="/home/ma-user/modelarts/user-job-dir/yujc/anaconda3"
source "${CONDA_BASE}/etc/profile.d/conda.sh"

PROJ="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot"
MODELS_ROOT="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/models"
LOGDIR="${PROJ}/data/logs"
mkdir -p "$LOGDIR"

export CUDA_VISIBLE_DEVICES=1   # GPU1 only — GPU0 reserved for ComfyUI (assumptions A5)
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

# ---- helpers ----------------------------------------------------------------
start_bg() {  # name cmd...
  local name="$1"; shift
  echo "[$(date +%H:%M:%S)] starting $name ..."
  nohup "$@" >"$LOGDIR/$name.log" 2>&1 &
  echo $! > "$LOGDIR/$name.pid"
}

# ---- 1. Embedding server (Qwen3-Embedding-8B, transformers, port 7873) -------
conda activate hiscopilot
start_bg embedding-server "${CONDA_BASE}/envs/hiscopilot/bin/python" \
  "${PROJ}/services/model-gateway/servers/embedding_server.py" \
  --port 7873 --model-path "${MODELS_ROOT}/Qwen3-Embedding-8B"

# ---- 2. Reranker server (Qwen3-Reranker-8B, port 7874) -----------------------
start_bg reranker-server "${CONDA_BASE}/envs/hiscopilot/bin/python" \
  "${PROJ}/services/model-gateway/servers/reranker_server.py" \
  --port 7874 --model-path "${MODELS_ROOT}/Qwen3-Reranker-8B"

# ---- 3. OCR server (PaddleOCR-VL gguf via llama-cpp, port 7875) --------------
# Uses comfui env (llama-cpp 0.4.2 + PaddleOCRChatHandler; hiscopilot env 0.3.36 lacks it)
start_bg ocr-server "${CONDA_BASE}/envs/comfui/bin/python" \
  "${PROJ}/services/model-gateway/servers/ocr_server.py" \
  --port 7875 --model-path "${MODELS_ROOT}/PaddleOCR-VL-1.6/PaddleOCR-VL-1.6-Q8_0.gguf"

# ---- 4. ASR server (Qwen3-ASR-1.7B gguf, port 7872) -------------------------
start_bg asr-server "${CONDA_BASE}/envs/hiscopilot/bin/python" \
  "${PROJ}/services/model-gateway/servers/asr_server.py" \
  --port 7872 --model-path "${MODELS_ROOT}/Qwen3-ASR-1.7B/Qwen3-ASR-1.7B-Q8_0.gguf"

# ---- 5. Clinical main (Qwen3.8-27B, transformers 5.18 via comfui env, port 7870)
#   Qwen3.8-27B needs transformers 5.18 (comfui env has it). Use comfui env.
conda activate comfui
start_bg clinical-main-server "${CONDA_BASE}/envs/comfui/bin/python" \
  "${PROJ}/services/model-gateway/servers/llm_server.py" \
  --port 7870 --model-path "${MODELS_ROOT}/Qwen3.8-27B" --role clinical_main

# ---- 6. TCM (ShizhenGPT-7B-Omni, transformers==4.51.0 via hiscopilot-tcm, port 7871)
conda activate hiscopilot-tcm
start_bg tcm-server "${CONDA_BASE}/envs/hiscopilot-tcm/bin/python" \
  "${PROJ}/services/model-gateway/servers/llm_server.py" \
  --port 7871 --model-path "${MODELS_ROOT}/ShizhenGPT-7B-Omni" --role tcm

echo "[$(date +%H:%M:%S)] all model servers launched. Logs in $LOGDIR"
echo "Wait for GPU memory to settle, then run: ${PROJ}/scripts/healthcheck.sh"
