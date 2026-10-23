#!/usr/bin/env bash
# Fetch a small PyTorch .pth weight that is not on Hugging Face (e.g. an ESRGAN upscaler from a GitHub
# release), check its sha256, and store it on the Spark as .safetensors in ~/models/<kind>/<name>/.
# The download happens on the laptop (GitHub crawls from the Spark). The conversion runs in the comfyui
# image with torch.load(weights_only=True), so a pickle cannot run code.
#   scripts/fetch-pth.sh <url> <sha256> <kind>/<name> [ssh-host]
set -euo pipefail
URL="$1"; SHA="$2"; DEST="$3"; HOST="${4:-spark}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="$(sed -n 's/^.*\(karspark\/comfyui:[^ ]*\).*$/\1/p' "$ROOT/recipes/comfyui/comfy.yaml" | head -1)"
FILE="$(basename "${URL%%\?*}")"; OUT="${FILE%.pth}.safetensors"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/$FILE" "$URL"
echo "$SHA  $tmp/$FILE" | sha256sum -c - >/dev/null || { echo "sha256 mismatch for $URL" >&2; exit 1; }
ssh "$HOST" "mkdir -p ~/scratch/.pth ~/models/$DEST"
scp -q "$tmp/$FILE" "$HOST:scratch/.pth/$FILE"
ssh "$HOST" "docker run --rm --memory 4g --user \$(id -u):\$(id -g) -v ~/scratch/.pth:/in:ro -v ~/models/$DEST:/out \
  --entrypoint python3 $IMAGE -c '
import torch, spandrel
from safetensors.torch import save_file
sd = torch.load(\"/in/$FILE\", map_location=\"cpu\", weights_only=True)
sd = sd.get(\"params_ema\", sd.get(\"params\", sd))
save_file({k: v.contiguous() for k, v in sd.items()}, \"/out/$OUT\")
m = spandrel.ModelLoader().load_from_file(\"/out/$OUT\")
print(\"ok:\", m.architecture.name, \"x%d\" % m.scale)
' && rm -rf ~/scratch/.pth && cd ~/models/$DEST && chmod 644 $OUT && sha256sum $OUT >> SHA256SUMS && sort -u -o SHA256SUMS SHA256SUMS \
  && echo \"$OUT <- $URL (sha256 $SHA)\" >> SOURCES && sort -u -o SOURCES SOURCES && echo stored ~/models/$DEST/$OUT"
