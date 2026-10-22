# Registry: what is on the Spark

One endpoint: **`http://100.101.249.74:8080`** (Tailscale) — OpenAI-compatible (`/v1/...`) and Anthropic
`/v1/messages`. Auth: `Authorization: Bearer <key>`; the key is `KARSPARK_API_KEY` in `~/gateway/env` on
the Spark. UI: `/ui`.

## Recipes

| recipe id | alias | kind | weights | engine | memory | load time | status |
|---|---|---|---|---|---|---|---|
| `qwen3-0.6b:vllm` | `qwen3-0.6b` | llm | `Qwen/Qwen3-0.6B@c1899de2` | vLLM 0.32.0 | ~13 GiB (0.08 util) | ~112 s cold | deployed 2026-10-22 |
| `qwen3.8-flash:vllm-salient` | **`qwen3.8-flash`** (default) | llm | `nvidia/Qwen3.8-Flash-Next-NVFP4@fc694b54` | vllm-salient 2026-10-14-77154ca6 | 73.5 GiB weights + 13.6 GiB KV, runs alone | 360 s cold | **deployed 2026-10-22**: 70.6 tok/s 1-stream, 100 tok/s total @8 |

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

## Ports

| port | bind | what |
|---|---|---|
| 22 | all | sshd |
| **8080** | all | **karspark gateway**, the only user-facing port |
| 10001+ | 127.0.0.1 | backend containers, assigned by the gateway (`${PORT}`) |

## Box baseline (after wipe, 2026-10-22)

DGX OS 7.6.0 (Ubuntu 24.04 base), NVIDIA driver 580.178.04, CUDA 13.0, Docker + NVIDIA runtime, Tailscale.
LAN IP `192.168.29.130` (DHCP, Wi-Fi). Kept in `~`: `.ssh`, default dotfiles, `.local/share/keyrings`.
