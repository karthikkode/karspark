#!/usr/bin/env bash
set -euo pipefail

source /home/karthik/vllm-env/bin/activate

exec vllm serve /home/karthik/models/qwen38-27b-abliterated \
  --served-model-name qwen38-27b-abliterated \
  --host 0.0.0.0 \
  --port 8002 \
  --max-model-len "${MAX_LEN:-262144}" \
  --gpu-memory-utilization "${GPU_UTIL:-0.80}" \
  --max-num-seqs "${MAX_SEQS:-1}" \
  --enable-prefix-caching \
  --max-num-batched-tokens 4096 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  --reasoning-parser qwen3 \
  --spec-method mtp \
  --spec-tokens "${SPEC_TOKENS:-3}" \
  "$@"
