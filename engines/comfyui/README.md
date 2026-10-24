# Engine: comfyui

Image `karspark/comfyui:0.42.4-r8`, built by `scripts/build-images.sh` from the [Dockerfile](Dockerfile):

- `python:3.12-slim` (pinned digest) + torch **2.13.0+cu130**, torchvision 0.28.0, torchaudio 2.11.0,
  comfy-kitchen 0.2.39 (built 2026-10-22, 6.9 GB; full set in `/opt/pip-freeze.txt` inside the image)
- ComfyUI **v0.42.4** (`3f438ca6`), fetched on the laptop as a commit tarball (GitHub crawls from the Spark)
  and checked to be that commit
- `extra_model_paths.yaml`: maps ComfyUI's model folders to `~/models` (read-only)
- `adapter/`: our process in front of ComfyUI, called only by karspark-core. One job = one ComfyUI run in a
  shared job folder (`/jobs/<id>/{in,out,ckpt}`): `POST /v1/run`, `GET /v1/jobs/{id}`, `/pause`, `/cancel`,
  `POST /v1/free`. Everything else (UI, `/prompt`, `/ws`) is proxied so core's `/comfyui/` serves the real
  app. The graphs are in `adapter/workflows.py`, flattened from the official templates in the image
  (`comfyui_workflow_templates_json/templates/`)
- `karspark_nodes/`: `KarsparkSampler`, a pausable `SamplerCustomAdvanced` that every graph uses. It writes
  `progress.json` each step; when `PAUSE` exists it saves the latent and step to `ckpt/` and stops. Running the
  same job again skips finished stages (`*.done.pt`) and continues the interrupted one from its saved step
  (ancestral noise is fast-forwarded, so the result matches an uninterrupted run)
- r8: `upscale_video` takes `esrgan-anime` (RealESRGAN per frame, then lanczos; the anime path to 4K).
- `tts/`: the IndexTTS-2.5 speech worker (r7). Upstream `index-tts` at `d9e41aac` (laptop-fetched tarball)
  in `/opt/indextts`, its runtime in `/opt/tts/venv` (`--system-site-packages`: it reuses the image's torch,
  but has its own transformers 4.52.1, which IndexTTS pins and ComfyUI's 5.x can't replace). Two upstream
  pieces are swapped in `worker.py`, not patched in the source: the text normaliser uses `wetext` (pynini, which
  WeTextProcessing needs, has no aarch64 wheel) and `torchaudio.load` uses soundfile (torchaudio 2.11 loads
  through TorchCodec, which isn't installed). The adapter starts the worker on the first speech job and stops
  it on `/v1/free`, so its memory is released like any model-family swap
- `tools/validate_graphs.py`: ComfyUI's own validator over every graph, on CPU with no weights loaded.
  Run it with `scripts/validate-comfy-graphs.sh`, which is safe while another model owns the GPU

**Changing `adapter/` or `extra_model_paths.yaml` means a new image:** bump the `-rN` suffix in
`scripts/build-images.sh` and `recipes/comfyui/comfy.yaml`, since the build skips tags that already exist.

GB10 flags (see docs/lessons.md): `--disable-pinned-memory --reserve-vram 4`. Never `--highvram`/`--gpu-only`.
Inputs, outputs and checkpoints live in the job folder that core mounts at `/jobs`; core owns their lifetime.
