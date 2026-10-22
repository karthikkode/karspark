#!/bin/sh
# ks-bench <model> <label> [levels] [prefill-sizes]  (inside the container; base URL = the gateway)
set -u
MODEL="$1"; LABEL="$2"; LEVELS="${3:-1 2 4 6 8}"; PREFILL="${4:-16000 64000 128000}"
BASE="${KARSPARK_BASE:-http://127.0.0.1:8080}"
echo "== $LABEL ($MODEL) start $(date -u +%FT%TZ)"
for C in $LEVELS; do
  echo "== C$C"; python3 tools/bench_categories.py "$BASE" "$MODEL" "$LABEL" --thinking off --concurrency "$C" 2>&1 | tail -3
done
for N in $PREFILL; do
  echo "== prefill $N"; python3 tools/stress_prefill.py "$BASE/v1" "$MODEL" "$N" 2>&1 | tee "results/prefill_${LABEL}_${N}.txt" | tail -1
done
echo "== done $(date -u +%FT%TZ)"
