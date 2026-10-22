#!/usr/bin/env bash
# Deploy the gateway to the Spark. Everything runs in Docker: the gateway image is built from gateway/
# (scripts/build-images.sh), engine state files come from engines/*/spark/, and the rendered config is
# validated by the gateway image itself before it replaces the live one. llama-swap hot-reloads the config.
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

echo "== images"
"$ROOT/scripts/build-images.sh" "$HOST"

echo "== helpers -> ~/bin"
scp -q "$ROOT"/spark-bin/* "$HOST:bin/"
ssh "$HOST" 'chmod +x ~/bin/*'

echo "== engine files -> ~/engines/<engine>/"
for d in "$ROOT"/engines/*/spark; do
  [ -d "$d" ] || continue
  e="$(basename "$(dirname "$d")")"
  ssh "$HOST" "mkdir -p ~/engines/$e"
  tar -C "$d" -cf - . | ssh "$HOST" "tar -C ~/engines/$e -xf -"
  echo "   $e"
done

echo "== secret"
ssh "$HOST" 'test -s ~/gateway/env || { umask 077; printf "KARSPARK_API_KEY=sk-ks-%s\n" "$(head -c 36 /dev/urandom | base64 | tr -d "/+=")" > ~/gateway/env; echo "   generated a new API key in ~/gateway/env"; }; chmod 600 ~/gateway/env'

echo "== config (validated by karspark/gateway:$V)"
scp -q "$TMP/config.yaml" "$HOST:gateway/config.yaml.new"
ssh "$HOST" "docker run --rm --env-file ~/gateway/env -e KARSPARK_MODELS=\$HOME/models -e KARSPARK_ENGINES=\$HOME/engines \
    -v ~/gateway:/gateway:ro karspark/gateway:$V -config /gateway/config.yaml.new -validate >/dev/null \
    || { echo 'llama-swap rejected the config; live config untouched' >&2; rm -f ~/gateway/config.yaml.new; exit 1; }"
ssh "$HOST" 'if cmp -s ~/gateway/config.yaml.new ~/gateway/config.yaml; then
    rm ~/gateway/config.yaml.new; echo "   config unchanged (loaded models keep running)"
  else
    # A config reload stops every loaded model, so say so.
    K=$(sed -n "s/^KARSPARK_API_KEY=//p" ~/gateway/env)
    curl -s -m4 localhost:8080/running -H "Authorization: Bearer $K" | grep -q "\"model\"" && echo "   NOTE: config changed; loaded models are unloaded by the reload"
    mv ~/gateway/config.yaml.new ~/gateway/config.yaml; echo "   config updated"
  fi'

echo "== unit"
scp -q "$ROOT/gateway/karspark-gateway.service" "$HOST:gateway/karspark-gateway.service.new"
ssh "$HOST" "if cmp -s ~/gateway/karspark-gateway.service.new ~/.config/systemd/user/karspark-gateway.service \
      && [ \"\$(docker inspect -f '{{.Config.Image}}' karspark-gateway 2>/dev/null)\" = karspark/gateway:$V ]; then
    rm ~/gateway/karspark-gateway.service.new
    systemctl --user is-active --quiet karspark-gateway || systemctl --user start karspark-gateway
  else
    mv ~/gateway/karspark-gateway.service.new ~/.config/systemd/user/karspark-gateway.service
    systemctl --user daemon-reload && systemctl --user enable karspark-gateway >/dev/null 2>&1
    systemctl --user restart karspark-gateway; echo '   gateway (re)started'
  fi
  for i in \$(seq 1 20); do curl -s -o /dev/null -m2 localhost:8080/health && break; sleep 1; done
  systemctl --user is-active karspark-gateway"

echo "deployed. recipes:"
ssh "$HOST" 'curl -s -m5 localhost:8080/v1/models -H "Authorization: Bearer $(sed -n "s/^KARSPARK_API_KEY=//p" ~/gateway/env)" | python3 -c "import sys,json;[print(\"  \",m[\"id\"]) for m in json.load(sys.stdin)[\"data\"]]"'
