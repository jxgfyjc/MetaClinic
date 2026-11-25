#!/usr/bin/env bash
# Health check for the v3 stack and the model servers it depends on. Exit code 0 = everything the clinic needs is up.
. "$(dirname "$0")/common.sh"
rc=0; ok() { printf '  \033[32mOK\033[0m   %s\n' "$*"; }; bad() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; rc=1; }; warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
echo "== v3 services"
curl -fs -m 3 "http://$API_HOST:$API_PORT/healthz" > /dev/null && ok "api   http://$API_HOST:$API_PORT/healthz" || bad "api   not answering on $API_HOST:$API_PORT"
curl -fs -m 5 -o /dev/null "http://127.0.0.1:$WEB_PORT/login" && ok "web   http://127.0.0.1:$WEB_PORT/login" || bad "web   not answering on :$WEB_PORT"
direct="$(curl -fs -m 3 "http://$API_HOST:$API_PORT/healthz" | sed -n 's/.*"instance":"\([^"]*\)".*/\1/p')"
via="$(curl -fs -m 5 "http://127.0.0.1:$WEB_PORT/api/v1/ping" | sed -n 's/.*"instance":"\([^"]*\)".*/\1/p')"
[ -n "$direct" ] && [ "$direct" = "$via" ] && ok "web→api proxy reaches THIS api instance ($direct)" || bad "web→api proxy mismatch (api=$direct, via web=$via) — the web build points at another backend; run scripts/v3/build.sh"
echo "== database"
( cd "$ROOT" && "$PYBIN" - <<'PY'
import sqlite3, os, sys
d = os.environ["XY_DATA_DIR"]; p = os.path.join(d, "xiangyuan.db")
if not os.path.exists(p): print("  FAIL no database at", p); sys.exit(1)
c = sqlite3.connect(p)
v = c.execute("select max(version) from schema_migrations").fetchone()[0]
fk = c.execute("pragma foreign_key_check").fetchall(); ic = c.execute("pragma integrity_check").fetchone()[0]
jobs = dict(c.execute("select status, count(*) from jobs group by status").fetchall())
stuck = c.execute("select count(*) from jobs where status='running' and heartbeat_at < datetime('now','-10 minutes')").fetchone()[0]
print(f"  OK   schema version {v}; integrity={ic}; FK violations={len(fk)}; jobs={jobs}")
if ic != "ok" or fk: sys.exit(1)
if stuck: print(f"  WARN {stuck} running job(s) without a heartbeat for >10 min (the API recovers them on restart)")
PY
) || rc=1
echo "== audit chain"
( cd "$ROOT" && "$PYBIN" -m server.cli verify-audit ) > /tmp/xy_audit_check.$$ 2>&1 && ok "$(cat /tmp/xy_audit_check.$$)" || bad "audit chain: $(cat /tmp/xy_audit_check.$$)"; rm -f /tmp/xy_audit_check.$$
echo "== model servers (read-only probes; this script never starts/stops them)"
for spec in "7870:clinical_main (Qwen 27B, GPU1)" "7871:tcm (ShizhenGPT 7B, GPU1)" "7872:asr (Qwen3-ASR, CPU)" "7875:ocr (PaddleOCR-VL, CPU)"; do
  port="${spec%%:*}"; name="${spec#*:}"
  curl -fs -m 4 "http://127.0.0.1:$port/health" > /dev/null && ok "$port $name" || { [ "${XY_AI_MODE:-live}" = fake ] && warn "$port $name (fake AI mode)" || bad "$port $name — AI features degrade to visible 'failed + retry' states"; }
done
echo "== protected neighbours (must be left alone)"
for port in 8000 8188; do (exec 3<>/dev/tcp/127.0.0.1/$port) 2>/dev/null && ok "port $port is up (not ours — untouched)" || warn "port $port not listening (not managed by this stack)"; done
exit $rc
