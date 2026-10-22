#!/usr/bin/env bash
# Runs ON THE SPARK (started detached by scripts/fetch-model.sh). Retries a pinned download in the
# karspark/hf-cli container until it completes. Gated repos use the token in ~/engines/hf-cli/token
# (chmod 600, created by the user); it reaches the container through a 600 env file, never argv.
#   fetch-loop.sh <name> <repo> <rev> <kind/name> [include-glob ...]
set -uo pipefail
NAME="$1"; REPO="$2"; REV="$3"; DEST="$4"; shift 4
INC=(); for g in "$@"; do INC+=(--include "$g"); done
H=~/engines/hf-cli
ENVF=(); if [ -s "$H/token" ]; then
  umask 077; printf 'HF_TOKEN=%s\n' "$(tr -d '[:space:]' < "$H/token")" > "$H/.run-$NAME.env"; ENVF=(--env-file "$H/.run-$NAME.env")
fi
trap 'rm -f "$H/.run-$NAME.env"' EXIT
for i in $(seq 1 50); do
  docker rm -f "$NAME" >/dev/null 2>&1
  # HARD RULE: model bytes live only in ~/models. No Xet chunk cache; staging is cleaned on success.
  docker run --rm --name "$NAME" --user "$(id -u):$(id -g)" "${ENVF[@]}" -e HF_XET_CHUNK_CACHE_SIZE_BYTES=0 \
    -v ~/models:/models -v "$H/cache:/tmp/.cache/huggingface" karspark/hf-cli:2.2.0-xet1.7.0 \
    download "$REPO" --revision "$REV" --local-dir "/models/$DEST" "${INC[@]}" && {
      rm -rf "$H/cache/xet" "$HOME/models/$DEST/.cache"; echo FETCH-OK; exit 0; }
  echo "retry $i"; sleep 20
done
echo FETCH-FAILED; exit 1
