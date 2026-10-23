# muse-glimmer-30b (creative-mode planner, challenger)

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
| `muse-glimmer-30b:vllm-dflash` (model `muse-glimmer-30b`, port 10031, **no mode yet**) | [vllm-salient](../../engines/vllm-salient/README.md) image (vLLM 0.32.0rc1 nightly) | 0.30 util ≈ 36 GiB, 44g cap; creative with it = 111 GiB | **draft**: DFlash drafter with 15 speculative tokens, FP8 KV, prefix caching, chunked prefill, `muse_glimmer` tool and reasoning parsers, 128K ctx, 4 sequences |

Checked in the image without a GPU (2026-10-23): `MuseGlimmerForConditionalGeneration` and the assistant
(`MuseGlimmerAssistantModel` → `DFlashQwen3ForCausalLM`) are registered, both `muse_glimmer` parsers exist,
and `method: dflash` is supported.

Left out of the model card's command: `--trust-remote-code` (the architecture is built into vLLM and the
weights ship no code) and `--mamba-cache-mode align` (deprecated in this vLLM; Glimmer has no Mamba layers).
The SGLang card command quantizes the drafter to FP8; try `"quantization": "fp8"` in the speculative config
if memory is tight.

**Sampling.** The card's quality settings are `temperature=1.0`, `top_p=0.95`, `top_k=64`, thinking on, high
reasoning effort. vLLM applies the shipped `generation_config.json` by default.

## Phase 0 (needs the GPU, so it waits for the owner's go)

1. Put it in `creative` instead of `qwen3.8-27b:vllm` (`config/modes.yaml`), `scripts/deploy.sh`, and switch
   to creative with the owner key.
2. Measure load time, memory (`/v1/status`) and tok/s, then `scripts/bakeoff.sh muse-glimmer-30b`, once
   with ComfyUI idle and once while a video job runs.
3. Compare with the `qwen3.8-27b` results (see `bench/planner/README.md`), keep the winner in `creative`,
   and record the numbers here and in the registry.
