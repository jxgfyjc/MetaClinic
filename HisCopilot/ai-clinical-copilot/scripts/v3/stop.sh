#!/usr/bin/env bash
# Stop ONLY the processes this stack started (by pidfile + command-line check). Never pkill, never ports 8000/8188.
. "$(dirname "$0")/common.sh"
stop_one() {  # name needle
  local f="$RUN_DIR/$1.pid"
  if alive "$f" "$2"; then local p; p="$(cat "$f")"
    # the pid is a session leader (started with setsid), so -PID signals its whole process group: npx → next → next-server all stop together
    kill -- "-$p" 2>/dev/null || kill "$p"; for _ in $(seq 1 15); do [ -d "/proc/$p" ] || break; sleep 1; done
    [ -d "/proc/$p" ] && { echo "$1 still alive after 15 s — sending SIGKILL to group $p"; kill -9 -- "-$p" 2>/dev/null || kill -9 "$p"; }; echo "$1 stopped"
  else echo "$1 not running (or pidfile stale)"; fi
  rm -f "$f"
}
case "${1:-all}" in api) stop_one api "server.app:create_app";; web) stop_one web "next";; all) stop_one web "next"; stop_one api "server.app:create_app";; *) die "usage: stop.sh [api|web|all]";; esac
