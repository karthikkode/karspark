# Engine: vllm-salient (Qwen3.8-Flash-Next)

The vLLM build from [salient-data/qwen38-next-dgx-recipe](https://github.com/salient-data/qwen38-next-dgx-recipe)
(Apache-2.0): vLLM nightly `77154ca6` + 14 GB10/Flash-Next patches.

| | |
|---|---|
| Image | **`karspark/vllm-salient:2026-10-14-77154ca6`**, built on the Spark from `upstream/Dockerfile` by `scripts/build-images.sh` |
| Upstream prebuilt | `ghcr.io/salient-data/qwen38-next-dgx@sha256:3d67df7b…` (same recipe, signed). **Not used**: GHCR serves the Spark at ~50 KB/s, while Docker Hub manages ~7 MB/s |
| Build | `2026-10-14-77154ca6` (upstream commit in `upstream/UPSTREAM_COMMIT`) |
| Signed | cosign, key `upstream/cosign.pub` |
| Base | `vllm/vllm-openai@sha256:a7deff2c…` (Docker Hub prunes nightly tags after ~2 weeks) |

## What's here

- `upstream/`: an exact copy of the recipe's Dockerfile, the 14 patches + checksums, pins, serve args/env and
  the signing key. Our image is built from exactly this: the pinned Docker Hub base + the patches, applied
  with `--fuzz=0`. If the base digest is ever pruned, rebase the patches onto a newer vLLM nightly and bump the tag.
- `spark/`: files deployed to `~/engines/vllm-salient/` on the Spark. `cache/` is mounted at `/root/.cache`
  in the container and holds the sparse draft-token map the speculative config names.

The 14 patches (details in the upstream README): PLE host-file gather, FP8 dense layers, NVFP4 draft
lm_head, SM12x grouped-GEMM alignment, fused mamba block-table, kernel warm-up, quiet boot, MTP shard
filter, QSA ring, confidence-stop drafting, skinny GEMM, RecoverSSM, sparse LM head, batched weight load.
