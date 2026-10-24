#!/usr/bin/env bash
# Deploy karspark-core to the Spark. Everything runs in Docker: images are built from this repo
# (scripts/build-images.sh), the config (recipes/ + config/) is validated here first, then copied to
# ~/core/config. Restarting core does not unload models: engines run detached and core adopts them, except an
# engine whose recipe changed, which reloads.
#
#   scripts/deploy.sh [--cutover] [ssh-host]      default host: spark
#
# --cutover: the one-time switch from the llama-swap gateway. Stopping the gateway stops every model it
# started (Flash-Next reloads, ~6 min), so deploy refuses to do it without this flag.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CUTOVER=0; [ "${1:-}" = "--cutover" ] && { CUTOVER=1; shift; }
HOST="${1:-spark}"
IMAGE="$(sed -n 's/^.*\(karspark\/core:[^ ]*\).*$/\1/p' "$ROOT/core/karspark-core.service" | head -1)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "== validate config"
(cd "$ROOT/core" && uv run --quiet python -m karspark.config "$ROOT")

if ssh "$HOST" 'systemctl --user is-active --quiet karspark-gateway' && (( ! CUTOVER )); then
  echo "the llama-swap gateway is still running. Stopping it unloads its models (Flash-Next reloads, ~6 min)." >&2
  echo "re-run with --cutover when that is OK." >&2
  exit 1
fi

ssh "$HOST" 'mkdir -p ~/bin ~/models ~/engines ~/core/config ~/data ~/scratch ~/.config/systemd/user'

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

echo "== secrets (~/core/env, never printed)"
# First run: a new key-hashing pepper; the existing API key moves over from the gateway (it becomes the
# owner key `karthik-coding`), or a new one is generated.
ssh "$HOST" 'test -s ~/core/env || { umask 077; {
    printf "KARSPARK_KEY_PEPPER=%s\n" "$(head -c 48 /dev/urandom | base64 | tr -d "/+=")"
    if [ -s ~/gateway/env ]; then grep "^KARSPARK_API_KEY=" ~/gateway/env
    else printf "KARSPARK_API_KEY=sk-ks-%s\n" "$(head -c 36 /dev/urandom | base64 | tr -d "/+=")"; fi
  } > ~/core/env; echo "   created ~/core/env"; }; chmod 600 ~/core/env'

echo "== config -> ~/core/config"
mkdir -p "$TMP/config"
(cd "$ROOT" && tar -cf - config $(ls recipes/*/*.yaml)) | tar -C "$TMP/config" -xf -
ssh "$HOST" 'rm -rf ~/core/config.new && mkdir -p ~/core/config.new'
tar -C "$TMP/config" -cf - . | ssh "$HOST" 'tar -C ~/core/config.new -xf -'
CHANGED=$(ssh "$HOST" 'if diff -rq ~/core/config.new ~/core/config >/dev/null 2>&1; then rm -rf ~/core/config.new; echo 0;
  else rm -rf ~/core/config && mv ~/core/config.new ~/core/config; echo 1; fi')
(( CHANGED )) && echo "   config updated" || echo "   config unchanged"

if (( CUTOVER )); then
  echo "== cutover: stopping the llama-swap gateway (its models stop with it)"
  ssh "$HOST" 'systemctl --user disable --now karspark-gateway 2>/dev/null || true; rm -f ~/.config/systemd/user/karspark-gateway.service; systemctl --user daemon-reload'
fi

echo "== unit"
# A restart during a mode switch loses the switch target: core comes back to the saved mode and reloads it
# (2026-10-24: a deploy mid-switch to creative reloaded Flash-Next). Wait for a running switch to finish.
for i in $(seq 1 120); do
  sw="$(ssh "$HOST" 'curl -s -m5 localhost:8080/health' | python3 -c 'import json,sys; print(json.load(sys.stdin).get("switching") or "")' 2>/dev/null || true)"
  [ -z "$sw" ] && break
  (( i == 1 )) && echo "   a mode switch to $sw is running; waiting for it before restarting core"
  sleep 10
done
scp -q "$ROOT/core/karspark-core.service" "$HOST:core/karspark-core.service.new"
ssh "$HOST" "unit=~/.config/systemd/user/karspark-core.service
  if cmp -s ~/core/karspark-core.service.new \$unit && [ $CHANGED = 0 ] \
     && [ \"\$(docker inspect -f '{{.Config.Image}}' karspark-core 2>/dev/null)\" = $IMAGE ]; then
    rm ~/core/karspark-core.service.new
    systemctl --user is-active --quiet karspark-core || systemctl --user start karspark-core
  else
    mv ~/core/karspark-core.service.new \$unit
    systemctl --user daemon-reload && systemctl --user enable karspark-core >/dev/null 2>&1
    systemctl --user restart karspark-core; echo '   core (re)started (engines adopted; any whose recipe changed reloads)'
  fi
  for i in \$(seq 1 30); do curl -s -o /dev/null -m2 localhost:8080/health && break; sleep 1; done
  systemctl --user is-active karspark-core"

echo "deployed:"
ssh "$HOST" 'curl -s -m5 localhost:8080/health; echo'
