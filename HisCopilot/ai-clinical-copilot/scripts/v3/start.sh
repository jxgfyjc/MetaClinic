#!/usr/bin/env bash
# Start the v3 stack:  scripts/v3/start.sh [api|web|all]   (default all)
#   API : ONE uvicorn process (SQLite + in-process job workers => do NOT use --workers > 1)
#   WEB : `next start` (production build). Run `scripts/v3/build.sh` after changing apps/web.
. "$(dirname "$0")/common.sh"
what="${1:-all}"

start_api() {
  alive "$RUN_DIR/api.pid" "server.app:create_app" && { echo "api already running (pid $(cat "$RUN_DIR/api.pid"))"; return; }
  ( cd "$ROOT" && "$PYBIN" -m server.cli migrate ) || die "migration failed — see output above (a backup is taken before any change)"
  # NB: `cd` and the daemon are separate statements so `$!` is the daemon's own pid (setsid → nohup → python share it), not a wrapper subshell
  ( cd "$ROOT"; setsid nohup "$PYBIN" -m uvicorn server.app:create_app --factory --host "$API_HOST" --port "$API_PORT" \
      >> "$LOG_DIR/api.log" 2>&1 < /dev/null & echo $! > "$RUN_DIR/api.pid" )
  for _ in $(seq 1 30); do curl -fs -m 2 "http://$API_HOST:$API_PORT/healthz" > /dev/null && { echo "api up on $API_HOST:$API_PORT (pid $(cat "$RUN_DIR/api.pid"))"; return; }; sleep 1; done
  die "api did not become healthy; see $LOG_DIR/api.log"
}
start_web() {
  alive "$RUN_DIR/web.pid" "next" && { echo "web already running (pid $(cat "$RUN_DIR/web.pid"))"; return; }
  [ -f "$ROOT/apps/web/$NEXT_DIST_DIR/BUILD_ID" ] || die "no production build — run scripts/v3/build.sh first"
  built_for="$(cat "$ROOT/apps/web/$NEXT_DIST_DIR/BACKEND_URL" 2>/dev/null)"
  [ "$built_for" = "http://$API_HOST:$API_PORT" ] || die "the production build proxies /api to '${built_for:-unknown}' but the API is configured at http://$API_HOST:$API_PORT — run scripts/v3/build.sh again (the address is fixed at build time)"
  ( cd "$ROOT/apps/web"; export BACKEND_URL="http://$API_HOST:$API_PORT"; setsid nohup npx next start -p "$WEB_PORT" >> "$LOG_DIR/web.log" 2>&1 < /dev/null & echo $! > "$RUN_DIR/web.pid" )
  for _ in $(seq 1 30); do curl -fs -m 2 -o /dev/null "http://127.0.0.1:$WEB_PORT/login" && { echo "web up on :$WEB_PORT (pid $(cat "$RUN_DIR/web.pid"))"; return; }; sleep 1; done
  die "web did not become healthy; see $LOG_DIR/web.log"
}
case "$what" in api) start_api;; web) start_web;; all) start_api; start_web;; *) die "usage: start.sh [api|web|all]";; esac
