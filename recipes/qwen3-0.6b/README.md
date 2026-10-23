# qwen3-0.6b

| | |
|---|---|
| Purpose | integration tests on the box (mode `test`); tiny utility model (classification, routing, quick drafts) |
| Weights | `Qwen/Qwen3-0.6B` @ `c1899de289a04d12100db370d81485cdf75e47ca` → `~/models/llm/qwen3-0.6b` (1.5 GB) |
| Fetch | `scripts/fetch-model.sh Qwen/Qwen3-0.6B c1899de289a04d12100db370d81485cdf75e47ca llm/qwen3-0.6b` |

## Recipes

| id | engine | memory | ctx | notes |
|---|---|---|---|---|
| `qwen3-0.6b:vllm` (model `qwen3-0.6b`, mode `test`, port 10002) | [vllm](../../engines/vllm/README.md) v0.32.0 | 0.08 util ≈ 10 GiB | 8K | `hermes` tool parser, `qwen3` reasoning parser |

## Measured

See the registry row, filled in on first deploy.
