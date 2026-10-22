#!/usr/bin/env bash
# Launch ComfyUI for FLUX.2 [dev] NVFP4 on DGX Spark (GB10).
#
#   ~/flux2/start_comfyui.sh              -> listen on localhost:8188
#   ~/flux2/start_comfyui.sh --lan        -> listen on all interfaces (reach it from another machine)
set -eu

VENV=/home/karthik/flux2/.venv
COMFY=/home/karthik/ComfyUI

LISTEN=127.0.0.1
[ "${1:-}" = "--lan" ] && LISTEN=0.0.0.0

# GB10 has 128 GB of *unified* (CPU+GPU shared) memory: "VRAM" and system RAM are the
# same physical pool.
#
# IMPORTANT -- restart after switching models. ComfyUI caches every model it loads and
# does not evict them. Swapping between checkpoints (pure/mixed NVFP4) or between Turbo
# and --base within one session accumulates 20+ GB copies until the machine swaps:
# generation degrades from ~52 s to ~170 s, and eventually the driver OOMs. Steady state
# on a freshly started server is 52 s at 1080p; if you see much worse, restart this.
#
# Do NOT add --gpu-only either: it keeps every cached model resident with no eviction,
# which is how the second hang happened.
#
# Do NOT use --highvram here. On a discrete GPU it just keeps weights resident in VRAM,
# but on GB10 it made ComfyUI pin 112 GB of 121 GB as page-locked (non-reclaimable)
# memory. With ~32 GB of models, LoRA patching (which clones weights) and a large
# download in page cache on top, the driver ran out of allocatable memory and hung the
# machine (NVRM NV_ERR_NO_MEMORY). Models stay resident here anyway -- there is no
# separate VRAM to promote them into.
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

cd "$COMFY"
exec "$VENV/bin/python" main.py \
  --listen "$LISTEN" \
  --port 8188 \
  --reserve-vram 16 \
  --disable-pinned-memory \
  "${@:2}"
