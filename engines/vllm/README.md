# Engine: vLLM

| | |
|---|---|
| Image | `vllm/vllm-openai:v0.32.0-aarch64` |
| Pinned digest | `sha256:6ff5ff0ddf3658d2c2d677dac5ca2d4726ce0156b4371166d918181b210bd4bc` (released 2026-10-18) |
| Size | ~10 GB compressed |
| Entrypoint | `vllm serve`, so the recipe passes `<model-path> [flags]` |

Recipes reference the image **by digest**, never by tag, so a tag moving upstream can't change a running recipe.

Pull it on the Spark (only needed once per digest):

```bash
ssh spark 'docker pull vllm/vllm-openai@sha256:6ff5ff0ddf3658d2c2d677dac5ca2d4726ce0156b4371166d918181b210bd4bc'
```

## Rules for vLLM recipes on GB10

- `--gpu-memory-utilization` is a fraction of the **whole** 121 GiB pool, and every concurrent engine
  thinks it owns the GPU. Fractions of models in the same routing set must add up to less than ~0.85.
- Always bind `-p 127.0.0.1:${PORT}:8000`. Never publish a backend on all interfaces.
- Give each container a `--memory` cap a little above its expected footprint.
- Recipe-specific images (patches, overlays) get a Dockerfile under `engines/vllm/<variant>/` and are
  tagged `karspark/vllm-<variant>:<ver>`. Never `docker commit`.
