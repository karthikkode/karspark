#!/usr/bin/env bash
# Serve Qwen3.5-35B-A3B-FP8 (thinking model) on DGX Spark GB10.
set -euo pipefail

source /home/karthik/vllm-env/bin/activate

export VLLM_ATTENTION_BACKEND="${VLLM_ATTENTION_BACKEND:-FLASHINFER}"

# Hybrid architecture: 40 layers, only 10 full-attention (30 are Gated DeltaNet
# linear attention, which hold per-sequence state rather than per-token KV).
#   KV = 2(K,V) x 10 layers x 2 kv_heads x 256 head_dim x 2 bytes = 20 KiB/token
#   262K context therefore costs only ~5.4 GB of KV cache.
# Sizing is left to gpu_memory_utilization here rather than pinned, because the
# hybrid state cache is not covered by --kv-cache-memory-bytes.

exec vllm serve Qwen/Qwen3.5-35B-A3B-FP8 \
    --served-model-name qwen35-35b \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len "${MAX_LEN:-262144}" \
    --gpu-memory-utilization "${GPU_UTIL:-0.60}" \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_xml \
    --reasoning-parser qwen3 \
    --max-num-seqs 8 \
    "$@"
