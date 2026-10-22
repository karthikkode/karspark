# qwen3.8-flash (the default model)

| | |
|---|---|
| Model | Qwen3.8-Flash-Next: 176B MoE (125B main + 51B n-gram "PLE" table), ~6B active, 262K context, text + vision |
| Weights | `nvidia/Qwen3.8-Flash-Next-NVFP4` @ `fc694b54fb0174e0913e6adf86691ef85a4ead47` → `~/models/llm/qwen3.8-flash-next-nvfp4` (132.7 GB) |
| Fetch | `scripts/fetch-model.sh nvidia/Qwen3.8-Flash-Next-NVFP4 fc694b54fb0174e0913e6adf86691ef85a4ead47 llm/qwen3.8-flash-next-nvfp4` |
| Why this recipe | [docs/plans/qwen3.8-flash-recipe.md](../../docs/plans/qwen3.8-flash-recipe.md) |

## Recipes

| id | engine | memory | notes |
|---|---|---|---|
| `qwen3.8-flash:vllm-salient` (alias **`qwen3.8-flash`**) | [vllm-salient](../../engines/vllm-salient/README.md) build 2026-10-14-77154ca6 | ~100 GiB (0.80 util, 112g cap). Runs alone | MTP up to 6 drafts with a confidence stop (3 under load), FP8 KV, prefix caching, `qwen3_xml` tools, `qwen3` reasoning, thinking **off** by default, max 6 concurrent sequences |

## Using it

```json
{"model": "qwen3.8-flash", "messages": [...]}
```

Turn thinking on for one request: `"chat_template_kwargs": {"enable_thinking": true}`.

## Measured on our Spark (2026-10-22, `scripts/bench.sh`, tonyd2wild 40-prompt harness, thinking off)

Raw results: [bench/2026-10-22-salient/](bench/2026-10-22-salient/SUMMARY.md). Upstream reported 71.3 median / 42.6 prose /
84 @6. **We reproduce it: 70.6 / 41.8 / 87.5 @6.**

| streams | per-stream decode | total throughput | prose / code per stream | TTFT |
|---|---|---|---|---|
| 1 | **70.6 tok/s** | 70.6 | 41.8 / **79.0** | 0.22 s |
| 2 | 48.2 | 65.5 | 32.7 / 50.3 | 0.30 s |
| 4 | 34.8 | 69.3 | 30.8 / 37.5 | 0.40 s |
| 6 | 27.4 | 87.5 | 20.4 / 29.9 | 0.47 s |
| 8 | 25.8 | 100.4 | 19.5 / 26.4 | 0.55 s |

- Prefill (cold, needle found every time): 14K → 5.7 s (2,479 tok/s), 56K → 21.4 s (2,641), 113K → 41.8 s (2,698).
- Cold start through the gateway: **360 s** (weights 117 s; model resident 73.5 GiB; KV pool 889,758 tokens = 3.4 full 262K requests).
- Smoke: tool call ✓, thinking on ✓, Anthropic `/v1/messages` ✓, 67.6 tok/s on code streamed to the laptop over Tailscale.
- Weak spot: total throughput only reaches ~100 tok/s at 8 streams, because the deep MTP drafting that wins at 1 stream
  helps less under load. If parallel agents become the main workload, benchmark a throughput recipe
  (plan step 5: AutoRound / v16b).
