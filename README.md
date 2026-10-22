# karspark

Operations repo for our self-hosted **DGX Spark** (`edgexpert-bd12`) and the AI models it serves:
local LLMs (Qwen 3.8 family), TTS (IndexTTS-2 and friends) and image generation (ComfyUI + FLUX.2 / Qwen-Image).

The Spark itself is the source of truth. This repo records **what is deployed, why, and how to operate it**,
so nothing on the box is a mystery again. `spark/` is a byte-for-byte mirror of its serving config, refreshed
by `scripts/pull-config.sh`; `git diff` after a pull shows exactly what changed on the box.

> Audited 2026-10-22. Run `scripts/status.sh` for the live picture.

## What is running right now

| | |
|---|---|
| **Serving** | **Qwen3.8-Flash-Next** (176B MoE, ~6B active, NVFP4) — vLLM in Docker, `qwen38-flash:v1.4-prefixfix` |
| **Endpoint** | `http://100.101.249.74:30000/v1` (OpenAI + Anthropic `/v1/messages`), model id `qwen3.8-flash-next`, 262K ctx, Bearer key in `~/.config/qwen38/api-key` on the Spark |
| **Agent endpoint** | `:30001` — keepalive proxy in front of `:30000` (use this for long-running agent/tool-call streams) |
| **Managed by** | user unit `qwen38-flash.service` (enabled) → `~/.config/qwen38/launch-flash.sh` |
| **Memory** | 109 / 121 GiB used (the Flash container owns the box) |
| **Up since** | 2026-10-12 (last reboot), 0 container restarts |
| **Stopped** | 27B NVFP4, 27B abliterated, ComfyUI, TTS — they can't share memory with Flash |

## The model lineup

| Model | Engine | Port | Unit | Doc |
|---|---|---|---|---|
| Qwen3.8-Flash-Next NVFP4 + MTP | vLLM (Docker) | 30000 (+30001 proxy) | `qwen38-flash` (user) | [docs/llm/qwen38-flash-next.md](docs/llm/qwen38-flash-next.md) |
| Qwen3.8-27B NVFP4 + MTP | vLLM (venv) | 8000 | `vllm-122b` (misnamed) or `qwen38-27b-hybrid` | [docs/llm/qwen38-27b.md](docs/llm/qwen38-27b.md) |
| Qwen3.8-27B NVFP4 + DFlash2 | SGLang (Docker) | 30000 | `qwen38-sglang` (system, disabled) | [docs/llm/qwen38-27b.md](docs/llm/qwen38-27b.md) |
| Qwen3.8-27B **abliterated** (BF16) | vLLM (venv) | 8002 | `qwen38-27b-abliterated` | [docs/llm/qwen38-27b-abliterated.md](docs/llm/qwen38-27b-abliterated.md) |
| ComfyUI (FLUX.2 dev NVFP4, Qwen-Image 2.1) | ComfyUI (venv) | 8188 (localhost) | `comfyui` | [docs/media/comfyui.md](docs/media/comfyui.md) |
| IndexTTS-2 / VoxCPM2 / others | scripts, no service | — | — | [docs/media/tts.md](docs/media/tts.md) |

**Golden rule:** 121 GiB of *unified* memory (CPU and GPU share it). Only one big tenant fits at a time.
Units carry `Conflicts=` and `gpu_residency.sh` waits for memory to really come back before a new one starts.
See [docs/memory-and-switching.md](docs/memory-and-switching.md).

## Repo layout

```
README.md                  this page: current state + lineup
CLAUDE.md                  rules for agents working on the Spark
docs/inventory.md          hardware, network, every service/port/model/dir on the box
docs/memory-and-switching.md  how models evict each other; how to switch safely
docs/llm/*.md              one page per LLM: recipe, provenance, flags, benchmarks
docs/media/*.md            ComfyUI and TTS
docs/findings.md           problems found in the audit, with suggested fixes
patches/                   local patches baked into Docker images (not in upstream recipes)
spark/                     MIRROR of the Spark's config (systemd units, launch scripts, templates). Do not edit here.
scripts/status.sh          read-only live status
scripts/pull-config.sh     refresh spark/ from the box (secrets excluded)
```

## Everyday commands

```bash
scripts/status.sh
```

```bash
scripts/pull-config.sh && git diff --stat
```

Talking to the model from the laptop (key never leaves the Spark):

```bash
ssh spark 'curl -s localhost:30000/v1/models -H "Authorization: Bearer $(cat ~/.config/qwen38/api-key)"'
```

Open issues from the audit are in [docs/findings.md](docs/findings.md) — read that first.
