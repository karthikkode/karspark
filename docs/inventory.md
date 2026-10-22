# Spark inventory (audited 2026-10-22)

## Box

| | |
|---|---|
| Hostname | `edgexpert-bd12` (DGX Spark / GB10, aarch64, headless) |
| GPU | NVIDIA GB10, driver 580.178.04, CUDA 13.0, sm_121 |
| Memory | 121 GiB unified + 15 GiB swap |
| Disk | `/dev/nvme0n1p2`, 3.7 TB: 568 GB used, 3.0 TB free |
| Network | LAN `192.168.1.20` (Wi-Fi, flaky) · Tailscale `100.101.249.74` |
| Tailnet peers | laptop `karthik` = `100.104.71.64`, `karthiks-macbook-pro` (offline 49 days) |
| SSH | from the laptop: `ssh spark` (Tailscale) / `ssh spark-lan`. Keyless; **sudo needs a password** |
| Linger | on, so user units run without a login |
| Docker | 29.x, NVIDIA runtime |

## Listening ports

| port | bind | what | auth |
|---|---|---|---|
| 22 | 0.0.0.0 | sshd | key |
| **30000** | 0.0.0.0 (docker-published) | Flash-Next vLLM (or SGLang 27B when switched) | Bearer key |
| **30001** | 0.0.0.0 | keepalive proxy → :30000 (system unit `qwen38-keepalive`) | passes the key through |
| 8000 | 0.0.0.0 | 27B vLLM (when running) | key only via the hybrid unit |
| 8002 | 0.0.0.0 | 27B abliterated (when running) | **none** |
| 8188 | 127.0.0.1 | ComfyUI (when running) | none (localhost) |
| 11000 | 127.0.0.1 | unknown local service, not part of the model stack | |

## systemd units

User units (`~/.config/systemd/user`, mirrored in [spark/systemd/user](../spark/systemd/user)):

| unit | enabled | state | what |
|---|---|---|---|
| `qwen38-flash` | ✅ | **running** | Flash-Next Docker |
| `qwen38-27b-hybrid` | ✅ | stopped | 27B NVFP4 vLLM :8000, coexists with ComfyUI |
| `comfyui` | ✅ | stopped | ComfyUI :8188 |
| `hermes-gateway` | ✅ | running | Hermes messaging gateway. 0 cron jobs; config still points at `qwen35-122b` on :8000 |
| `vllm-122b` | — | stopped | **actually the 27B NVFP4** (drop-in), :8000 |
| `qwen38-27b-abliterated` | — | stopped | abliterated BF16 :8002 |

System units (`/etc/systemd/system`, from the upstream recipe, mirrored in [spark/systemd/system](../spark/systemd/system)):

| unit | enabled | state |
|---|---|---|
| `qwen38-keepalive` | ✅ | **running** (:30001) |
| `qwen38-flash` | — | stopped (duplicate of the user unit) |
| `qwen38-sglang` | — | stopped |

## Docker images

| image | size | use |
|---|---|---|
| `qwen38-flash:v1.4-prefixfix` | 21 GB | **live** Flash image (`docker commit` of v1.4 + 2 vLLM patches) |
| `qwen38-flash:v1.4` | 20.6 GB | upstream recipe build (PLE-mmap overlay on the official image) |
| `qwen38-flash-dgx:latest` | 20.6 GB | earlier build from `~/qwen3.8-Flash-DGX` (blazux), superseded |
| `qwen38-dflash2:v1.2.2` | 38.6 GB | SGLang + DFlash2 for the 27B |

## Models (`~/models`, 399 GB)

| dir | size | used by |
|---|---|---|
| `qwen38-flash-next-nvfp4` | 126 GB | **live** Flash |
| `qwen38-flash-next-nvfp4-fp8hybrid` | 139 GB | FP8-hybrid experiment (reverted), **unused** |
| `qwen38-27b-nvfp4` | 21 GB | 27B (vLLM + SGLang) |
| `qwen38-27b-dflash2` | 3.6 GB | SGLang draft model |
| `qwen38-27b-abliterated` | 52 GB | abliterated BF16 |
| `nemotron-3.5-lightning-nvfp4` | 21 GB | old "fast" model; serve script exists, no unit |
| `comfyui/` | 68 GB | ComfyUI model tree (some files are symlinks into the dirs below and the HF cache) |
| `flux2-dev-nvfp4-hf`, `flux2-dev-comfy-org` | 41 + 15 GB | FLUX.2 |
| `indextts-2` | 11 GB | IndexTTS-2 |
| `voxcpm2`, `qwen3-tts-voice-design`, `maya1`, `snac-24khz`, `moss-*` | 4–7 GB each | TTS / voice design |
| `faster-whisper-medium`, `openai-whisper-medium` | 1.5 / 2.9 GB | STT |
| `clip-vit-base-patch32`, `detr-resnet-50`, `yolos-small`, `ultrasharp` | small | vision helpers |

`~/.cache/huggingface` is now only 19 MB: the old "195 GB hub library" was moved into `~/models`.
MiniMax-M2.7, Qwen3.5-122B and Qwen3.6-27B are **no longer on disk**.

## Home directory map (what matters)

| path | what |
|---|---|
| `~/dgx-spark-qwen38` | upstream recipe (hasso5703) v1.4: install/switch scripts, Flash overlay, DFlash2, benchmarks |
| `~/flash-recipes/` | 3 alternative Flash recipes + "sharp" template + `switch-flash.sh` |
| `~/flash-exp/` | Flash tuning sweep: probes + `ledger.jsonl` |
| `~/qwen3.8-Flash-DGX` | blazux's original PLE-mmap recipe (built `qwen38-flash-dgx:latest`) |
| `~/.config/qwen38/` | **live** Flash launch script, chat templates, keepalive proxy, `api-key` (secret) |
| `~/.config/qwen38-27b-{hybrid,abliterated}/` | 27B runners; `hybrid/env` holds the API key (secret) |
| `~/vllm-env/` | vLLM 0.26.0 venv + serve scripts |
| `~/bin/` | `switch-model.sh`, `ds4_serve.sh`, `hermes-health` |
| `~/ComfyUI`, `~/flux2` | ComfyUI + its venv/scripts |
| `~/index-tts`, `~/voxcpm-venv`, `~/qwentts-venv`, `~/maya-venv`, `~/moss-vg`, `~/fish-speech` | TTS engines |
| `~/kathawin-work/` | Kathawin sound/video pipeline scratch root (`bin/`, `production/`, `voicelib/`) |
| `~/.hermes/` | Hermes agent + gateway (Spark side) |
| loose `~/*.py`, `~/*.sh`, `~/ltest`, `~/stt` | one-off experiments from July–August (animate/decompose/layer_split, dl scripts, sweeps) |
