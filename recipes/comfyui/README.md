# comfyui (images, layers, video)

One container, `karspark/comfyui:0.42.4-r2` ([engine](../../engines/comfyui/README.md)), serves every
diffusion model. Weights are read-only from `~/models` via `extra_model_paths.yaml`. The graphs live in
[workflows.py](../../engines/comfyui/adapter/workflows.py). Each one is the official ComfyUI template
for that model, flattened, and checked with `scripts/validate-comfy-graphs.sh`.

Status: **ready, not deployed** (Flash-Next owns the GPU). Not yet timed on the GPU.

## Models

| model (`"model"`) | what | weights | revision |
|---|---|---|---|
| `flux2-dev` | FLUX.2 [dev] NVFP4 (pure). Default: Turbo LoRA v2 at 8 steps. Up to 6 reference images for character consistency | `black-forest-labs/FLUX.2-dev-NVFP4` → `~/models/image/flux2-dev-nvfp4`; Mistral-3 FP4 TE, Turbo LoRA, VAE from `Comfy-Org/flux2-dev` → `~/models/image/flux2-dev-comfy` | `142b87e7`, `ed33133c` |
| `qwen-image-layered` | splits an image into RGBA layers (input for the parallax pipeline). fp8mixed | `Comfy-Org/Qwen-Image-Layered_ComfyUI` → `~/models/image/qwen-image-layered-comfy`; Qwen2.5-VL 7B fp8 TE from `Comfy-Org/Qwen-Image_ComfyUI` → `~/models/image/qwen-image-te` | `02130716`, `1f12b17b` |
| `ltx-2.5` | LTX-2.5 22B distilled (int8-convrot), image-to-video or text-to-video, with audio | `Lightricks/LTX-2.5` → `~/models/video/ltx-2.5` (Gemma-4 12B int8 TE, video/audio VAE, 2× latent upscaler) | `49b093a3` |
| character LoRAs | for `flux2-dev` | `~/models/image/loras/<name>.safetensors` | ours |

## API (all through the gateway, one key)

**Images**: `POST /v1/images/generations`. The response is the OpenAI shape, `data[].b64_json`, plus `seed`.

```jsonc
// FLUX.2: text-to-image
{"model": "flux2-dev", "prompt": "...", "size": "832x1216", "n": 1, "seed": 7}
// FLUX.2: character consistency / edit from references (character sheet first). Without "size", the output
// takes the first reference's aspect ratio at 1 MP.
{"model": "flux2-dev", "prompt": "the girl from image 1, wearing the jacket from image 2, in a rainy alley",
 "reference_images": ["<base64 png>", "<base64 png>"],
 "loras": [{"name": "mychar.safetensors", "strength": 0.9}]}
// optional: "turbo": false (base model, 20 steps), "steps", "guidance" (4.0)

// Qwen-Image-Layered: image -> layers (returns one RGBA PNG per layer, background first)
{"model": "qwen-image-layered", "image": "<base64 png>", "layers": 4}
// optional: "prompt" (describe the whole image incl. hidden parts), "resolution": 640 | 1024, "steps" (20), "cfg" (2.5)
```

**Video**: `POST /upstream/comfyui_auto/v1/videos/generations` → `data[0].b64_json` is an MP4 with audio.

```jsonc
{"prompt": "she turns toward the camera and smiles, wind in her hair", "image": "<base64 first frame>",
 "size": "1280x720", "duration": 5, "fps": 24, "seed": 7}
// no "image" = text-to-video. size must be a multiple of 64, duration 1..20 s.
```

**Full ComfyUI app + native API** (custom graphs, templates): `http://<spark>:8080/comfyui/`.

## Settings and why

- **FLUX.2**: guidance 4, euler, `Flux2Scheduler`. References are scaled to 1 MP and chained as
  `ReferenceLatent`, exactly as in the template. Turbo LoRA at 8 steps is the default because it worked
  best for us. The base model at 20 steps is about 2.5x slower.
- **Qwen-Image-Layered**: template settings (20 steps, CFG 2.5, 640 px). The model card's original
  50 steps / CFG 4 more than doubles the time. 4 layers by default. Frame 0, the composite, is dropped.
- **LTX-2.5**: the template's distilled two-stage run. 8 steps at half resolution, a 2× latent upscale,
  then 3 steps at full resolution, CFG 1, euler_ancestral. The first frame is anchored at 0.7, then 1.0.
  The optional prompt enhancer (gemma4 e2b) is not downloaded, so prompts go in as written; write them
  like the template examples (camera, motion, sound).

Runs alone, not in a routing set. ComfyUI loads the weights on demand: about 34 GB for FLUX.2, 30 GB for
Qwen-Image-Layered, and 40 GB for LTX-2.5.
