#!/usr/bin/env bash
# Serve Qwen3.8-27B-NVFP4 (dense, qwen3_5 arch) on DGX Spark GB10.
# Tool calling: qwen3_xml parser. Reasoning: qwen3 parser (separates <think> blocks).
# MTP speculative decoding enabled (2026-08-19): uses the checkpoint's bundled
# model_mtp.safetensors head. Lossless; ~3-5 accepted tokens/step on agentic/code,
# ~2-3 on prose. Set SPEC_TOKENS=0 to disable (fallback to plain decode).
set -euo pipefail

source /home/karthik/vllm-env/bin/activate

exec vllm serve /home/karthik/models/qwen38-27b-nvfp4 \
    --served-model-name "qwen38-27b" \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len "${MAX_LEN:-262144}" \
    --gpu-memory-utilization "${GPU_UTIL:-0.80}" \
    --max-num-seqs "${MAX_SEQS:-4}" \
    --enable-prefix-caching \
    --max-num-batched-tokens 4096 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_xml \
    --reasoning-parser qwen3 \
    --spec-method "mtp" \
    --spec-tokens "${SPEC_TOKENS:-3}" \
    "$@"
