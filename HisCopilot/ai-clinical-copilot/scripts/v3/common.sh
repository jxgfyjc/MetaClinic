#!/usr/bin/env bash
# Shared settings for the v3 service scripts. Override any of these in the environment (or in scripts/v3/local.env, git-ignored).
# NOTHING here touches ComfyUI (8188), aivideo (8000), the GPU allocation or the model servers (7870-7875).
set -u
V3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$V3_DIR/../.." && pwd)"                          # ai-clinical-copilot/
[ -f "$V3_DIR/local.env" ] && . "$V3_DIR/local.env"

PYBIN="${PYBIN:-/home/ma-user/modelarts/user-job-dir/yujc/anaconda3/envs/hiscopilot/bin/python}"
API_HOST="${API_HOST:-127.0.0.1}"; API_PORT="${API_PORT:-8011}"
WEB_PORT="${WEB_PORT:-3100}"
export XY_DATA_DIR="${XY_DATA_DIR:-$ROOT/data}"               # new DB: $XY_DATA_DIR/xiangyuan.db  (legacy ai_clinical.db is only ever READ by import-legacy)
export XY_COOKIE_SECURE="${XY_COOKIE_SECURE:-auto}"           # auto = secure cookies when served over https (X-Forwarded-Proto)
export XY_SEED_DEMO_USERS="${XY_SEED_DEMO_USERS:-false}"      # production: no demo accounts; create real ones with `create-user`
export APP_ENV="${APP_ENV:-production}"
export NEXT_DIST_DIR="${NEXT_DIST_DIR:-.next-prod}"          # production build output (separate from the dev server's .next)
RUN_DIR="${RUN_DIR:-$ROOT/run}"; LOG_DIR="${LOG_DIR:-$ROOT/logs}"
mkdir -p "$RUN_DIR" "$LOG_DIR"

die() { echo "ERROR: $*" >&2; exit 1; }
for _p in "$API_PORT" "$WEB_PORT"; do case "$_p" in 8000|8188) die "port $_p belongs to another service (aivideo / ComfyUI) — refusing";; esac; done

# is the pidfile's process alive AND really ours? (guards against pid reuse)
alive() { local f="$1" needle="$2"; [ -f "$f" ] || return 1; local p; p="$(cat "$f")"; [ -d "/proc/$p" ] && tr '\0' ' ' < "/proc/$p/cmdline" | grep -q -- "$needle"; }
