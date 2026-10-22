#!/usr/bin/env bash
# Download model weights onto the Spark at a pinned revision, inside the karspark/hf-cli container,
# resuming over the flaky Wi-Fi (loop: engines/hf-cli/spark/fetch-loop.sh, deployed to ~/engines/hf-cli).
#
#   scripts/fetch-model.sh <hf-repo> <revision-sha> <kind>/<name> [include-glob ...]
#   e.g. scripts/fetch-model.sh Qwen/Qwen3-0.6B c1899de2... llm/qwen3-0.6b
#        scripts/fetch-model.sh Comfy-Org/flux2-dev ed33133c... image/flux2-dev-comfy 'split_files/vae/*'
#
# Gated repos need a Hugging Face read token in ~/engines/hf-cli/token on the Spark (the user creates it).
# Runs detached as container ks-fetch-<name>; progress: scripts/status.sh. HOST=<ssh-host> overrides spark.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${1:?hf repo}"; REV="${2:?revision sha (pin it)}"; DEST="${3:?<kind>/<name>}"; shift 3
HOST="${HOST:-spark}"
case "$DEST" in llm/*|image/*|video/*|audio/*|vision/*) ;; *) echo "kind must be llm|image|video|audio|vision" >&2; exit 1;; esac
[[ "$REV" =~ ^[0-9a-f]{40}$ ]] || { echo "revision must be a full 40-char commit sha" >&2; exit 1; }
NAME="ks-fetch-$(basename "$DEST")"
scp -q "$ROOT/engines/hf-cli/spark/fetch-loop.sh" "$HOST:engines/hf-cli/fetch-loop.sh" 2>/dev/null \
  || { ssh "$HOST" 'mkdir -p ~/engines/hf-cli'; scp -q "$ROOT/engines/hf-cli/spark/fetch-loop.sh" "$HOST:engines/hf-cli/fetch-loop.sh"; }
args=$(printf '%q ' "$NAME" "$REPO" "$REV" "$DEST" "$@")
ssh "$HOST" "mkdir -p ~/models/$DEST ~/engines/hf-cli/cache && chmod +x ~/engines/hf-cli/fetch-loop.sh && \
  (setsid nohup ~/engines/hf-cli/fetch-loop.sh $args > ~/models/$DEST.fetch.log 2>&1 < /dev/null &)"
echo "fetching $REPO@${REV:0:8} -> ~/models/$DEST (container $NAME, log ~/models/$DEST.fetch.log)"
