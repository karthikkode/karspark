# Registry: what is on the Spark

One endpoint: **`http://100.101.249.74:8080`** (Tailscale) — OpenAI-compatible (`/v1/...`) and Anthropic
`/v1/messages`. Auth: `Authorization: Bearer <key>`; the key is `KARSPARK_API_KEY` in `~/gateway/env` on
the Spark. UI: `/ui`.

## Recipes

| recipe id | alias | kind | weights | engine | memory | load time | status |
|---|---|---|---|---|---|---|---|
| `qwen3-0.6b:vllm` | `qwen3-0.6b` | llm | `Qwen/Qwen3-0.6B@c1899de2` | vLLM 0.32.0 | ~13 GiB (0.08 util) | ~112 s cold | deployed 2026-10-22 |
| `qwen3.8-flash:vllm-salient` | **`qwen3.8-flash`** (default) | llm | `nvidia/Qwen3.8-Flash-Next-NVFP4@fc694b54` | vllm-salient 2026-10-14-77154ca6 | 73.5 GiB weights + 13.6 GiB KV, runs alone | 360 s cold | **deployed 2026-10-22**: 70.6 tok/s 1-stream, 100 tok/s total @8 |

## Downloaded, not deployed (waiting for the go-ahead; deploying reloads the gateway and unloads Flash-Next)

| weights | dir | size | for recipe |
|---|---|---|---|
| `black-forest-labs/FLUX.2-dev-NVFP4@142b87e7` (pure NVFP4) | `~/models/image/flux2-dev-nvfp4` | 21.0 GB | `comfyui_auto` (alias `flux2-dev`) |
| `Comfy-Org/flux2-dev@ed33133c` (Mistral FP4 TE, Turbo LoRA v2, VAE) | `~/models/image/flux2-dev-comfy` | 15.4 GB | same |
| `Lightricks/LTX-2.5@49b093a3` (distilled int8-convrot, Gemma-4 12B int8 TE, VAEs, 2× upscaler) | `~/models/video/ltx-2.5` | 39.7 GB | `comfyui_auto` (alias `ltx-2.5`) |
| `Comfy-Org/Qwen-Image-Layered_ComfyUI@02130716` (fp8mixed transformer, layered VAE) | `~/models/image/qwen-image-layered-comfy` | 20.8 GB | `comfyui_auto` (layer decomposition for parallax), downloading |
| `Comfy-Org/Qwen-Image_ComfyUI@1f12b17b` (Qwen2.5-VL 7B fp8_scaled TE only) | `~/models/image/qwen-image-te` | 9.4 GB | same, downloading |

Engine image `karspark/comfyui:0.42.4` is built. The recipe is in `recipes/comfyui/`. Image/video workflow
graphs (`engines/comfyui/adapter/workflows.py`) still need authoring against `/object_info`.

## Engines on the box

| engine | version / digest | where |
|---|---|---|
Everything runs in Docker. Nothing is installed on the host except Docker itself.

| image | what | built from |
|---|---|---|
| `karspark/gateway:265` | llama-swap v265 + docker CLI (the gateway) | `gateway/Dockerfile` |
| `karspark/vllm-salient:2026-10-14-77154ca6` | vLLM nightly + 14 Flash-Next patches | `engines/vllm-salient/upstream/Dockerfile` |
| `vllm/vllm-openai@sha256:6ff5ff0d…` (v0.32.0) | stock vLLM | Docker Hub, pinned digest |
| `karspark/hf-cli:2.2.0` | model downloads | `engines/hf-cli/Dockerfile` |
| `karspark/bench:1` | benchmark harness | `bench/Dockerfile` |
| `karspark/comfyui:0.42.4` | ComfyUI + torch 2.13 cu130 + OpenAI-style adapter | `engines/comfyui/Dockerfile` |

## Ports

| port | bind | what |
|---|---|---|
| 22 | all | sshd |
| **8080** | all | **karspark gateway**, the only user-facing port |
| 10001+ | 127.0.0.1 | backend containers, assigned by the gateway (`${PORT}`) |

## Box baseline (after wipe, 2026-10-22)

DGX OS 7.6.0 (Ubuntu 24.04 base), NVIDIA driver 580.178.04, CUDA 13.0, Docker + NVIDIA runtime, Tailscale.
LAN IP `192.168.29.130` (DHCP, Wi-Fi). Kept in `~`: `.ssh`, default dotfiles, `.local/share/keyrings`.
