#!/usr/bin/env bash
# Production build of the web app (run once, and after every change in apps/web OR a change of the API host/port).
# NOTE: Next.js bakes the /api/* rewrite destination into the build, so the API address given here is fixed until the next build.
. "$(dirname "$0")/common.sh"
export BACKEND_URL="http://$API_HOST:$API_PORT"
cd "$ROOT/apps/web" && { [ -d node_modules ] || npm ci; } && npx next build && echo "$BACKEND_URL" > "$NEXT_DIST_DIR/BACKEND_URL" && echo "built for backend $BACKEND_URL"
