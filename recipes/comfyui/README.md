# comfyui (images + video)

One container, `karspark/comfyui:0.42.4` ([engine](../../engines/comfyui/README.md)), serves every
diffusion model. Weights are read-only from `~/models` via `extra_model_paths.yaml`.

| how to use | endpoint |
|---|---|
| OpenAI images API (FLUX.2) | `POST /v1/images/generations` with `"model": "flux2-dev"` (+ karspark extensions: `reference_images`, `loras`, `steps`, `seed`) |
| LTX-2.5 video | `POST /upstream/comfyui_auto/v1/videos/generations` |
| Full ComfyUI app + native API (character workflows, LoRAs, templates) | `http://<spark>:8080/comfyui/` |

| model | weights | revision |
|---|---|---|
| FLUX.2 [dev] NVFP4 (pure) | `black-forest-labs/FLUX.2-dev-NVFP4` → `~/models/image/flux2-dev-nvfp4` | `142b87e7` |
| Mistral-3 FP4 text encoder, Turbo LoRA v2, VAE | `Comfy-Org/flux2-dev` → `~/models/image/flux2-dev-comfy` | `ed33133c` |
| LTX-2.5 22B distilled (int8-convrot), Gemma-4 12B int8 TE, video/audio VAE, 2× spatial upscaler | `Lightricks/LTX-2.5` → `~/models/video/ltx-2.5` | `49b093a3` |
| Character LoRAs | `~/models/image/loras/<character>.safetensors` | ours |

Runs alone (not in a routing set). ~36 GB of FLUX weights or ~40 GB of LTX weights loaded on demand by ComfyUI.
