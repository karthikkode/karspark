#!/usr/bin/env bash
# Launch script for qwen38-flash.service, rendered by install.sh (a plain file
# so no systemd-vs-bash double escaping can ever corrupt the JSON arguments;
# regenerated on every install; the serve revision is refreshed by switch-model.sh).
# Serving flags: see flash/ATTRIBUTION.md for why each GB10-specific one exists.
set -euo pipefail
exec /usr/bin/docker run --rm --name qwen38-flash --gpus all \
  --memory 110g --memory-swap 110g --shm-size 16g --ipc=host \
  -p 30000:8000 \
  -v /home/karthik/models:/hf -e HF_HOME=/hf -e HF_HUB_OFFLINE=1 \
  -v /home/karthik/.config/qwen38:/out \
  -e VLLM_PLE_MMAP=1 -e VLLM_PLE_GPU_GATHER=1 -e VLLM_PLE_DECODE_WARM=1 -e VLLM_PLE_MMAP_WORKERS=32 -e VLLM_PLE_MMAP_PREWARM=0 -e VLLM_QSA_EXACT_TOPK=1 -e VLLM_FP8_HYBRID=1 -e VLLM_USE_DEEP_GEMM=0 \
  -e VLLM_USE_FLASHINFER_SAMPLER=1 \
  ghcr.io/lancelind/qwen38-flash-dgx@sha256:62a77b7c2806385cd23aeb7f6c4979b29f1b0db941bdc0f4791da90aa15f5bb9 \
  /hf/qwen38-flash-next-nvfp4-fp8hybrid  --served-model-name qwen3.8-flash-next \
    --host 0.0.0.0 --port 8000 --load-format safetensors \
    --max-model-len 262144 --max-num-seqs 8 --gpu-memory-utilization 0.75 \
    --enable-prefix-caching --mamba-cache-mode align --enable-chunked-prefill --max-num-batched-tokens 8192 --kv-cache-dtype auto \
    -cc.cudagraph_mode=PIECEWISE \
    '-cc.splitting_ops=["vllm::unified_attention_with_output","vllm::unified_mla_attention_with_output","vllm::mamba_mixer2","vllm::mamba_mixer","vllm::short_conv","vllm::qwen3_8_flash_next_ple_short_conv","vllm::qwen3_8_flash_next_qsa_with_output","vllm::linear_attention","vllm::qwen_gdn_attention_core","vllm::qwen_gdn_attention_core_fused_norm_packed","vllm::sparse_attn_indexer","vllm::ple_mmap_lookup"]' \
    --no-enable-flashinfer-autotune \

    --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
    --chat-template /out/chat-template-sharp.jinja \
    --api-key "$(cat /home/karthik/.config/qwen38/api-key)"
