#!/usr/bin/env bash
# One-screen, read-only view of what the Spark is doing right now. Changes nothing.
#
#   scripts/status.sh [ssh-host]     default: spark (Tailscale); spark-lan at home
set -uo pipefail
HOST="${1:-spark}"
ssh -o ConnectTimeout=10 "$HOST" 'bash -s' <<'REMOTE'
echo "== $(hostname)  $(uptime -p)"
free -g | awk '/Mem:/ {printf "== unified memory: %s GiB used / %s total, %s available\n", $3, $2, $7}'
df -h / | awk 'NR==2 {printf "== disk: %s used of %s (%s)\n", $3, $2, $5}'
echo
echo "== model / media services (one big GPU tenant at a time)"
for u in qwen38-flash qwen38-27b-hybrid vllm-122b qwen38-27b-abliterated comfyui hermes-gateway; do
  printf "  user   %-26s %-9s %s\n" "$u" "$(systemctl --user is-active $u 2>/dev/null)" "$(systemctl --user is-enabled $u 2>/dev/null)"
done
for u in qwen38-flash qwen38-sglang qwen38-keepalive; do
  printf "  system %-26s %-9s %s\n" "$u" "$(systemctl is-active $u 2>/dev/null)" "$(systemctl is-enabled $u 2>/dev/null)"
done
echo
echo "== containers"
docker ps --format '  {{.Names}}  {{.Image}}  {{.Status}}  {{.Ports}}'
echo
echo "== listening model ports"
for p in 8000 8002 8188 30000 30001; do
  ss -ltn "sport = :$p" | grep -q LISTEN && echo "  :$p  up" || echo "  :$p  -"
done
K="$(cat ~/.config/qwen38/api-key 2>/dev/null)"
for p in 30000 8000 8002; do
  m=$(curl -s -m3 -H "Authorization: Bearer $K" "localhost:$p/v1/models" 2>/dev/null | python3 -c 'import sys,json;print(",".join(d["id"] for d in json.load(sys.stdin)["data"]))' 2>/dev/null)
  [ -n "$m" ] && echo "  :$p serves $m"
done
echo
echo "== GPU processes"
nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null | sed 's/^/  /'
REMOTE
