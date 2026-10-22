#!/usr/bin/env bash
# switch-flash.sh hybrid|baseline  -> swaps the service config and triggers a clean restart
set -euo pipefail
CFG=/home/karthik/.config/qwen38
case "${1:-}" in
  hybrid)   cp $CFG/launch-flash.hybrid.sh $CFG/launch-flash.sh ;;
  baseline) cp /home/karthik/flash-exp/launch-flash.orig $CFG/launch-flash.sh ;;
  *) echo "usage: switch-flash.sh hybrid|baseline"; exit 2 ;;
esac
echo "config now: $(sha256sum $CFG/launch-flash.sh | cut -c1-12)"
docker stop -t 20 qwen38-flash >/dev/null 2>&1 || true
echo "restart triggered via systemd (Restart=always); load takes ~13-15 min"
