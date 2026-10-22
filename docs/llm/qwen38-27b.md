# Qwen3.8-27B NVFP4 (dense) — the coding / agent model

**Status 2026-10-22: STOPPED**, because Flash-Next holds the memory. Weights:
`~/models/qwen38-27b-nvfp4` (21 GB, modelopt NVFP4, arch `Qwen3_5ForConditionalGeneration`, multimodal).

There are **three** ways to run it on the box. They all bind the same ports as something else, so only
one can be up at a time.

## A. vLLM venv, port 8000 — the plain path

- Script: [spark/vllm-env/serve-qwen38-27b.sh](../../spark/vllm-env/serve-qwen38-27b.sh). It uses venv
  `~/vllm-env` (vLLM **0.26.0**, torch 2.11 cu130).
- Served as `qwen38-27b`, `--tool-call-parser qwen3_xml --reasoning-parser qwen3`, prefix caching on,
  **MTP** speculative decoding with `SPEC_TOKENS=3` (the checkpoint's own `model_mtp.safetensors`).
- No API key on this path unless the caller passes `--api-key`.
- It can be started by two different units, and both use port 8000:

| unit | memory | seqs | ctx | coexists with ComfyUI? | enabled at boot |
|---|---|---|---|---|---|
| `vllm-122b.service` (**misnamed**: a drop-in `serve-qwen38-27b.conf` swaps its ExecStart to the 27B) | 0.45 | 1 | 262K | **no**: the drop-in `90-hybrid-qwen-flux.conf` tries `Conflicts=` to clear it, but systemd can't reset dependencies from a drop-in, and `systemctl show` still lists `Conflicts=comfyui.service` | no |
| `qwen38-27b-hybrid.service` (adds the API key from `~/.config/qwen38-27b-hybrid/env`) | 0.45 | 1 | 262K | yes | **yes** ⚠️ |

The "hybrid" mode is the **Qwen 27B + ComfyUI coexistence experiment**: Qwen takes about 55 GiB and
ComfyUI starts once 40 GiB is free. It's managed by
[spark/bin/hybrid-qwen-flux.sh](../../spark/bin/hybrid-qwen-flux.sh) (`--status | --apply | --revert`).
That script's own note says it "has not been load-tested" and you should "not run concurrent subagents
on Qwen in this mode".

Hermes and Codex were wired to this path. `~/bin/switch-model.sh qwen|deepseek` starts `vllm-122b`
(that is, the 27B) or a DeepSeek-V4-Flash `ds4-server` on :8000, then rewrites the Hermes and Codex
model names.

## B. SGLang + DFlash2, port 30000 — the upstream recipe's 27B target

- **System** unit `/etc/systemd/system/qwen38-sglang.service` (**disabled**). Source:
  [spark/systemd/system/qwen38-sglang.service](../../spark/systemd/system/qwen38-sglang.service).
- Image `qwen38-dflash2:v1.2.2`: SGLang nightly `c4271c3f` (2026-08-14) plus DFlash2 speculative
  files, built by `~/dgx-spark-qwen38` (`dflash2/`).
- Draft model `~/models/qwen38-27b-dflash2` (3.6 GB), 8 draft tokens, `--mem-fraction-static 0.50`,
  torch.compile, `--sleep-on-idle`, `--tool-call-parser qwen3_coder`, OpenAI + Anthropic API, API key.
- Repo benchmark (`~/dgx-spark-qwen38/BENCHMARKS.md`): about 50 tok/s greedy median on code and
  structured output, about 2–3× slower on free prose.
- Switch to it with the recipe's script: `~/dgx-spark-qwen38/switch-model.sh stock`. It swaps which unit
  owns :30000 and edits the boot targets.
  ⚠️ That script manages the **system** units, while today's Flash runs as a **user** unit. Read it before
  you run it ([findings.md](../findings.md) #3).

## Leftover serve scripts (no weights on disk any more)

`~/vllm-env/serve-qwen35-122b.sh`, `serve-qwen35-35b.sh` and `serve-nemotron.sh` are mirrored for history.
`~/models` no longer holds Qwen3.5-122B, Qwen3.6-27B or MiniMax-M2.7.
`nemotron-3.5-lightning-nvfp4` (21 GB) **is** still there.
