#!/usr/bin/env bash
# switch-model.sh — Swap the local inference model on the DGX Spark.
# Usage: switch-model.sh <qwen|deepseek>
#
# Both models serve on port 8000 (standardized), so Hermes + Codex always
# point at :8000. This script updates the model NAME + reasoning effort in
# both configs so whichever model is loaded is what the tools use.
#
#   qwen     -> Qwen3.8-27B-NVFP4 via vLLM (service vllm-122b), reasoning xhigh
#   deepseek -> DeepSeek V4 Flash via ds4-server, reasoning max
set -euo pipefail

TARGET="${1:-}"
if [[ "$TARGET" != "qwen" && "$TARGET" != "deepseek" ]]; then
    echo "Usage: $0 <qwen|deepseek>" >&2
    exit 1
fi

echo "=== Switching to: $TARGET ==="

# 1. Stop whichever model is currently running
if systemctl --user is-active vllm-122b >/dev/null 2>&1; then
    echo "[1/4] Stopping Qwen (vLLM)..."
    systemctl --user stop vllm-122b
    sleep 3
fi
DS4_PID=$(pgrep -f 'ds4-server --ctx' | head -1 || true)
if [[ -n "$DS4_PID" ]]; then
    echo "[1/4] Stopping DeepSeek (ds4-server pid $DS4_PID)..."
    kill -9 "$DS4_PID"
    sleep 3
fi
# Ensure port 8000 is free
if ss -tlnp 2>/dev/null | grep -q ':8000 '; then
    echo "ERROR: port 8000 still in use. Check what's running." >&2
    ss -tlnp 2>/dev/null | grep ':8000 ' >&2
    exit 1
fi
echo "[1/4] Port 8000 free."

# 2. Start the target model
if [[ "$TARGET" == "qwen" ]]; then
    echo "[2/4] Starting Qwen 3.8-27B (vLLM)..."
    systemctl --user start vllm-122b
    MODEL_NAME="qwen38-27b"
    REASONING="xhigh"
else
    echo "[2/4] Starting DeepSeek V4 Flash (ds4-server)..."
    nohup /home/karthik/bin/ds4_serve.sh > /tmp/ds4_server.log 2>&1 &
    MODEL_NAME="deepseek-v4-flash"
    REASONING="max"
fi

# 3. Wait for the model to be ready on :8000
echo "[3/4] Waiting for model to be ready..."
for i in $(seq 1 90); do
    H=$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null || echo 000)
    [[ "$H" == "200" ]] && { echo "      Ready after ${i} checks."; break; }
    sleep 3
done
H=$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null || echo 000)
if [[ "$H" != "200" ]]; then
    echo "ERROR: model did not become ready on :8000 (health=$H)." >&2
    exit 1
fi

# 4. Update Hermes config (model name + reasoning via extra_body)
echo "[4/4] Updating Hermes + Codex config to model=$MODEL_NAME..."
hermes config set model_aliases.spark-qwen38.model "$MODEL_NAME" --force >/dev/null 2>&1 || true
hermes config set model_aliases.spark-ds4.model "$MODEL_NAME" --force >/dev/null 2>&1 || true
hermes config set custom_providers.0.extra_body "{\"reasoning_effort\":\"$REASONING\"}" >/dev/null 2>&1 || true

# Update Codex config
CODEX_CFG="$HOME/.codex/config.toml"
if [[ -f "$CODEX_CFG" ]]; then
    cp "$CODEX_CFG" "$CODEX_CFG.bak" 2>/dev/null || true
    sed -i "s/^model = .*/model = \"$MODEL_NAME\"/" "$CODEX_CFG"
fi

echo ""
echo "=== Done. Loaded $TARGET as model '$MODEL_NAME' on :8000 ==="
echo "    Hermes + Codex both point at :8000 with reasoning=$REASONING"
