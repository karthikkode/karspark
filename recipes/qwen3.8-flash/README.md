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

## Measured on our Spark

Pending the bake-off (step 4 of the plan). Upstream reports 71.3 tok/s median single stream, 42.6 prose,
84 tok/s total at 6 streams, and a ~5–6 min cold start.
