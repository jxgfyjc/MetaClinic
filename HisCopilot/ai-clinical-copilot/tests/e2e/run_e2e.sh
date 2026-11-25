#!/usr/bin/env bash
# Fresh-stack end-to-end run: wipes the e2e data dir, (re)starts the fake-AI API on :8012 and a dedicated Next dev server on :3101,
# then runs the browser scenarios in order. Never touches the main stack (8011/3100), ComfyUI (8188), aivideo (8000) or any GPU.
#   usage: tests/e2e/run_e2e.sh [scenario files...]   (default: all)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
PYBIN="${PYBIN:-/home/ma-user/modelarts/user-job-dir/yujc/anaconda3/envs/hiscopilot/bin/python}"
DATA=/tmp/xy_e2e; API_PORT=8012; WEB_PORT=3101

if [ "${KEEP:-0}" != 1 ]; then
# stop ONLY our own e2e API (exact command-line match; refuses anything on 8000/8188)
for p in /proc/[0-9]*; do
  pid="${p#/proc/}"; cmd="$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)"
  case "$cmd" in "$PYBIN -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port $API_PORT ") kill "$pid"; sleep 1;; esac
done
rm -rf "$DATA"; mkdir -p "$DATA" /tmp/v3shots
( cd "$ROOT" && XY_DATA_DIR=$DATA XY_AI_MODE=fake XY_SEED_DEMO_USERS=true XY_DEMO_PASSWORD=Demo12345 XY_COOKIE_SECURE=false \
  setsid nohup "$PYBIN" -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port $API_PORT > $DATA/api.log 2>&1 < /dev/null & )
curl -s -m 2 -o /dev/null "http://127.0.0.1:$WEB_PORT/login" || ( cd "$ROOT/apps/web" && NEXT_DIST_DIR=.next-e2e BACKEND_URL=http://127.0.0.1:$API_PORT \
  setsid nohup npx next dev -p $WEB_PORT > /tmp/xy_web_e2e.log 2>&1 < /dev/null & )
fi
for i in $(seq 1 40); do curl -s -m 2 "http://127.0.0.1:$API_PORT/healthz" >/dev/null && break; sleep 1; done
curl -s -m 120 -o /dev/null "http://127.0.0.1:$WEB_PORT/login"      # warm the dev compiler

SCEN=("$@"); [ ${#SCEN[@]} -eq 0 ] && SCEN=(scenario1_intake.js scenario2_doctor_loop.js scenario3_roles_failures.js scenario4_style.js)
rc=0
for s in "${SCEN[@]}"; do
  echo; echo "################ $s"; WEB=http://127.0.0.1:$WEB_PORT SHOTS=/tmp/v3shots/${s%.js} node "$HERE/$s" || rc=1
done
echo; echo "e2e exit code: $rc"; exit $rc
