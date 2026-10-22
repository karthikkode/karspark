#!/usr/bin/env bash
# Serve NVIDIA Nemotron 3.5 Lightning 30B-A3B (NVFP4, hybrid Nemotron-H) on DGX Spark GB10.
# ~21 GiB weights, modelopt MIXED_PRECISION quant (FP8 attention / NVFP4 experts + lm_head).
# Installed 2026-08-18 as a temporary swap for vllm-122b.service (speed evaluation);
# the qwen36-27b default is restored by removing the serve-nemotron.conf drop-in.
#
# Tool calling (2026-08-18): Nemotron's chat template emits Hermes-style tags
# with <parameter=name> lines -- no stock vLLM parser reads it. Custom plugin at
# /home/karthik/vllm-env/nemotron_tool_parser.py handles it; keep the two flags
# below together with that file.
set -euo pipefail

source /home/karthik/vllm-env/bin/activate

exec vllm serve /home/karthik/models/nemotron-3.5-lightning-nvfp4 \
    --served-model-name "nemotron-3.5-lightning" \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len "${MAX_LEN:-262144}" \
    --gpu-memory-utilization "${GPU_UTIL:-0.80}" \
    --max-num-seqs "${MAX_SEQS:-4}" \
    --enable-prefix-caching \
    --max-num-batched-tokens 8192 \
    --mamba-cache-mode align \
    --enable-auto-tool-choice \
    --tool-call-parser nemotron_35_lightning \
    --reasoning-parser nemotron_v3 \
    --tool-parser-plugin /home/karthik/vllm-env/nemotron_tool_parser.py \
    "$@"
