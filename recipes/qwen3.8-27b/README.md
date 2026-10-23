# qwen3.8-27b (creative-mode planner)

| | |
|---|---|
| Purpose | the always-on LLM in mode `creative`: plans stories and shots, writes FLUX/LTX prompts, drives the karspark MCP tools, runs next to ComfyUI |
| Weights | `unsloth/Qwen3.8-27B-NVFP4` @ `f0b7c9e722f5565102fff8481c99e4d86ae099c7` → `~/models/llm/qwen3.8-27b-nvfp4` (23.4 GB) |
| Fetch | `scripts/fetch-model.sh unsloth/Qwen3.8-27B-NVFP4 f0b7c9e722f5565102fff8481c99e4d86ae099c7 llm/qwen3.8-27b-nvfp4` |
| Why this model | EQ-Bench Creative Writing v3: 1671 (Muse-Glimmer-30B 1798, gemma-4-31B 1368); Longform 53.3; stronger on agentic/tool-calling benchmarks than Glimmer, mature vLLM support, built-in MTP. Won the 2026-10-23 bake-off against Glimmer on tool calls |

## Recipes

| id | engine | memory | notes |
|---|---|---|---|
| `qwen3.8-27b:vllm` (model `qwen3.8-27b`, mode `creative`, port 10030) | [vllm-salient](../../engines/vllm-salient/README.md) image (confirmed: loads as Qwen3-VL, so it has vision) | 0.31 util ≈ 38 GiB, 46g cap (0.30 left 9.04 GiB KV, under the 9.29 GiB one 256K request needs) | **deployed**, 249 s cold: MTP 3 tokens, FP8 KV, prefix caching, `qwen3_xml` tools, `qwen3` reasoning, 256K ctx (the model's full context; 9.29 GiB of FP8 KV per full-length request), 4 sequences |

## Bake-off (2026-10-23, `bench/2026-10-23-bakeoff{,-b}/`)

Tool steps **18/18**, 30 s shot plan valid in 65 s, median 23.8 tok/s next to an idle ComfyUI. It won on tool
reliability; Glimmer writes better prose and is faster (full table in
[muse-glimmer-30b](../muse-glimmer-30b/README.md)).

**Thinking.** It thinks by default and the reasoning counts toward `max_tokens`. With thinking on, a
long-form chapter spent 53–65K characters of reasoning and wrote nothing in 11–13 min. With
`chat_template_kwargs: {enable_thinking: false}` (`reasoning_effort="none"`) it wrote 1429–1592 words in
about 100 s. Core r8 fails such a job clearly instead of returning empty text.

**No output cap (core r10).** That chapter hit the bake-off's 16K `max_tokens`, not a model limit. Core no
longer adds `max_tokens`, and the context was raised from 128K to the model's full 256K, so a request
runs until the model is done; a looping one is stopped (`core/karspark/loopguard.py`).
Rerun with no cap (`bench/2026-10-23-nocap/`): the same chapter, thinking on, wrote **1871 and 1672 words**
(31.5K tokens in 21 min, then 25.6K in 19 min, ~24 tok/s). Expect about 20 minutes for a chapter with full
reasoning, or about 100 s with `reasoning_effort="none"`.
