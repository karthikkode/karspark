# Memory and switching between models

The GB10 has **121 GiB of unified memory**. CPU and GPU draw from the same pool, so there is no separate
VRAM. Every model server, ComfyUI, the TTS engines and the desktop all compete for it.

## Who needs how much

| tenant | footprint | fits alongside |
|---|---|---|
| Flash-Next (`qwen38-flash`) | ~104 GiB (0.85 util, 110g docker cap) | nothing big |
| 27B abliterated BF16 (`qwen38-27b-abliterated`) | 0.80 util ≈ 97 GiB, and it waits for 104 free | nothing big |
| 27B NVFP4, hybrid mode (`qwen38-27b-hybrid`) | 0.45 util ≈ 55 GiB | ComfyUI (waits for 40 free) |
| SGLang 27B (`qwen38-sglang`) | `--mem-fraction-static 0.50`, 100g cap | maybe ComfyUI, untested |
| ComfyUI + FLUX.2 | 20–40 GiB working, grows if you swap models without restarting | 27B hybrid only |
| IndexTTS-2 | tens of GiB during render | nothing big (render_chapter stops the LLM first) |

## The guardrails that exist

1. **`Conflicts=` in the unit files.** Starting one unit stops the units it lists. Current graph (from
   `systemctl --user show -p Conflicts,ConflictedBy`):
   - `qwen38-flash` ⟷ 27B-hybrid, 27B-abliterated, vllm-122b, comfyui
   - `qwen38-27b-abliterated` ⟷ everything
   - `vllm-122b` ⟷ comfyui (still, despite the drop-in that tries to clear it)
   - `qwen38-27b-hybrid` ⟷ flash, abliterated (and **not** comfyui, on purpose)
2. **`gpu_residency.sh <GiB> [units…]`** ([spark/bin/gpu_residency.sh](../spark/bin/gpu_residency.sh)),
   run as `ExecStartPre`. It stops the listed units, then **polls MemAvailable** until the memory is back.
   systemd's stop returns before the kernel has reclaimed tens of GB. Without the wait, vLLM measures
   free memory too early and refuses to start ("86.46/121.69 GiB free" on 2026-08-01).
3. **Docker memory caps** (`--memory 110g` on Flash, `100g` on SGLang) keep a runaway container from
   taking the desktop down with it.

## Switching recipes (user units, the way the box runs today)

Flash → 27B coding model, coexisting with ComfyUI:

```bash
ssh spark 'systemctl --user start qwen38-27b-hybrid && systemctl --user start comfyui'
```

Back to Flash (its `Conflicts=` stops the other two):

```bash
ssh spark 'systemctl --user start qwen38-flash'
```

Personal chat (evicts everything):

```bash
ssh spark 'systemctl --user start qwen38-27b-abliterated'
```

Starting a unit doesn't change what comes up at boot. Today **three conflicting units are enabled**
(`qwen38-flash`, `qwen38-27b-hybrid`, `comfyui`), so the boot winner is whichever systemd settles on.
Flash won on 2026-10-12. See [findings.md](findings.md) #2.

Clients that need re-pointing after a switch: the port and model id change (Flash `:30000`
`qwen3.8-flash-next`, 27B `:8000` `qwen38-27b`, abliterated `:8002` `qwen38-27b-abliterated`).

## Three switch scripts, each covering different units

| script | handles | notes |
|---|---|---|
| `~/dgx-spark-qwen38/switch-model.sh flash\|stock\|uncensored` | **system** units `qwen38-flash` / `qwen38-sglang` on :30000 | the upstream recipe; it doesn't know about the user units in use today |
| `~/bin/switch-model.sh qwen\|deepseek` | user `vllm-122b` (27B) or `ds4-server` on :8000, plus Hermes/Codex config | written for the old Hermes setup |
| `~/flash-recipes/switch-flash.sh hybrid\|baseline` | which launch script `qwen38-flash` uses | the FP8-hybrid experiment; baseline is live |

Recommendation: replace these with one `karspark` switch script built around the user units
([findings.md](findings.md) #3).
