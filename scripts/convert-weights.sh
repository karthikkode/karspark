#!/usr/bin/env bash
# Convert a weight file already in ~/models (a .pth/.ckpt pickle or a TorchScript .pt, fetched at a pinned revision
# with scripts/fetch-model.sh) to .safetensors in the same folder, check that the model loads, then delete the
# original: ComfyUI only reads safetensors from ~/models.
# Runs in the comfyui image with no network and no GPU. Pickles go through spandrel's restricted unpickler and
# TorchScript through torch.jit (no Python code), so a file can't run code.
#   scripts/convert-weights.sh <kind>/<name>/<file> <out>.safetensors <check> [ssh-host]
#   check: spandrel (e.g. LaMa) | bg_removal (BiRefNet family, as ComfyUI's RemoveBackground loads it)
#   IMAGE=karspark/comfyui:<tag> overrides the image (default: the one in recipes/comfyui/comfy.yaml)
#   STRIP=<prefix> keeps only the keys under <prefix> and drops it (a TorchScript wrapper's "model.", torch.compile's "_orig_mod.")
set -euo pipefail
SRC="${1:?<kind>/<name>/<file>}"; OUT="${2:?<out>.safetensors}"; CHECK="${3:?spandrel|bg_removal}"; HOST="${4:-spark}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${IMAGE:-$(sed -n 's/^.*\(karspark\/comfyui:[^ ]*\).*$/\1/p' "$ROOT/recipes/comfyui/comfy.yaml" | head -1)}"
DIR="$(dirname "$SRC")"; FILE="$(basename "$SRC")"
case "$CHECK" in spandrel|bg_removal) ;; *) echo "check must be spandrel or bg_removal" >&2; exit 1;; esac
[[ "$OUT" == *.safetensors && "$OUT" != */* ]] || { echo "out must be a bare <name>.safetensors" >&2; exit 1; }
ssh "$HOST" "test -f ~/models/$SRC && docker run --rm --network none --memory 8g --user \$(id -u):\$(id -g) \
  -v ~/models/$DIR:/w --entrypoint python3 $IMAGE -c '
import sys
sys.path.insert(0, \"/opt/comfyui\")
sys.argv = [sys.argv[0], \"--cpu\"]
import comfy.options
comfy.options.enable_args_parsing()
import torch, spandrel
from safetensors.torch import save_file, load_file
sd = spandrel.ModelLoader().load_state_dict_from_file(\"/w/$FILE\")
if \"${STRIP:-}\":
    sd = {k[len(\"${STRIP:-}\"):]: v for k, v in sd.items() if k.startswith(\"${STRIP:-}\")}
save_file({k: v.contiguous() for k, v in sd.items() if isinstance(v, torch.Tensor)}, \"/w/$OUT\")
if \"$CHECK\" == \"spandrel\":
    m = spandrel.ModelLoader().load_from_file(\"/w/$OUT\")
    print(\"ok:\", m.architecture.name, m.purpose)
else:
    from comfy.bg_removal_model import load_background_removal_model
    m = load_background_removal_model(load_file(\"/w/$OUT\"))
    assert m is not None, \"not a BiRefNet state dict\"
    print(\"ok: BiRefNet\", m.model_type, m.image_size)
' && cd ~/models/$DIR && chmod 644 $OUT && rm -f $FILE && sha256sum $OUT >> SHA256SUMS && sort -u -o SHA256SUMS SHA256SUMS \
  && echo \"$OUT <- $FILE (converted by scripts/convert-weights.sh)\" >> SOURCES && sort -u -o SOURCES SOURCES \
  && echo stored ~/models/$DIR/$OUT"
