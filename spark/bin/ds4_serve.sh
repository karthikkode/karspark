#!/usr/bin/env bash
# Launch ds4-server with the full DSpark speculative stack on port 8001.
set -u
export HOME=/home/karthik
export DS4_GGUF_DIR=/home/karthik/gguf
export DS4_DSPARK_QUENCH=0
cd /home/karthik/gguf
exec /home/karthik/code/ds4/ds4-server \
    --ctx 524288 \
    --host 0.0.0.0 \
    --port 8000 \
    --dspark /home/karthik/gguf/DSpark-drafter-Q2K-Q8-0731.gguf \
    --kv-disk-dir /home/karthik/ds4-kv \
    --kv-disk-space-mb 65536 \
    "$@"
