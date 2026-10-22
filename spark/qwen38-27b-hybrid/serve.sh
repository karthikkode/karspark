#!/usr/bin/env bash
set -euo pipefail
: "${VLLM_API_KEY:?hybrid Qwen API key is missing}"
exec /bin/bash /home/karthik/vllm-env/serve-qwen38-27b.sh --api-key "$VLLM_API_KEY"
