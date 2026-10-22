#!/usr/bin/env bash
# Download model weights onto the Spark at a pinned revision, inside the karspark/hf-cli container,
# resuming over the flaky Wi-Fi.
#
#   scripts/fetch-model.sh <hf-repo> <revision-sha> <kind>/<name> [ssh-host]
#   e.g. scripts/fetch-model.sh Qwen/Qwen3-0.6B c1899de2... llm/qwen3-0.6b
#
# Runs detached on the Spark as container ks-fetch-<name>; progress: scripts/status.sh
set -euo pipefail
REPO="${1:?hf repo}"; REV="${2:?revision sha (pin it)}"; DEST="${3:?<kind>/<name>}"; HOST="${4:-spark}"
case "$DEST" in llm/*|image/*|audio/*|vision/*) ;; *) echo "kind must be llm|image|audio|vision" >&2; exit 1;; esac
[[ "$REV" =~ ^[0-9a-f]{40}$ ]] || { echo "revision must be a full 40-char commit sha" >&2; exit 1; }
NAME="ks-fetch-$(basename "$DEST")"

ssh "$HOST" "mkdir -p ~/models/$DEST ~/engines/hf-cli/cache && cd ~/models && setsid nohup bash -c '
  for i in \$(seq 1 50); do
    docker rm -f $NAME >/dev/null 2>&1
    docker run --rm --name $NAME --user \$(id -u):\$(id -g) -v ~/models:/models -v ~/engines/hf-cli/cache:/tmp/.cache/huggingface karspark/hf-cli:2.2.0-xet1.7.0 \
      download $REPO --revision $REV --local-dir /models/$DEST && { echo FETCH-OK; exit 0; }
    echo \"retry \$i\"; sleep 20
  done; echo FETCH-FAILED; exit 1' > ~/models/$DEST.fetch.log 2>&1 < /dev/null &"
echo "fetching $REPO@${REV:0:8} -> ~/models/$DEST (container $NAME, log ~/models/$DEST.fetch.log)"
