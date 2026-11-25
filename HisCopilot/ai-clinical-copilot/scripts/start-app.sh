#!/usr/bin/env bash
# start-app.sh — start backend API + frontend (assumes model servers already up).
# instruct: do NOT touch ports 8000 (aivideo) / 8188 (ComfyUI).
set -euo pipefail
CONDA_BASE="/home/ma-user/modelarts/user-job-dir/yujc/anaconda3"
source "${CONDA_BASE}/etc/profile.d/conda.sh"
PROJ="/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/ai-clinical-copilot"

# --- backend (FastAPI :8001) ---
conda activate hiscopilot
cd "$PROJ"
export PYTHONPATH=.
pkill -f "uvicorn services.api.app" 2>/dev/null || true
sleep 1
nohup python -m uvicorn services.api.app:app --host 0.0.0.0 --port 8001 --app-dir . \
  > data/logs/api.log 2>&1 &
echo $! > data/logs/api.pid
echo "[backend] pid $(cat data/logs/api.pid) on :8001"

# --- frontend (Next.js :3000) ---
cd "$PROJ/apps/doctor-web"
pkill -f "next dev" 2>/dev/null || true
sleep 1
nohup npx next dev -p 3000 > "$PROJ/data/logs/frontend.log" 2>&1 &
echo $! > "$PROJ/data/logs/frontend.pid"
echo "[frontend] pid $(cat "$PROJ/data/logs/frontend.pid") on :3000"

echo "wait for health..."
sleep 15
curl -s -m 3 http://127.0.0.1:8001/health && echo " [api ok]"
curl -s -m 3 http://127.0.0.1:3000/ -o /dev/null -w "frontend HTTP %{http_code}\n" || true
