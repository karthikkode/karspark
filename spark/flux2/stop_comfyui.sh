#!/usr/bin/env bash
# Stop ComfyUI. Skips its own PID so the pattern match can't kill this script.
SELF=$$
for p in $(pgrep -f "venv/bin/python main.py"); do
  [ "$p" = "$SELF" ] && continue
  kill "$p" 2>/dev/null
done
sleep 4
pgrep -f "venv/bin/python main.py" >/dev/null && echo "still running" || echo "ComfyUI stopped"
