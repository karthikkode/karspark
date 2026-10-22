# Qwen3.8-Flash-Next (the model that is live)

**Status 2026-10-22: RUNNING**, the Spark's primary model since the 2026-10-12 boot.

| | |
|---|---|
| Model | Qwen3.8-Flash-Next, 176B hybrid MoE (125B main + 51B n-gram "PLE" table), ~6B active, 512 experts, QSA sparse attention, multimodal |
| Checkpoint | `RadixArk/Qwen3.8-Flash-Next-NVFP4` → `~/models/qwen38-flash-next-nvfp4` (126 GB on disk) |
| Engine | vLLM in Docker, image `qwen38-flash:v1.4-prefixfix` (local build, see below) |
| Served as | `qwen3.8-flash-next`, port **30000** (host) → 8000 (container); keepalive proxy on **30001** |
| APIs | OpenAI `/v1/chat/completions`, Anthropic `/v1/messages` |
| Context | 262,144 tokens, `--max-num-seqs 8` |
| Speculative decoding | MTP (the model's own head), `num_speculative_tokens=2` |
| Parsers | `--tool-call-parser qwen3_coder --reasoning-parser qwen3`, template `chat-template-flashnext.jinja` |
| Auth | `--api-key $(cat ~/.config/qwen38/api-key)` |
| Unit | **user** `qwen38-flash.service` (enabled) → [spark/qwen38/launch-flash.sh](../../spark/qwen38/launch-flash.sh) |

## Which recipe: `hasso5703/dgx-spark-qwen38` v1.4, target `flash`, plus two local patches

Clone on the Spark: `~/dgx-spark-qwen38` (commit `b43b737`, "v1.4: Qwen3.8-Flash-Next 176B as a third switchable target").
It was installed with `MODEL_CHOICE=flash ./install.sh`, which:

1. Pulls the official image `vllm/vllm-openai:qwen38-flash-next` pinned by digest
   (`sha256:fc120ece…5bf8`, 2026-08-27).
2. Builds `qwen38-flash:v1.4` on top with the **PLE-mmap overlay** (`flash/vllm_ple_mmap.py`, by
   blazux, Apache-2.0). That one change makes the model fit: the 44–48 GiB n-gram table is served from
   NVMe through `mmap` and the page cache instead of sitting in unified memory. The weights drop to about
   76 GiB, which leaves room for KV cache.
3. Renders `~/.config/qwen38/launch-flash.sh` and the unit.

### What we changed locally (2026-08-31 to 2026-09-19)

**a) `qwen38-flash:v1.4-prefixfix` image.** Upstream v1.4 ships `--no-enable-prefix-caching` because
prefix caching crashed on sm_121. On 2026-08-31 two vLLM files were patched inside a running v1.4
container, and the result was saved with `docker commit`:

- `vllm/v1/core/single_type_kv_cache_manager.py` — `BCN81_EAGLE_DROP`, a port of unmerged vllm#48375:
  with MTP/EAGLE, drop the last matched page of a prefix-cache hit, because its recurrent (GDN/mamba)
  snapshot can hold draft tokens that were later rejected.
- `vllm/v1/worker/gpu/model_states/mamba_hybrid.py` — `BCN81_STATE_SEED`, the fix from vllm#53142:
  seed the mamba state index with the *mamba* group's block size. Without it a resume over a cached prefix
  gives an illegal memory access on the second request.

The exact diff is in [patches/vllm-prefixfix.patch](../../patches/vllm-prefixfix.patch). The commit also
baked in a warm torch.compile/Triton cache (387 MB layer).

> ⚠️ A `docker commit` image can't be rebuilt from source, and this one stores the **live API key** in its
> image metadata. See [findings.md](../findings.md) #1.

**b) Launch flags that differ from upstream v1.4**, all in `launch-flash.sh`:

| flag | upstream v1.4 | live |
|---|---|---|
| image | `qwen38-flash:v1.4` | `qwen38-flash:v1.4-prefixfix` |
| prefix caching | `--no-enable-prefix-caching` | `--enable-prefix-caching --mamba-cache-mode align` |
| `--max-num-seqs` | 2 | 8 |
| `--gpu-memory-utilization` | 0.78 | 0.85 |
| `--kv-cache-dtype` | (default) | `auto` |

Older versions of the script are kept on the Spark as `launch-flash.sh.bak-{noprefix,seqs2,gpumem,flashexp-…}`.

### Measured (the `~/flash-exp` sweep, 2026-09-19)

The tuning sweep is logged in [spark/flash-exp/ledger.jsonl](../../spark/flash-exp/ledger.jsonl). Decode is in tok/s, single stream:

| config | code | prose | prefill @60K | 8 concurrent, total |
|---|---|---|---|---|
| pristine fresh boot (≈ live) | 30.3 | 24.5 | 2,176 tok/s | 116.9 tok/s |
| prefix caching off | 28.4 | 24.6 | 2,206 | 102.1 |
| gpu-mem 0.78 | 30.7 | 24.7 | 2,187 | 112.5 |
| FULL cudagraphs / PLE CPU offload / fp8 KV | — | — | — | **failed to start** |

Decode at 8K / 32K / 64K / 128K of context: about 25–34 tok/s. Prefill at 128K: about 5,300–7,000 tok/s.
The ~31 tok/s ceiling comes from kernel-launch overhead in the 512-expert MoE at batch 1, so flag
tuning can't push past it.

## The FP8-hybrid alternative (tried, not adopted)

`~/flash-recipes/` holds three other community recipes that were cloned on 2026-09-19:

- `albond/SingleSpark-Qwen3.8-Flash-Next`: claims 44 tok/s single request, NVFP4 + FP8 hybrid.
- `lancelind/qwen3.8-Flash-DGX`: image `ghcr.io/lancelind/qwen38-flash-dgx@sha256:62a77b…`.
- `MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark`: the Mia-AiLab 99 GiB checkpoint.

There's also a "sharp" chat template (`flash-recipes/sharp-template`).

`launch-flash.hybrid.sh` and `launch-flash.hybrid-mtp.sh` run the lancelind image against
`~/models/qwen38-flash-next-nvfp4-fp8hybrid` (139 GB on disk) with the sharp template.
`~/flash-recipes/switch-flash.sh hybrid|baseline` copies one of them over `launch-flash.sh` and then
stops the container so it restarts. **The live script is byte-identical to `~/flash-exp/launch-flash.orig`
(the baseline)**, so the hybrid was reverted. The 139 GB checkpoint is still on disk ([findings.md](../findings.md) #6).

## Operating it

Restart (loading takes about 13–15 min):

```bash
ssh spark 'systemctl --user restart qwen38-flash'
```

Logs:

```bash
ssh spark 'docker logs --tail 50 qwen38-flash'
```

Stopping it frees roughly 100 GiB. Any other big model unit's `Conflicts=` stops it automatically.

> There is also a **system** unit `/etc/systemd/system/qwen38-flash.service` (disabled, `Restart=always`)
> that runs the same launch script. It's what upstream `install.sh` created. Only the user unit is in use.
> Don't enable both ([findings.md](../findings.md) #3).
