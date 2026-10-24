# comfyui (images, layers, video, speech)

One container, `karspark/comfyui:0.42.4-r9` ([engine](../../engines/comfyui/README.md)), serves every
diffusion model. Weights are read-only from `~/models` via `extra_model_paths.yaml`. The graphs live in
[workflows.py](../../engines/comfyui/adapter/workflows.py). Each one is the official ComfyUI template
for that model, flattened, and checked with `scripts/validate-comfy-graphs.sh`.

Status: **ready, not deployed** (Flash-Next owns the GPU). Not yet timed on the GPU. Recipe `comfyui:comfy`
(port 10020, mode `creative`, next to the planner LLM). From r4 the adapter speaks only the internal job
API (`/v1/run`, `/v1/jobs/{id}`, pause/cancel) for karspark-core; callers use core's `/v1/jobs` and MCP
tools with the parameters below.

## Models

| model (`"model"`) | what | weights | revision |
|---|---|---|---|
| `flux2-dev` | FLUX.2 [dev] NVFP4 (pure). Default: Turbo LoRA v2 at 12 steps. Up to 6 reference images for character consistency | `black-forest-labs/FLUX.2-dev-NVFP4` → `~/models/image/flux2-dev-nvfp4`; Mistral-3 FP4 TE, Turbo LoRA, VAE from `Comfy-Org/flux2-dev` → `~/models/image/flux2-dev-comfy` | `142b87e7`, `ed33133c` |
| `qwen-image-layered` | splits an image into RGBA layers (input for the parallax pipeline). fp8mixed | `Comfy-Org/Qwen-Image-Layered_ComfyUI` → `~/models/image/qwen-image-layered-comfy`; Qwen2.5-VL 7B fp8 TE from `Comfy-Org/Qwen-Image_ComfyUI` → `~/models/image/qwen-image-te` | `02130716`, `1f12b17b` |
| `ltx-2.5` | LTX-2.5 22B distilled (int8-convrot), text-to-video, image-to-video, first + last frame. Silent by default | `Lightricks/LTX-2.5` → `~/models/video/ltx-2.5` (Gemma-4 12B int8 TE, video/audio VAE, 2× latent upscaler) | `49b093a3` |
| `seedvr2-7b`, `seedvr2-7b-sharp`, `seedvr2-3b` | SeedVR2 one-step upscaler for images and video (7B fp16 = best quality, 7B sharp int8 = more detail, 3B fp16 = faster, video default) | `Comfy-Org/SeedVR2` → `~/models/image/seedvr2` | `df488797` |
| `esrgan-anime` (also the first step of `flux2-refine`) | RealESRGAN x4plus anime 6B. Instant, adds no detail | official GitHub release v0.2.2.4, converted to safetensors by `scripts/fetch-pth.sh` → `~/models/image/upscale-gan` | sha256 `f872d837…` (.pth) |
| character LoRAs | for `flux2-dev` | `~/models/image/loras/<name>.safetensors` | ours |
| `indextts-2.5` | IndexTTS-2.5 zero-shot TTS (bf16): text → narration WAV in a cloned voice. Not a ComfyUI graph: the adapter runs it in a worker process with its own venv, and `/v1/free` stops it like any family swap | `IndexTeam/IndexTTS-2.5` → `~/models/audio/indextts-2.5` (no `qwen0.6bemo4-merge`: emotion-from-text is off); `facebook/w2v-bert-2.0` → `~/models/audio/w2v-bert-2.0`; `funasr/campplus` → `~/models/audio/campplus`; `nvidia/bigvgan_v2_22khz_80band_256x` (generator only) → `~/models/audio/bigvgan-v2-22khz` | `c39ce5ba`, `da985ba0`, `e4b6ede7`, `633ff708` |
| voice `narrator-male` | the built-in narrator: `examples/voice_05.wav` of the `IndexTeam/IndexTTS-2-Demo` space (a calm male storyteller, Chinese speaker, 8.4 s; original sha256 `eefb7f4a…`), made mono and turned down 7 dB (`ffmpeg -ac 1 -af volume=-7dB`): the original peaks at 0 dBFS and IndexTTS copies its loudness, which clipped (274 full-scale samples in 10 s, now 17) | `~/models/audio/indextts-voices/narrator-male.wav` (sha256 `e6ec91e4…`) | space `b01840e8` |

## Job parameters (served by karspark-core from phase 2: `/v1/jobs`, MCP)

**Images** (`image`, `layers`): PNG files.

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

**Video**: an MP4, **silent by default**. We add music and voices separately.

```jsonc
{"prompt": "she turns toward the camera and smiles, wind in her hair", "first_frame": "<image>",
 "size": "1280x720", "duration": 5, "fps": 24, "seed": 7}
// no frames = text-to-video; first_frame = image-to-video; first_frame + last_frame = first-and-last-frame.
// "audio": "sfx" keeps LTX's soundtrack, steered to sound effects only (no music, no voices).
// size must be a multiple of 64, duration 1..20 s.
```

**Upscaling** (`upscale_image`): render small, then upscale.

```jsonc
{"model": "seedvr2-7b", "image": "<base64 png>", "scale": 2}            // best general quality (default model)
{"model": "flux2-refine", "image": "<base64 png>", "scale": 2,          // anime-safe: keeps FLUX.2's lines/faces
 "prompt": "describe the image", "loras": [{"name": "mychar.safetensors"}], "denoise": 0.35}
{"model": "esrgan-anime", "image": "<base64 png>", "scale": 4}          // instant preview
// scale > 1 and <= 4. seedvr2 also takes "color_correction": lab (default) | wavelet | adain | none
```

**Speech** (`speech`): a WAV, 22.05 kHz mono. The only source of voices (videos stay silent; mux it on).

```jsonc
{"text": "Paragraph one.\n\nParagraph two.", "voice": "narrator-male", "lang": "EN", "pause_ms": 600, "seed": 7}
// or "voice": "<file_id of a 3-15 s clean speech clip>" to clone it. Spell out numbers you want read aloud.
// The text is split into chunks of whole sentences; a pause keeps finished chunks and resume continues.
```

`upscale_video`: an MP4 that keeps the source's frame rate (and audio track, if any).

```jsonc
{"model": "seedvr2-3b", "video": "<base64 mp4>", "scale": 2}            // seedvr2-7b for hero shots
{"model": "esrgan-anime", "video": "<base64 mp4>", "scale": 3}          // anime to 4K: 1280x704 -> 3840x2112
```



## Settings and why

- **FLUX.2**: guidance 4, euler, `Flux2Scheduler`. References are scaled to 1 MP and chained as
  `ReferenceLatent`, exactly as in the template. Turbo LoRA at 12 steps is the default (8 worked, 12 is the owner's pick for quality).
  The base model at 20 steps is about 1.7x slower.
- **Qwen-Image-Layered**: template settings (20 steps, CFG 2.5, 640 px). The model card's original
  50 steps / CFG 4 more than doubles the time. 4 layers by default. Frame 0, the composite, is dropped.
- **LTX-2.5**: the template's distilled two-stage run. 8 steps at half resolution, a 2× latent upscale,
  then 3 steps at full resolution, CFG 1, euler_ancestral. The first frame is anchored at 0.7, then 1.0.
  First + last frame follows `video_ltx2_5_flf2v.json`: one stage at full size, `LTXVAddGuide` at frames 0
  and -1 (0.7, `"frame_strength"`), euler ancestral with eta 0, `LTXVCropGuides`.
- **Audio**: LTX-2.5 always samples audio with the video (the model is joint), but by default we don't
  decode it and the MP4 has no audio track. `"audio": "sfx"` decodes it and appends a direction to the
  prompt: diegetic sound effects only, no music, speech or singing. At CFG 1 a negative prompt has no
  effect, so the direction has to go in the prompt.
  The optional prompt enhancer (gemma4 e2b) is not downloaded, so prompts go in as written; write them
  like the template examples (camera, motion, sound).

- **Upscalers**: SeedVR2 settings come from the official templates (one euler step at denoise 1, tiled
  VAE), except colour correction is `lab` rather than `none`, to keep colours true to the source. SeedVR2
  learned mostly from real footage and can add texture to flat anime shading, so try it on our anime
  before trusting it there. `flux2-refine` is the safe path for character art: RealESRGAN-anime to the
  target size, then FLUX.2 Turbo at denoise 0.35 (about 4 of 12 steps). Video: LTX-2.5 already renders
  at half size and upscales 2× itself, so SeedVR2 is for going past 720p. `esrgan-anime` on video (r8) runs
  RealESRGAN per frame and then lanczos to the scale: no diffusion and no new texture, the anime path to 4K. It holds
  every 4x frame at once (~170 MB each at 1280x704), so clips over ~8 s at 720p risk running out of memory.

Runs in mode `creative` with the planner (111 GiB together). ComfyUI loads weights on demand, and core frees the
previous family before a different one runs: about 34 GB for FLUX.2, 30 GB for
Qwen-Image-Layered, and 40 GB for LTX-2.5.
