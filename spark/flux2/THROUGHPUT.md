# FLUX.2 throughput on the DGX Spark — measured levers

Measured 2026-07-30 on GB10 (121 GB unified), ComfyUI 0.28.0, pure NVFP4 + Turbo LoRA.
Every number here came off this machine; nothing is inferred from model cards.

**Workload this is tuned for:** one long video per day for kathawin.com. ~85-130 images
per story, mostly 2.36 MP (2048x1152 scenes / 1152x2048 shorts) with 1-2 character and
location references attached for consistency.

## Current defaults (after this pass)

| Setting | Value | Where |
|---|---|---|
| Transformer | `flux2-dev-nvfp4.safetensors` (pure, 19.59 GB) | `generate.py` / `edit.py` |
| LoRA | `Flux2TurboComfyv2` @ 1.0 | default, `--base` opts out |
| Steps | **6** | was 8 |
| Guidance | 2.5 | |
| Reference size | **0.25 MP** | was 1.0 MP |

Net effect on the batch workload: a 2-reference 2.36 MP job went **116 s -> 48 s (2.4x)**.
A 126-job story goes from ~4.1 h to ~1.7 h.

## The two levers that mattered

### 1. Reference resolution — the big one

Each reference is concatenated into the attention sequence, so its pixel count is paid on
**every denoising step**. At 2048x1152 with 2 refs, Turbo 8 steps:

| ref size | time |
|---|---|
| no refs | 52.1 s |
| **0.25 MP** | **64.1 s** |
| 0.50 MP | 80.1 s |
| 1.00 MP (old default) | 116.2 s |

Native-resolution crops at 0.25 vs 1.00 MP showed identity, wardrobe, location and
lighting equally preserved; only composition jitters, because the sampling trajectory
shifts. `kathawin_tools/video/bin/flux_batch.py` reached the same conclusion independently
on the RunPod path ("~512px carries the face identity fine" — 512x512 is ~0.26 MP).

Reference count is also superlinear — 44 / 72 / 106 / 176 s for 0 / 1 / 2 / 3 refs at
1.0 MP. Attach only the references a shot actually needs.

### 2. Step count

Turbo is distilled for 8 steps, but that is a quality recommendation, not a floor.
2048x1152, 2 refs @ 0.25 MP, warm:

| steps | time | vs 8 |
|---|---|---|
| 8 | 62.1 s | 100% |
| **6** | **48.0 s** | 77% |
| 5 | 40.0 s | 64% |
| 4 | 32.0 s | 52% |

Checked at native resolution on three scene types — a rooftop wide shot, a tear-filled eye
close-up, and a dark kitchen interior. 6 was indistinguishable from 8 in all three,
including the eye close-up, which is where a starved sampler shows first. 4 visibly softens
backgrounds and flattens sky gradients. 6 keeps a margin; use `--steps 8` for thumbnails
and hero frames where 14 extra seconds does not matter.

## Tried and rejected — do not re-run these

| Lever | Result |
|---|---|
| `flux2-dev-nvfp4-mixed.safetensors` | 9.5% **slower**, no visible quality gain. On disk, opt-in via `--unet`. |
| `torch.compile` (`--compile`) | **Fails.** Dynamo cannot trace the NVFP4 custom ops. `generate.py` now refuses the flag up front rather than failing 60 s in. |
| `--fast` | No effect. Targets fp16/BF16 paths; our matmuls are NVFP4. |
| `--highvram` | Pinned 112 GB, hung the driver, forced a reboot. |
| `--gpu-only` | No eviction, hung the machine again. |

Untried: **SageAttention** — needs compiling for sm_121a. The only remaining kernel-level
lever, and the payoff is unknown.

## Operating rules

**Restart ComfyUI after switching models or LoRAs.** It caches every checkpoint and never
evicts. Steady state is 48 s; if you see 150 s, restart. This is what caused two reboots
during the original setup.

**ComfyUI caches identical graphs.** Same prompt + same seed + same settings returns the
cached result in ~0.25 s. Useful (re-running a manifest is free for unchanged jobs), but it
will silently invalidate a benchmark — vary the seed when measuring.

**The 122B cannot coexist with FLUX.** 74 GB of weights against 33 GB of FLUX models on a
121 GB box. Stop it with `systemctl --user stop vllm-122b` — **not** `pkill -f "vllm serve"`,
which `~/vllm-env/MODELS.md` still recommends: the unit has `Restart=on-failure`, so a
signal-kill just makes systemd restart it 15 s later. `serve-qwen35-35b.sh` (35 GB, util
0.60) is documented as coexisting if you need an LLM up while rendering.

**Client overhead is ~1.8 s per job** (poll interval + process start): 46.5 s server-side
vs 48.0 s wall. A batch backend that queues jobs into ComfyUI rather than spawning one
process per image saves ~4% on a full story.

## Test scripts

`multiref_test.py` (reference-count sweep), `refmp_test.py` (reference-resolution sweep),
`steps_test.py` (step sweep), `steps_faces.py` (8 vs 6 on close-ups). All take reference
basenames staged in `~/ComfyUI/input/`. Re-run any of them after a torch or comfy-kitchen
upgrade — several conclusions above are version-dependent.

Originals before this pass: `generate.py.pre-opt-20260730`, `edit.py.pre-opt-20260730`.
