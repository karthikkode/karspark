# Qwen3.8-27B Abliterated — personal / uncensored chat

**Status 2026-10-22: STOPPED** (unit disabled, so it doesn't start at boot).

| | |
|---|---|
| Checkpoint | `huihui-ai/Huihui-Qwen3.8-27B-abliterated` → `~/models/qwen38-27b-abliterated` |
| Precision | **BF16, not quantized** (52 GB, 18 shards, all present; `DOWNLOAD-STATUS.txt` still says "downloading", which is stale) |
| Engine | vLLM venv `~/vllm-env` (0.26.0) |
| Script | [spark/qwen38-27b-abliterated/serve.sh](../../spark/qwen38-27b-abliterated/serve.sh) |
| Unit | user `qwen38-27b-abliterated.service` + drop-in `90-katha-workers.conf` |
| Served as | `qwen38-27b-abliterated`, port **8002**, `qwen3_xml` tools, `qwen3` reasoning, MTP 3 tokens |
| Limits | `GPU_UTIL=0.80`, **`MAX_SEQS=3`, `MAX_LEN=65536`** (the drop-in overrides the unit's 1 / 262K, to run parallel katha workers) |
| Auth | the unit loads `~/.config/qwen38-27b-hybrid/env`, but `serve.sh` never passes `--api-key`, so **:8002 has no auth** ([findings.md](../findings.md) #4) |

Starting it **evicts everything**. `ExecStartPre` runs `gpu_residency.sh 104 …`, which stops Flash, the
27B units, the 122B, ComfyUI and IndexTTS, then waits for 104 GiB to be free. `Conflicts=` covers the same
set.

```bash
ssh spark 'systemctl --user start qwen38-27b-abliterated'
```

It's BF16 because no NVFP4 build of the abliterated weights existed when it was downloaded (2026-09-11).
An NVFP4 or FP8 quant would take about 15–28 GB instead of 52 GB and decode about 2× faster on this
memory-bound box. That's worth doing if this model gets used regularly.
