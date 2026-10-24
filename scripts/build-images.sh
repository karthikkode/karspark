#!/usr/bin/env bash
# Build every karspark/* image on the Spark from its Dockerfile in this repo (skips ones already present).
#   scripts/build-images.sh [--force] [ssh-host]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORCE=0; [ "${1:-}" = "--force" ] && { FORCE=1; shift; }
HOST="${1:-spark}"

# Inputs that must not be downloaded from the Spark (GitHub crawls at ~30 KB/s there): fetch on the laptop.
prefetch_core() {
  # core validates media params with the adapter's own graph builder, and installs from the uv lock.
  mkdir -p "$ROOT/core/.dl"
  cp "$ROOT/engines/comfyui/adapter/workflows.py" "$ROOT/core/.dl/workflows.py"
  (cd "$ROOT/core" && uv export --no-dev --no-emit-project --format requirements-txt -q > requirements.txt)
}

prefetch_comfyui() {
  local f="$ROOT/engines/comfyui/.dl/comfyui.tar.gz" tag=v0.42.4 sha=3f438ca69f3d0efc5f748de055daeea9ba26ee50
  if [ ! -s "$f" ]; then
    mkdir -p "$ROOT/engines/comfyui/.dl"
    curl -fsSL -o "$f" "https://codeload.github.com/comfyanonymous/ComfyUI/tar.gz/$sha"
  fi
  # the tarball's top dir is named after the commit, which proves what we fetched
  local top; top="$(tar -tzf "$f" 2>/dev/null | head -1 || true)"
  [ "$top" = "ComfyUI-$sha/" ] || { echo "comfyui tarball is not commit $sha" >&2; exit 1; }
}

prefetch_indextts() {
  prefetch_comfyui
  local f="$ROOT/engines/comfyui/.dl/indextts.tar.gz" sha=d9e41aac89fd00b3d71497fddb287b7f24613712
  [ -s "$f" ] || curl -fsSL -o "$f" "https://codeload.github.com/index-tts/index-tts/tar.gz/$sha"
  local top; top="$(tar -tzf "$f" 2>/dev/null | head -1 || true)"
  [ "$top" = "index-tts-$sha/" ] || { echo "index-tts tarball is not commit $sha" >&2; exit 1; }
}

# tag | build context | prefetch fn | build args
IMAGES=(
  "karspark/hf-cli:2.2.0-xet1.7.0|engines/hf-cli||"
  "karspark/core:0.1.0-r20|core|prefetch_core|"
  "karspark/bench:4|bench||"
  "karspark/comfyui:0.42.4-r14|engines/comfyui|prefetch_indextts|"
  "karspark/vllm-salient:2026-10-14-77154ca6|engines/vllm-salient/upstream||BASE=vllm/vllm-openai@sha256:a7deff2c216def98c193352aca50fb03a2ee29231355b3033dc3789009f6d23e"
)
for line in "${IMAGES[@]}"; do
  IFS='|' read -r tag ctx pre args <<<"$line"
  if (( ! FORCE )) && ssh "$HOST" "docker image inspect $tag >/dev/null 2>&1"; then echo "have  $tag"; continue; fi
  echo "build $tag  <- $ctx"
  [ -n "$pre" ] && "$pre"
  ba=""; for a in $args; do ba+=" --build-arg $a"; done
  # Build from a directory on the Spark: a context streamed over ssh stdin stalls BuildKit's session.
  dir="scratch/.build-$(echo "$tag" | tr ':/' '__')"
  ssh "$HOST" "rm -rf ~/$dir && mkdir -p ~/$dir"
  tar -C "$ROOT/$ctx" --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=tests -cf - . | ssh "$HOST" "tar -C ~/$dir -xf -"
  ssh "$HOST" "docker build -q$ba -t $tag ~/$dir >/dev/null && rm -rf ~/$dir"
done
