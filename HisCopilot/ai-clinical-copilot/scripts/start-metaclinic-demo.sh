#!/usr/bin/env bash
set -euo pipefail

REPO="/cephfs/yujc/HisCopilot/ai-clinical-copilot"
KEY_FILE="/cephfs/yujc/HisCopilot/api_key.txt"
PYBIN="/cephfs/yujc/anaconda3/envs/hiscopilot/bin/python"

API_KEY="$(awk '/^api_key[[:space:]]+/{print $2}' "$KEY_FILE")"
if [ -z "$API_KEY" ]; then
  echo "Failed to read api_key from $KEY_FILE" >&2
  exit 1
fi

unset ALL_PROXY HTTPS_PROXY HTTP_PROXY all_proxy https_proxy http_proxy

export QWEN_BASE_URL="https://api.deepseek.com"
export SHIZHEN_BASE_URL="https://api.deepseek.com"
export LLM_MAIN_MODEL="deepseek-flash"
export LLM_TCM_MODEL="deepseek-flash"
export XY_LLM_MAIN_LABEL="Qwen3.8-27B"
export XY_LLM_TCM_LABEL="ShizhenGPT-7B-Omni"
export XY_DEMO_LOCAL_PERSONA="true"
export XY_LLM_API_KEY="$API_KEY"
export XY_LLM_EXTRA_JSON='{"thinking":{"type":"disabled"}}'
export XY_STYLE_EXT_BASE_URL="https://api.deepseek.com"
export XY_STYLE_EXT_MODEL="deepseek-flash"
export XY_STYLE_EXT_API_KEY="$API_KEY"
export XY_STYLE_EXT_EXTRA_JSON='{"thinking":{"type":"disabled"}}'
export XY_CLINIC_NAME="MetaClinic"
export BACKEND_URL="http://127.0.0.1:8011"

mkdir -p "$REPO/data/logs"

if [ "${1:-}" = "build" ] || [ ! -f "$REPO/apps/web/.next/BUILD_ID" ]; then
  (cd "$REPO/apps/web" && npm run build)
fi

cd "$REPO"
"$PYBIN" -m server.cli migrate

"$PYBIN" -m uvicorn server.app:create_app --factory --host 0.0.0.0 --port 8011 \
  > "$REPO/data/logs/v3-api.log" 2>&1 &
API_PID=$!

cd "$REPO/apps/web"
npx next start -p 3100 > "$REPO/data/logs/v3-web.log" 2>&1 &
WEB_PID=$!

trap 'kill "$API_PID" "$WEB_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  if curl -fs -m 2 "http://127.0.0.1:8011/healthz" >/dev/null && \
     curl -fs -m 2 -o /dev/null "http://127.0.0.1:3100/login"; then
    echo "MetaClinic demo up: API=$API_PID (8011) WEB=$WEB_PID (3100)"
    break
  fi
  sleep 1
done

wait "$API_PID" "$WEB_PID"
