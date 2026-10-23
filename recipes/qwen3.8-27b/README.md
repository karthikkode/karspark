# qwen3.8-27b (creative-mode planner)

| | |
|---|---|
| Purpose | the always-on LLM in mode `creative`: plans stories and shots, writes FLUX/LTX prompts, drives the karspark MCP tools, runs next to ComfyUI |
| Weights | `unsloth/Qwen3.8-27B-NVFP4` @ `f0b7c9e722f5565102fff8481c99e4d86ae099c7` → `~/models/llm/qwen3.8-27b-nvfp4` (23.4 GB) |
| Fetch | `scripts/fetch-model.sh unsloth/Qwen3.8-27B-NVFP4 f0b7c9e722f5565102fff8481c99e4d86ae099c7 llm/qwen3.8-27b-nvfp4` |
| Why this model | EQ-Bench Creative Writing v3: 1671 (Muse-Glimmer-30B 1798, gemma-4-31B 1368); Longform 53.3; stronger on agentic/tool-calling benchmarks than Glimmer, mature vLLM support, built-in MTP. Glimmer is the bake-off challenger |

## Recipes

| id | engine | memory | notes |
|---|---|---|---|
| `qwen3.8-27b:vllm` (model `qwen3.8-27b`, mode `creative`, port 10030) | [vllm-salient](../../engines/vllm-salient/README.md) image (to confirm in phase 0) | 0.25 util ≈ 30 GiB, 40g cap | **draft**: MTP 3 tokens, FP8 KV, prefix caching, `qwen3_xml` tools, `qwen3` reasoning, 128K ctx, 4 sequences |

## Phase 0 (needs the GPU, so it waits for the owner's go)

- Confirm the image serves it (or pin a stock vLLM digest), then measure load time, memory and tok/s with
  ComfyUI busy next to it. Community numbers on Spark: 22–34 tok/s single stream (NVFP4 + MTP).
- Bake off against [`muse-glimmer-30b`](../muse-glimmer-30b/README.md) (NVFP4 + DFlash drafter, downloaded) with
  `scripts/bakeoff.sh` ([bench/planner](../../bench/planner/README.md)) on our tasks: FLUX/LTX
  prompts from a story beat, story planning and long-form writing, multi-turn karspark MCP tool calls, and
  speed while ComfyUI runs. The winner gets the `creative` mode's `text` slot.
