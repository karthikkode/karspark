# Registry: what is on the Spark

One endpoint: **`http://100.101.249.74:8080`** (Tailscale). Text (OpenAI `/v1/...`, Anthropic `/v1/messages`),
jobs, files, `/mcp`, `/ui`. Auth: a project key (`Authorization: Bearer <key>`).

**State on 2026-10-23:**
- **Live on the box:** the pre-core llama-swap gateway, serving Flash-Next.
- **The repo:** karspark-core. It is built (`karspark/core:0.1.0-r2`) and tested (37 tests, fake engines),
  but not yet deployed.
- **The cutover:** `scripts/deploy.sh --cutover`. It stops the gateway, and Flash-Next reloads under core
  (~6 min). It waits for the owner's go.

## Service

| what | image | where | status |
|---|---|---|---|
| karspark-core | `karspark/core:0.1.0-r2` | `core/`, unit `core/karspark-core.service`, `--network host`, `:8080` | built, tested, **not deployed** |
| llama-swap gateway (pre-core) | `karspark/gateway:265` | removed from the repo; still running on the box until cutover | retiring |

## Modes (`config/modes.yaml`)

| mode | engines | memory | text model | notes |
|---|---|---|---|---|
| `coding` (default) | `qwen3.8-flash:vllm-salient` | 100 GiB | `qwen3.8-flash` | P0, owner keys; holds 30 min after the last coding request |
| `creative` | `qwen3.8-27b:vllm` + `comfyui:comfy` | 107 GiB | `qwen3.8-27b` | planner LLM next to every media model. Not run on the GPU yet (phase 0) |
| `test` | `qwen3-0.6b:vllm` | 12 GiB | `qwen3-0.6b` | integration tests on the box |

## Recipes

| recipe id | models | port | weights | engine | memory | load time | status |
|---|---|---|---|---|---|---|---|
| `qwen3.8-flash:vllm-salient` | `qwen3.8-flash` | 10011 | `nvidia/Qwen3.8-Flash-Next-NVFP4@fc694b54` | vllm-salient 2026-10-14-77154ca6 | 73.5 GiB weights + 13.6 GiB KV | 360 s cold | **deployed** (under the gateway): 70.6 tok/s 1-stream, 100 tok/s total @8 |
| `qwen3.8-27b:vllm` | `qwen3.8-27b` | 10030 | `unsloth/Qwen3.8-27B-NVFP4@f0b7c9e7` (downloading) | vllm-salient (to confirm) | 0.25 util ≈ 30 GiB | – | **draft**; bake-off vs Muse-Glimmer-30B in phase 0 |
| `comfyui:comfy` | `flux2-dev`, `qwen-image-layered`, `ltx-2.5`, `seedvr2-7b`, `seedvr2-7b-sharp`, `seedvr2-3b`, `flux2-refine`, `esrgan-anime` | 10020 | below | `karspark/comfyui:0.42.4-r4` | ≤ 75 GiB (one model family at a time) | – | ready; 14/14 graphs pass ComfyUI's validator; not run on the GPU |
| `qwen3-0.6b:vllm` | `qwen3-0.6b` | 10002 | `Qwen/Qwen3-0.6B@c1899de2` | vLLM 0.32.0 | ~13 GiB (0.08 util) | ~112 s cold | deployed 2026-10-22 (test) |

## Weights on disk

| weights | dir | size | for |
|---|---|---|---|
| `nvidia/Qwen3.8-Flash-Next-NVFP4@fc694b54` | `~/models/llm/qwen3.8-flash-next-nvfp4` | 132.7 GB | `qwen3.8-flash:vllm-salient` |
| `Qwen/Qwen3-0.6B@c1899de2` | `~/models/llm/qwen3-0.6b` | 1.5 GB | `qwen3-0.6b:vllm` |
| `unsloth/Qwen3.8-27B-NVFP4@f0b7c9e7` | `~/models/llm/qwen3.8-27b-nvfp4` | 23.4 GB | `qwen3.8-27b:vllm` (downloading 2026-10-23) |
| `nvidia/Muse-Glimmer-30B-NVFP4@47818374` + `meta-models/Muse-Glimmer-30B-assistant@c676cce9` | `~/models/llm/muse-glimmer-30b-{nvfp4,assistant}` | 24.7 + 5.1 GB | planner challenger (queued downloads) |
| `black-forest-labs/FLUX.2-dev-NVFP4@142b87e7` (pure NVFP4) | `~/models/image/flux2-dev-nvfp4` | 21.0 GB | `flux2-dev` |
| `Comfy-Org/flux2-dev@ed33133c` (Mistral FP4 TE, Turbo LoRA v2, VAE) | `~/models/image/flux2-dev-comfy` | 15.4 GB | `flux2-dev`, `flux2-refine` |
| `Lightricks/LTX-2.5@49b093a3` (distilled int8-convrot, Gemma-4 12B int8 TE, VAEs, 2× upscaler) | `~/models/video/ltx-2.5` | 39.7 GB | `ltx-2.5` |
| `Comfy-Org/Qwen-Image-Layered_ComfyUI@02130716` (fp8mixed transformer, layered VAE) | `~/models/image/qwen-image-layered-comfy` | 20.8 GB | `qwen-image-layered` |
| `Comfy-Org/Qwen-Image_ComfyUI@1f12b17b` (Qwen2.5-VL 7B fp8_scaled TE only) | `~/models/image/qwen-image-te` | 9.4 GB | `qwen-image-layered` |
| `Comfy-Org/SeedVR2@df488797` (7B fp16, 7B sharp int8, 3B fp16, VAE) | `~/models/image/seedvr2` | 32.1 GB | `seedvr2-*` |
| RealESRGAN x4plus anime 6B (GitHub v0.2.2.4 → safetensors, `scripts/fetch-pth.sh`) | `~/models/image/upscale-gan` | 18 MB | `esrgan-anime`, `flux2-refine` |

## Images

Everything runs in Docker. Nothing is installed on the host except Docker itself.

| image | what | built from |
|---|---|---|
| `karspark/core:0.1.0-r2` | karspark-core (FastAPI, scheduler, MCP) + docker CLI | `core/Dockerfile` |
| `karspark/comfyui:0.42.4-r4` | ComfyUI + torch 2.13 cu130 + karspark job adapter + pausable sampler nodes | `engines/comfyui/Dockerfile` |
| `karspark/vllm-salient:2026-10-14-77154ca6` | vLLM nightly + 14 Flash-Next patches | `engines/vllm-salient/upstream/Dockerfile` |
| `vllm/vllm-openai@sha256:6ff5ff0d…` (v0.32.0) | stock vLLM | Docker Hub, pinned digest |
| `karspark/hf-cli:2.2.0-xet1.7.0` | model downloads | `engines/hf-cli/Dockerfile` |
| `karspark/bench:1` | benchmark harness | `bench/Dockerfile` |
| `karspark/gateway:265` | llama-swap (pre-core) | removed from the repo; delete the image after cutover |

## Ports

| port | bind | what |
|---|---|---|
| 22 | all | sshd |
| **8080** | all | **karspark-core** (the gateway until cutover), the only user-facing port |
| 10001–10999 | 127.0.0.1 | engine containers, a fixed port per recipe (see Recipes) |

## Box baseline (after wipe, 2026-10-22)

DGX OS 7.6.0 (Ubuntu 24.04 base), NVIDIA driver 580.178.04, CUDA 13.0, Docker + NVIDIA runtime, Tailscale.
LAN IP `192.168.29.130` (DHCP, Wi-Fi). Kept in `~`: `.ssh`, default dotfiles, `.local/share/keyrings`.
