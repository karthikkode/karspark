#!/usr/bin/env bash
# Deploy one service from this repo to the Spark. Copies files and installs the unit; never starts it.
#
#   scripts/deploy.sh <service> [ssh-host]      ssh-host default: spark
#   scripts/deploy.sh --bin [ssh-host]          only refresh ~/bin from spark-bin/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SVC="${1:?usage: deploy.sh <service>|--bin [ssh-host]}"
HOST="${2:-spark}"

ssh "$HOST" 'mkdir -p ~/bin ~/models ~/engines ~/services ~/scratch ~/.config/systemd/user'
scp -q "$ROOT"/spark-bin/* "$HOST:bin/"
ssh "$HOST" 'chmod +x ~/bin/*'
echo "~/bin refreshed"
[ "$SVC" = "--bin" ] && exit 0

D="$ROOT/services/$SVC"
[ "$SVC" != "_template" ] || { echo "copy the template first" >&2; exit 1; }
for f in README.md service.unit launch.sh env.example; do
  [ -f "$D/$f" ] || { echo "missing services/$SVC/$f (see docs/standard.md)" >&2; exit 1; }
done
grep -q "| $SVC |" "$ROOT/docs/registry.md" || { echo "add $SVC to docs/registry.md first" >&2; exit 1; }
if grep -RIl '<service>\|<port>\|<GiB>' "$D" >/dev/null; then echo "template placeholders left in $D" >&2; exit 1; fi

ssh "$HOST" "mkdir -p ~/services/$SVC"
tar -C "$D" --exclude=env -cf - . | ssh "$HOST" "tar -C ~/services/$SVC -xf - && chmod +x ~/services/$SVC/launch.sh"
ssh "$HOST" "cp ~/services/$SVC/service.unit ~/.config/systemd/user/$SVC.service && systemctl --user daemon-reload
  [ -f ~/services/$SVC/env ] || { echo 'NOTE: create ~/services/$SVC/env (chmod 600) from env.example before starting'; }"
echo "deployed $SVC (not started). Start: ssh $HOST 'systemctl --user start $SVC'"
