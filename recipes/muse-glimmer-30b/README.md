# muse-glimmer-30b (planner challenger, lost the bake-off; in no mode)

| | |
|---|---|
| Purpose | the bake-off challenger to `qwen3.8-27b` for the always-on LLM in mode `creative`: story planning, FLUX/LTX prompts, long-form writing, driving the karspark MCP tools. Only one planner fits next to ComfyUI, so the bake-off winner goes into `creative`; the other stays an unused recipe |
| Weights | `nvidia/Muse-Glimmer-30B-NVFP4` @ `47818374517751c48c55cde2621594926b1888b6` → `~/models/llm/muse-glimmer-30b-nvfp4` (24 GB: NVFP4 MLP, FP8 attention, BF16 vision encoder) |
| Drafter | `meta-models/Muse-Glimmer-30B-assistant` @ `c676cce91658f9568aaf264a416211e2782735b7` → `~/models/llm/muse-glimmer-30b-assistant` (4.8 GB BF16; DFlash, 5 layers, 16-token blocks) |
| Fetch | `scripts/fetch-model.sh nvidia/Muse-Glimmer-30B-NVFP4 47818374517751c48c55cde2621594926b1888b6 llm/muse-glimmer-30b-nvfp4` and `scripts/fetch-model.sh meta-models/Muse-Glimmer-30B-assistant c676cce91658f9568aaf264a416211e2782735b7 llm/muse-glimmer-30b-assistant` |
| Why this model | best open model under 40B on EQ-Bench Creative Writing v3 (1798 vs Qwen3.8-27B 1671); dense 29.6B with vision; 131K context; 2 KV heads and sliding-window attention on 3 of every 4 layers, so the KV cache is tiny. Weaker than Qwen 27B on agentic benchmarks (its own card says so), which is why it is the challenger and not the default |

## Recipes

| id | engine | memory | notes |
|---|---|---|---|
| `muse-glimmer-30b:vllm-dflash` (model `muse-glimmer-30b`, port 10031, **no mode yet**) | [vllm-salient](../../engines/vllm-salient/README.md) image (vLLM 0.32.0rc1 nightly) | 0.30 util ≈ 36 GiB, 44g cap; creative with it = 111 GiB | **benched, in no mode**: DFlash drafter with 15 speculative tokens, FP8 KV, prefix caching, chunked prefill, `muse_glimmer` tool and reasoning parsers, 128K ctx, 4 sequences |

Checked in the image without a GPU (2026-10-23): `MuseGlimmerForConditionalGeneration` and the assistant
(`MuseGlimmerAssistantModel` → `DFlashQwen3ForCausalLM`) are registered, both `muse_glimmer` parsers exist,
and `method: dflash` is supported.

Left out of the model card's command: `--trust-remote-code` (the architecture is built into vLLM and the
weights ship no code) and `--mamba-cache-mode align` (deprecated in this vLLM; Glimmer has no Mamba layers).
The SGLang card command quantizes the drafter to FP8; try `"quantization": "fp8"` in the speculative config
if memory is tight.

**Sampling.** The card's quality settings are `temperature=1.0`, `top_p=0.95`, `top_k=64`, thinking on, high
reasoning effort. vLLM applies the shipped `generation_config.json` by default.

## Bake-off (2026-10-23, `bench/2026-10-23-bakeoff/`)

Ran in `creative` next to an idle ComfyUI. Load 273 s, 30.5 GiB actual, 393K tokens of KV.

| | Glimmer | Qwen3.8-27B |
|---|---|---|
| tool steps (the planner's main job) | **12/18**: in both runs it made a second keyframe instead of animating the first (`tools-character-video` step 2) | **18/18** |
| other checks | 20/20 | 14/18 (thinking ate the whole budget on longform; fixed in core r8) |
| FLUX/LTX prompts from beats | 65–75 s, 42–46 tok/s | 127–200 s |
| 30 s shot plan | 57–59 s | 65 s |
| long-form chapter, full reasoning | 1353–1820 words in 155–210 s | 0 words (all reasoning) |
| long-form, low reasoning | 1628–2013 words, 17.5 tok/s | 1429–1592 words, 18.5 tok/s (thinking off) |
| median tok/s | 29.1 | 23.8 |

Glimmer writes better prose (specific and grounded, fewer clichés) and is faster. It lost because a planner
that picks the wrong tool wastes GPU jobs, and its miss was the same in both runs. The `busy` variant (next to a
running video job) was skipped: the box had powered off under sustained load earlier that day.
Reconsider it if a later Glimmer release, or a stricter system prompt, fixes tool choice.

## Bring it back
Put it in `creative` instead of `qwen3.8-27b:vllm` (`config/modes.yaml`), run `scripts/deploy.sh`, switch
to creative, then run `scripts/bakeoff.sh muse-glimmer-30b <label>`.
