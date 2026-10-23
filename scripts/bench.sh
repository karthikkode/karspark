#!/usr/bin/env bash
# Benchmark one recipe through karspark (:8080), in the karspark/bench container on the Spark, and copy the
# results into the repo.   scripts/bench.sh <recipe-id> <label> ["1 2 4 6 8"] ["16000 64000 128000"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID="${1:?recipe id, e.g. qwen3.8-flash:vllm-salient}"; LABEL="${2:?label}"
LEVELS="${3:-1 2 4 6 8}"; PREFILL="${4:-16000 64000 128000}"
MODEL_DIR="$ROOT/recipes/${ID%%:*}/bench/$(date +%F)-$LABEL"
R="scratch/bench-$LABEL"
ssh spark "E=~/core/env; [ -s \$E ] || E=~/gateway/env; rm -rf ~/$R && mkdir -p ~/$R && docker run --rm --network host --user \$(id -u):\$(id -g) \
  --env-file <(grep ^KARSPARK_API_KEY= \$E) -v ~/$R:/bench/results karspark/bench:3 '$ID' '$LABEL' '$LEVELS' '$PREFILL'" | tee /dev/stderr > /dev/null
mkdir -p "$MODEL_DIR"
scp -q "spark:$R/*" "$MODEL_DIR/"
ssh spark "rm -rf ~/$R"
echo "results in ${MODEL_DIR#$ROOT/}"
