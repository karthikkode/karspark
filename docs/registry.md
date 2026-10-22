# Registry: what is on the Spark

One endpoint: **`http://100.101.249.74:8080`** (Tailscale) — OpenAI-compatible (`/v1/...`) and Anthropic
`/v1/messages`. Auth: `Authorization: Bearer <key>`; the key is `KARSPARK_API_KEY` in `~/gateway/env` on
the Spark. UI: `/ui`.

## Recipes

| recipe id | alias | kind | weights | engine | memory | load time | status |
|---|---|---|---|---|---|---|---|
| `qwen3-0.6b:vllm` | `qwen3-0.6b` | llm | `Qwen/Qwen3-0.6B@c1899de2` | vLLM 0.32.0 | ~13 GiB (0.08 util) | 116 s cold (first run, incl. compile) | deployed 2026-10-22 |

## Engines on the box

| engine | version / digest | where |
|---|---|---|
| llama-swap (the gateway) | v265 | `~/engines/llama-swap/current` |
| vLLM | `vllm/vllm-openai:v0.32.0-aarch64@sha256:6ff5ff0d…` | Docker |
| hf-cli | huggingface_hub 2.2.0 | `~/engines/hf-cli` |

## Ports

| port | bind | what |
|---|---|---|
| 22 | all | sshd |
| **8080** | all | **karspark gateway**, the only user-facing port |
| 10001+ | 127.0.0.1 | backends, assigned by the gateway (`${PORT}`) |

## Box baseline (after wipe, 2026-10-22)

DGX OS 7.6.0 (Ubuntu 24.04 base), NVIDIA driver 580.178.04, CUDA 13.0, Docker + NVIDIA runtime, Tailscale.
LAN IP `192.168.29.130` (DHCP, Wi-Fi). Kept in `~`: `.ssh`, default dotfiles, `.local/share/keyrings`.
