#!/usr/bin/env bash
# Make room for one big model by evicting the others, then WAIT until the memory is
# actually back. Runs ON THE SPARK.
#
# vLLM, IndexTTS-2 and ComfyUI all draw on the same 121 GB of unified memory and none of
# them fits beside another. `Conflicts=` in the unit file stops the loser, but stopping is
# not instant: the process has to unmap tens of gigabytes, and systemd returns long before
# the kernel has handed the pages back. vLLM's own startup then measures free memory and
# refuses:
#
#   ValueError: Free memory on device cuda:0 (86.46/121.69 GiB) on startup is less than
#   desired GPU memory utilization (0.8, 97.35 GiB)
#
# That is what killed the 2026-08-01 session -- not a broken install, a race. It reads as
# a hard failure and sends you looking at the model.
#
# render_chapter.sh handled it with `sleep 20`, which is a guess in both directions: too
# long when the unit was never running, too short when it was mid-teardown of a 60 GB
# model. Poll the number instead.
#
#   gpu_residency.sh <need-GiB> [unit-to-stop ...]
set -u
NEED="${1:?usage: gpu_residency.sh <need-GiB> [unit ...]}"
shift
DEADLINE=$((SECONDS + ${RESIDENCY_WAIT:-240}))

for u in "$@"; do
  if systemctl --user is-active --quiet "$u"; then
    echo "[residency] stopping $u"
    systemctl --user stop "$u"
  fi
done

# Anything started by hand is outside systemd's reach -- and ComfyUI HAS been run that way,
# which is what made its own Conflicts= silently inert. Report it; do not pattern-kill it,
# because `pkill -f` matches the ssh command line carrying this script.
stray="$(pgrep -af 'ComfyUI/main.py|vllm.entrypoints|indextts' 2>/dev/null | grep -v gpu_residency || true)"
[ -n "$stray" ] && echo "[residency] NOTE: GPU processes outside systemd:" && echo "$stray"

avail () { awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo; }

while :; do
  a="$(avail)"
  if [ "$a" -ge "$NEED" ]; then
    echo "[residency] ${a} GiB available, need ${NEED} -- ok"
    exit 0
  fi
  if [ "$SECONDS" -ge "$DEADLINE" ]; then
    echo "[residency] TIMEOUT: only ${a} GiB available, need ${NEED}"
    echo "$stray"
    exit 75   # EX_TEMPFAIL: a transient resource problem, not a bad configuration
  fi
  echo "[residency] ${a}/${NEED} GiB, waiting"
  sleep 5
done
