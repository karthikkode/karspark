#!/usr/bin/env bash
# Planner bake-off for one text model through karspark (:8080), in the karspark/bench container on the Spark.
# The model must be served by the current mode (switch to it first). Results go to
# recipes/<model>/bench/<date>-<label>/.     scripts/bakeoff.sh <model> [label] [runs]   (ONLY=task1,task2 to run a subset)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${1:?model, e.g. qwen3.8-27b}"; LABEL="${2:-bakeoff}"; RUNS="${3:-2}"
MODEL_DIR="$ROOT/recipes/$MODEL/bench/$(date +%F)-$LABEL"
R="scratch/bakeoff-$MODEL-$LABEL"
ssh spark "rm -rf ~/$R && mkdir -p ~/$R && docker run --rm --network host --user \$(id -u):\$(id -g) \
  --env-file <(grep ^KARSPARK_API_KEY= ~/core/env) -v ~/$R:/bench/results --entrypoint python3 karspark/bench:4 \
  planner/bakeoff.py http://127.0.0.1:8080 '$MODEL' '$LABEL' --runs '$RUNS' ${ONLY:+--only '$ONLY'}" | tee /dev/stderr > /dev/null || true
mkdir -p "$MODEL_DIR"
scp -qr "spark:$R/$LABEL/*" "$MODEL_DIR/"
ssh spark "rm -rf ~/$R"
echo "results in ${MODEL_DIR#$ROOT/}"
