#!/usr/bin/env bash
# Snapshot the Spark's serving config into spark/ (a read-only mirror).
# The Spark is the source of truth; this repo records what is deployed so changes show up in git diff.
# Secrets (api-key, */env) are never copied.
#
#   scripts/pull-config.sh [ssh-host]     default: spark (Tailscale); use spark-lan at home
set -euo pipefail
HOST="${1:-spark}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DST="$ROOT/spark"
rm -rf "$DST" && mkdir -p "$DST"

# remote path -> mirror path (relative to spark/)
FILES=(
  ".config/systemd/user/qwen38-flash.service             systemd/user/qwen38-flash.service"
  ".config/systemd/user/qwen38-27b-hybrid.service        systemd/user/qwen38-27b-hybrid.service"
  ".config/systemd/user/qwen38-27b-abliterated.service   systemd/user/qwen38-27b-abliterated.service"
  ".config/systemd/user/vllm-122b.service                systemd/user/vllm-122b.service"
  ".config/systemd/user/comfyui.service                  systemd/user/comfyui.service"
  ".config/systemd/user/hermes-gateway.service           systemd/user/hermes-gateway.service"
  ".config/systemd/user/comfyui.service.d                systemd/user/comfyui.service.d"
  ".config/systemd/user/vllm-122b.service.d              systemd/user/vllm-122b.service.d"
  ".config/systemd/user/qwen38-27b-abliterated.service.d systemd/user/qwen38-27b-abliterated.service.d"
  "/etc/systemd/system/qwen38-flash.service              systemd/system/qwen38-flash.service"
  "/etc/systemd/system/qwen38-sglang.service             systemd/system/qwen38-sglang.service"
  "/etc/systemd/system/qwen38-keepalive.service          systemd/system/qwen38-keepalive.service"
  ".config/qwen38/launch-flash.sh                        qwen38/launch-flash.sh"
  ".config/qwen38/launch-flash.hybrid.sh                 qwen38/launch-flash.hybrid.sh"
  ".config/qwen38/launch-flash.hybrid-mtp.sh             qwen38/launch-flash.hybrid-mtp.sh"
  ".config/qwen38/keepalive-proxy.py                     qwen38/keepalive-proxy.py"
  ".config/qwen38/chat-template-flashnext.jinja          qwen38/chat-template-flashnext.jinja"
  ".config/qwen38/chat-template-sglang.jinja             qwen38/chat-template-sglang.jinja"
  ".config/qwen38/chat-template-sharp.jinja              qwen38/chat-template-sharp.jinja"
  ".config/qwen38/opencode.json                          qwen38/opencode.json"
  ".config/qwen38-27b-hybrid/serve.sh                    qwen38-27b-hybrid/serve.sh"
  ".config/qwen38-27b-abliterated/serve.sh               qwen38-27b-abliterated/serve.sh"
  "vllm-env/serve-qwen38-27b.sh                          vllm-env/serve-qwen38-27b.sh"
  "vllm-env/serve-qwen35-122b.sh                         vllm-env/serve-qwen35-122b.sh"
  "vllm-env/serve-qwen35-35b.sh                          vllm-env/serve-qwen35-35b.sh"
  "vllm-env/serve-nemotron.sh                            vllm-env/serve-nemotron.sh"
  "kathawin-work/bin/gpu_residency.sh                    bin/gpu_residency.sh"
  "kathawin-work/bin/hybrid-qwen-flux.sh                 bin/hybrid-qwen-flux.sh"
  "bin/switch-model.sh                                   bin/switch-model.sh"
  "bin/ds4_serve.sh                                      bin/ds4_serve.sh"
  "flash-recipes/switch-flash.sh                         bin/switch-flash.sh"
  "flux2/start_comfyui.sh                                flux2/start_comfyui.sh"
  "flux2/stop_comfyui.sh                                 flux2/stop_comfyui.sh"
  "flux2/THROUGHPUT.md                                   flux2/THROUGHPUT.md"
  "flash-exp/ledger.jsonl                                flash-exp/ledger.jsonl"
)

for line in "${FILES[@]}"; do
  read -r src dst <<<"$line"
  [[ "$src" = /* ]] || src="/home/karthik/$src"
  mkdir -p "$DST/$(dirname "$dst")"
  scp -q -r "$HOST:$src" "$DST/$dst" 2>/dev/null || echo "  missing on spark: $src"
done

# Drop backups and anything that looks like a secret.
find "$DST" \( -name '*.bak*' -o -name '*.removed-by-*' -o -name 'env' -o -name 'api-key' \) -delete
if grep -rIlE '(sk-[A-Za-z0-9]{20,}|api[_-]?key"?\s*[:=]\s*"?[A-Za-z0-9]{24,})' "$DST" ; then
  echo "WARNING: possible secret in the files above; scrub before committing" >&2
fi
echo "snapshot written to $DST"
