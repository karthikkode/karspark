# Engine: comfyui

Image `karspark/comfyui:0.42.4`, built by `scripts/build-images.sh` from the [Dockerfile](Dockerfile):

- `python:3.12-slim` (pinned digest) + torch **2.13.0+cu130**, torchvision 0.28.0, torchaudio 2.11.0,
  comfy-kitchen 0.2.39 (built 2026-10-22, 6.9 GB; full set in `/opt/pip-freeze.txt` inside the image)
- ComfyUI **v0.42.4** (`3f438ca6`), fetched on the laptop as a commit tarball (GitHub crawls from the Spark)
  and checked to be that commit
- `extra_model_paths.yaml`: maps ComfyUI's model folders to `~/models` (read-only)
- `adapter/`: our process in front of ComfyUI. It provides OpenAI-style `/v1/images/generations`
  (FLUX.2) and `/v1/videos/generations` (LTX-2.5), and proxies everything else (UI, `/prompt`, `/ws`)
  so the gateway's `/comfyui/` serves the real app

GB10 flags (see docs/lessons.md): `--disable-pinned-memory --reserve-vram 4`. Never `--highvram`/`--gpu-only`.
Outputs go to `/tmp` inside the container and are returned in the response, never written to the host.
