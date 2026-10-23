#!/usr/bin/env bash
# Check the ComfyUI adapter's graphs (working tree) with ComfyUI's own validator, on the Spark, CPU only.
# Loads no weights, so it is safe while another model owns the GPU.
#   scripts/validate-comfy-graphs.sh [ssh-host]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${1:-spark}"
IMAGE="$(sed -n 's/^.*\(karspark\/comfyui:[^ ]*\).*$/\1/p' "$ROOT/recipes/comfyui/comfy.yaml" | head -1)"
E="$ROOT/engines/comfyui"
ssh "$HOST" 'rm -rf ~/scratch/.comfy-check && mkdir -p ~/scratch/.comfy-check'
tar -C "$E" -cf - adapter tools karspark_nodes extra_model_paths.yaml | ssh "$HOST" 'tar -C ~/scratch/.comfy-check -xf -'
ssh "$HOST" "d=~/scratch/.comfy-check; docker run --rm --memory 4g -v ~/models:/models:ro \
  -v \$d/adapter:/opt/adapter:ro -v \$d/tools:/opt/tools:ro -v \$d/karspark_nodes:/opt/karspark_nodes:ro -v \$d/extra_model_paths.yaml:/opt/comfyui/extra_model_paths.yaml:ro \
  --entrypoint python3 $IMAGE /opt/tools/validate_graphs.py 2>&1 | grep -E '^(OK|FAIL| )' ; rc=\${PIPESTATUS[0]}; rm -rf \$d; exit \$rc"
