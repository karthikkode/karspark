#!/usr/bin/env bash
# Deploy the gateway to the Spark: helpers, llama-swap binary (pinned in gateway/VERSION), unit, and
# the rendered config. llama-swap watches its config, so a deploy never drops the endpoint.
#
#   scripts/deploy.sh [ssh-host]      default: spark
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${1:-spark}"
V="$(cat "$ROOT/gateway/VERSION")"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "== render config"
python3 "$ROOT/scripts/render-config.py" > "$TMP/config.yaml"

ssh "$HOST" 'mkdir -p ~/bin ~/models ~/engines ~/gateway ~/scratch ~/.config/systemd/user'

echo "== helpers -> ~/bin"
scp -q "$ROOT"/spark-bin/* "$HOST:bin/"
ssh "$HOST" 'chmod +x ~/bin/*'

echo "== llama-swap v$V"
if ! ssh "$HOST" "test -x ~/engines/llama-swap/v$V/llama-swap"; then
  ( cd "$TMP"
    curl -fsSLO "https://github.com/mostlygeek/llama-swap/releases/download/v$V/llama-swap_${V}_linux_arm64.tar.gz"
    curl -fsSLO "https://github.com/mostlygeek/llama-swap/releases/download/v$V/llama-swap_${V}_checksums.txt"
    grep linux_arm64 "llama-swap_${V}_checksums.txt" | sha256sum -c - >/dev/null
    tar -xzf "llama-swap_${V}_linux_arm64.tar.gz" llama-swap )
  ssh "$HOST" "mkdir -p ~/engines/llama-swap/v$V"
  scp -q "$TMP/llama-swap" "$HOST:engines/llama-swap/v$V/llama-swap"
fi
ssh "$HOST" "chmod +x ~/engines/llama-swap/v$V/llama-swap && ln -sfn v$V ~/engines/llama-swap/current"

echo "== secret"
ssh "$HOST" 'test -s ~/gateway/env || { umask 077; printf "KARSPARK_API_KEY=sk-ks-%s\n" "$(head -c 36 /dev/urandom | base64 | tr -d "/+=")" > ~/gateway/env; echo "   generated a new API key in ~/gateway/env"; }; chmod 600 ~/gateway/env'

echo "== config + unit"
scp -q "$TMP/config.yaml" "$HOST:gateway/config.yaml.new"
ssh "$HOST" 'set -a; . ~/gateway/env; set +a
  ~/engines/llama-swap/current/llama-swap -config ~/gateway/config.yaml.new -validate >/dev/null \
    || { echo "llama-swap rejected the config; live config untouched" >&2; ~/engines/llama-swap/current/llama-swap -config ~/gateway/config.yaml.new -validate; exit 1; }
  if cmp -s ~/gateway/config.yaml.new ~/gateway/config.yaml; then
    rm ~/gateway/config.yaml.new; echo "   config unchanged (loaded models keep running)"
  else
    # A config reload stops every loaded model, so say so.
    K=$(sed -n "s/^KARSPARK_API_KEY=//p" ~/gateway/env)
    curl -s -m4 localhost:8080/running -H "Authorization: Bearer $K" | grep -q "\"model\"" && echo "   NOTE: config changed; loaded models are unloaded by the reload"
    mv ~/gateway/config.yaml.new ~/gateway/config.yaml; echo "   config updated"
  fi'
scp -q "$ROOT/gateway/karspark-gateway.service" "$HOST:.config/systemd/user/karspark-gateway.service"
ssh "$HOST" 'systemctl --user daemon-reload && systemctl --user enable karspark-gateway >/dev/null 2>&1
  if systemctl --user is-active --quiet karspark-gateway; then
    [ "$(readlink -f ~/engines/llama-swap/current)" = "$(readlink -f /proc/$(systemctl --user show -p MainPID --value karspark-gateway)/exe)" ] \
      || systemctl --user restart karspark-gateway
  else
    systemctl --user start karspark-gateway
  fi
  sleep 2; systemctl --user is-active karspark-gateway'
echo "deployed. models:"
ssh "$HOST" 'curl -s -m5 localhost:8080/v1/models -H "Authorization: Bearer $(sed -n "s/^KARSPARK_API_KEY=//p" ~/gateway/env)" | python3 -c "import sys,json;[print(\"  \",m[\"id\"]) for m in json.load(sys.stdin)[\"data\"]]"'
