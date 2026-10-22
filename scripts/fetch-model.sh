#!/usr/bin/env bash
# Download model weights onto the Spark at a pinned revision, resuming over the flaky Wi-Fi.
#
#   scripts/fetch-model.sh <hf-repo> <revision-sha> <kind>/<name> [ssh-host]
#   e.g. scripts/fetch-model.sh Qwen/Qwen3-0.6B c1899de2... llm/qwen3-0.6b
#
# Runs detached on the Spark; follow it with:  ssh spark 'tail -f ~/models/<kind>/<name>.fetch.log'
set -euo pipefail
REPO="${1:?hf repo}"; REV="${2:?revision sha (pin it)}"; DEST="${3:?<kind>/<name>}"; HOST="${4:-spark}"
case "$DEST" in llm/*|image/*|audio/*|vision/*) ;; *) echo "kind must be llm|image|audio|vision" >&2; exit 1;; esac
[[ "$REV" =~ ^[0-9a-f]{40}$ ]] || { echo "revision must be a full 40-char commit sha" >&2; exit 1; }

ssh "$HOST" "mkdir -p ~/models/$(dirname "$DEST") && cd ~/models && setsid nohup bash -c '
  for i in \$(seq 1 50); do
    HF_HUB_DISABLE_XET=1 timeout 3600 ~/engines/hf-cli/bin/hf download $REPO --revision $REV --local-dir ~/models/$DEST && { echo FETCH-OK; exit 0; }
    echo \"retry \$i\"; sleep 20
  done; echo FETCH-FAILED; exit 1' > ~/models/$DEST.fetch.log 2>&1 < /dev/null &"
echo "fetching $REPO@${REV:0:8} -> ~/models/$DEST (log: ~/models/$DEST.fetch.log)"
