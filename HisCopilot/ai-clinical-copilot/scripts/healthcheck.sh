"""Health check script — probe all model servers + API + frontend.
instruct #56/#91: health/readiness/metrics.
"""
#!/usr/bin/env bash
set -euo pipefail

declare -A SERVICES=(
  [clinical-main]=7870
  [tcm]=7871
  [asr]=7872
  [embedding]=7873
  [reranker]=7874
  [ocr]=7875
  [api]=8001
)
OK=0; FAIL=0
for name in "${!SERVICES[@]}"; do
  port=${SERVICES[$name]}
  if curl -sf -m 3 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
    echo "✓ $name (:$port)"; OK=$((OK+1))
  else
    echo "✗ $name (:$port)"; FAIL=$((FAIL+1))
  fi
done
echo "---"
echo "OK=$OK FAIL=$FAIL"
[ $FAIL -eq 0 ]
