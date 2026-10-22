# Plan: the default model, Qwen3.8-Flash-Next (researched 2026-10-22)

Goal: the fastest recipe for **single-stream** and **multi-stream** decode on one DGX Spark, without giving
up agent quality (tool calls, structured output). Flash-Next becomes the gateway's default model.

## The field (all numbers self-reported, different harnesses, so not directly comparable)

| recipe | engine / weights | 1 stream | multi-stream (total) | prefill | quality evidence | verdict |
|---|---|---|---|---|---|---|
| **[salient-data/qwen38-next-dgx-recipe](https://github.com/salient-data/qwen38-next-dgx-recipe)** build 2026-10-14 | vLLM nightly `77154ca6` + 14 patches, **official `nvidia/…-NVFP4`** | **71.3 median**, 42.6 prose, 70–80 code/JSON | 84 @ 6 | n/r | tool-eval-bench **90.0 ± 1.0** (pass^5 81.5%), GSM8K 99/100, needle @131K | **default candidate** |
| [dime-online/qwen3.8-Flash-DGX-UltraFast](https://github.com/dime-online/qwen3.8-Flash-DGX-UltraFast) v16b | vLLM + patches, Intel **AutoRound W4A16** hybrid + FP8 PLE | 74.1 (copy-heavy) | **212 @ 8** | **4,016 @16K**, 2,213 @128K | own suite 93%; the parent AutoRound stack scored tool-eval **87.6 ± 2.2** (pass^5 70.7%) | throughput candidate |
| [MiaAI-Lab (reference)](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark) | vLLM, Mia's own NVFP4 checkpoint (method undocumented) | 48.7 prose | 163 @ 8 prose | ~2,200 | none for its checkpoint; on the official checkpoint only a spot check (~25 tok/s) | reference only |
| [MiaAI TensorFold](https://mia-ai.net/models/Qwen3.8-Flash-Next-Single-DGX-Spark-TensorFold) | own Zig engine | 46–55 prose, 68 code (FP4) | 201 @ 8 (INT4) | ~2,400 | INT4 ≈ 10% lower capability | **excluded**: no structured outputs / logprobs (HTTP 400), refuses instead of queueing |
| our old setup (hasso5703 v1.4) | vLLM + PLE-mmap | ~31 | 117 @ 8 | ~2,200 | canaries | superseded |
| **DeepSeek DeepInfer** (open-sourced 2026-10-20) | — | — | — | — | — | **not applicable**: SM100 + Ascend only, DeepSeek-V4.1-Flash only, offline engine without a serving layer |
| SGLang single-Spark | — | ~37–47 | — | — | — | no competitive single-Spark recipe; NVIDIA's card lists vLLM only |

What makes the fast recipes fast, in order of impact: a **deeper MTP draft with a confidence stop** (up to
6 tokens alone, 3 under load), a **reduced/sparse draft vocabulary**, **FP8 for the dense BF16 layers**, the
**PLE table read from disk** instead of held in memory, FULL decode CUDA graphs, and kernel fixes for
SM121. Quantization matters for quality: the official NVFP4 keeps attention and shared experts in high
precision, and it's the one with the best independent agent score.

## Recommendation

1. **Default recipe `qwen3.8-flash:vllm-salient`** (alias `qwen3.8-flash`).
   - Official NVIDIA weights `nvidia/Qwen3.8-Flash-Next-NVFP4` @ `fc694b54fb0174e0913e6adf86691ef85a4ead47`
     (132.7 GB). That's the same revision the recipe pins, and still the latest.
   - Prebuilt, cosign-signed image
     `ghcr.io/salient-data/qwen38-next-dgx@sha256:3d67df7b7b7d25a5da64f62c0f87f6109062584261fe77157f6d37de178d10b2`.
     We pin that digest and also keep the 14 patches + Dockerfile in `engines/vllm/salient/`, so we can
     rebuild if it ever disappears. Its base tag on Docker Hub gets pruned after about 2 weeks.
   - Best measured single-stream on the best-validated quantization, a ~5–6 min cold start (vs ~15 for our
     old setup), prefix caching, `qwen3_xml` tools, thinking off by default (per-request `enable_thinking`),
     262K context.
   - Weakness: concurrency was only measured to 6 streams (84 tok/s total).
2. **Throughput challenger `qwen3.8-flash:vllm-autoround`**, phase 2, only if multi-stream matters.
   dime v16b reports 2.5× the total throughput at 8 streams and twice the prefill, but it uses different
   weights (a separate ~100+ GB download) and carries a measurable agent-reliability cost. It only gets kept
   if it holds up on *our* quality tests. Then the gateway serves both, and you pick by name.

## Our own bake-off (so the decision rests on our box, not forum numbers)

`scripts/bench/` runs **one** harness against any recipe through the gateway:

- **Speed:** tonyd2wild's 40-prompt harness (the one salient and others publish with, so our numbers line
  up with theirs). Single stream prose / code / structured; total throughput at 2 / 4 / 8 streams; prefill
  at 16K / 64K / 128K; time to first token; cold-start time.
- **Quality:** tool-eval-bench (92 scenarios, the community agent benchmark that separated NVFP4 from
  AutoRound), a GSM8K-100 subset, a needle test at 128K, and a prefix-cache canary.
- Results go into the recipe README + registry. The default alias goes to the winner.

## Steps (nothing downloads until you say go)

| # | step | cost |
|---|---|---|
| 1 | fetch `nvidia/Qwen3.8-Flash-Next-NVFP4@fc694b54` → `~/models/llm/qwen3.8-flash-next-nvfp4` | **132.7 GB**, ~3–4 h on the Spark's Wi-Fi (resumes on drops) |
| 2 | pull the signed salient image by digest, verify the signature, vendor the patches + Dockerfile | ~25 GB |
| 3 | recipe `qwen3.8-flash:vllm-salient`, runs alone; gateway **preloads it at startup** and never unloads it on idle (TTS/image requests still evict it) | — |
| 4 | bake-off harness + first measurements, then registry + README | ~1 h |
| 5 | *(optional)* AutoRound weights + `vllm-autoround` recipe, same bake-off, keep or drop | ~100+ GB |

## Known risks

- Every recipe here builds on a vLLM nightly plus community patches. Pinning by digest plus vendoring the
  patches is what makes it reproducible.
- The salient fast path drafts with a reduced vocabulary, so non-English text (Telugu, CJK) drafts less
  well and decodes slower. Output quality isn't affected. We'll measure it in the bake-off.
- Flash-Next needs ~100 GiB, so while it's loaded nothing else big runs. TTS and images swap it out, and it
  takes ~5–6 min to come back.
