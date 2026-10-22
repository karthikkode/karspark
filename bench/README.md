# Bench

One harness for every recipe, run in a container (`karspark/bench:1`) against the gateway. It executes
model-written code to grade answers, so it never runs on the host.

- `tonyd2wild/`: the community harness used by the published single-Spark Flash-Next numbers, vendored
  byte-identical at the commit in `UPSTREAM_COMMIT` (Apache-2.0). It has 40 real prompts in 8 categories
  with auto-graded quality, streams to get TTFT and decode tok/s, optionally batches into N concurrent
  streams for total throughput, and adds a long-prefill needle test.
- `sitecustomize.py` adds the gateway key to its requests. `run.sh` is the sequence.
- Caveat inherited from upstream: the `summary` category reads `bench-docs/*`, which upstream doesn't
  publish, so those 5 prompts summarize a placeholder. Compare recipes on the other 35.

Run it (results land in `recipes/<model>/bench/<date>-<label>/`):

```bash
scripts/bench.sh qwen3.8-flash:vllm-salient salient
```
