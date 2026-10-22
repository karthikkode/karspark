#!/usr/bin/env bash
# Build every karspark/* image on the Spark from its Dockerfile in this repo (skips ones already present).
#   scripts/build-images.sh [--force] [ssh-host]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORCE=0; [ "${1:-}" = "--force" ] && { FORCE=1; shift; }
HOST="${1:-spark}"
V="$(cat "$ROOT/gateway/VERSION")"

# Inputs that must not be downloaded from the Spark (GitHub crawls at ~30 KB/s there): fetch on the laptop.
prefetch_gateway() {
  local f="$ROOT/gateway/.dl/llama-swap.tar.gz"
  local want=3401097bb426f99a4de191cc599d53acaee0152ce63d6ca15fb0a6098ea80280
  if ! echo "$want  $f" | sha256sum -c - >/dev/null 2>&1; then
    mkdir -p "$ROOT/gateway/.dl"
    curl -fsSL -o "$f" "https://github.com/mostlygeek/llama-swap/releases/download/v$V/llama-swap_${V}_linux_arm64.tar.gz"
    echo "$want  $f" | sha256sum -c - >/dev/null
  fi
}

# tag | build context | prefetch fn | build args
IMAGES=(
  "karspark/hf-cli:2.2.0-xet1.7.0|engines/hf-cli||"
  "karspark/gateway:$V|gateway|prefetch_gateway|"
  "karspark/bench:1|bench||"
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
  tar -C "$ROOT/$ctx" -cf - . | ssh "$HOST" "tar -C ~/$dir -xf -"
  ssh "$HOST" "docker build -q$ba -t $tag ~/$dir >/dev/null && rm -rf ~/$dir"
done
