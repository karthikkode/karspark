#!/usr/bin/env bash
# Serve the text-stage LLM on DGX Spark GB10.
#
# DEFAULT: Qwen3.6-27B-NVFP4 (dense, ~21 GiB weights) — matches the pipeline's
# default LLM_MODEL (kathawin_tools/sound/bin/llm.py).
# FALLBACK: set QWEN35_122B_MODEL=/path (or SERVE_QWEN35_122B=1) to serve
# Qwen3.5-122B-A10B-GPTQ-Int4 as `qwen35-122b` instead; the client picks it up
# automatically via its LLM_FALLBACK_MODEL logic.
set -euo pipefail

source /home/karthik/vllm-env/bin/activate

if [ -n "${QWEN35_122B_MODEL:-}" ] || [ "${SERVE_QWEN35_122B:-0}" = "1" ]; then
    # The big one: ~74 GiB of weights on a 121.7 GiB unified-memory box, so it wants
    # the GPU mostly to itself. Shut down FLUX / IndexTTS-2 / the ComfyUI stack first.
    # Hybrid architecture: 48 layers, only 12 full-attention (36 are Gated DeltaNet).
    #   KV = 2(K,V) x 12 layers x 2 kv_heads x 256 head_dim x 2 bytes = 24 KiB/token
    #   262K context therefore costs only ~6.4 GB of KV cache.
    # 0.80 x 121.7 GiB = 97 GiB budget: 74 weights + KV + overhead, leaving ~24 GiB
    # for the desktop.
    MODEL_PATH="${QWEN35_122B_MODEL:-/home/karthik/models/qwen3.5-122b-a10b-gptq-int4}"
    [ -d "$MODEL_PATH" ] || MODEL_PATH="Qwen/Qwen3.5-122B-A10B-GPTQ-Int4"
    SERVED_NAME="qwen35-122b"
else
    MODEL_PATH="${QWEN36_27B_MODEL:-/home/karthik/models/qwen36-27b-nvfp4}"
    SERVED_NAME="qwen36-27b"
fi

exec vllm serve "$MODEL_PATH" \
    --served-model-name "$SERVED_NAME" \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len "${MAX_LEN:-262144}" \
    --gpu-memory-utilization "${GPU_UTIL:-0.80}" \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_xml \
    --reasoning-parser qwen3 \
    --max-num-seqs "${MAX_SEQS:-4}" \
    --mamba-cache-mode align \
    --enable-prefix-caching \
    --max-num-batched-tokens 4096 \
    "$@"
