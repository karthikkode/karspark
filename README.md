# karspark

Defines everything that runs on our **DGX Spark** (`edgexpert-bd12`, GB10, 121 GiB unified memory).
If it isn't in this repo, it isn't supposed to be on the Spark.

## One service for every model

```
http://100.101.249.74:8080/v1     OpenAI + Anthropic text APIs, /v1/jobs (images, layers, video, upscaling), /v1/files
http://100.101.249.74:8080/mcp    the `karspark` MCP server (Streamable HTTP)
http://100.101.249.74:8080/ui     live dashboard: mode, engines, queue, jobs
Authorization: Bearer <project key>          (or x-api-key: <key> for Anthropic SDKs)
```

**karspark-core** owns the GPU. Projects call it as a black box: they submit work, and core decides what
is loaded, queues by priority and fair share, pauses and resumes jobs without losing progress, and logs
every request. Start here: [docs/integrating.md](docs/integrating.md).

Each project gets its own key with a tier (`scripts/karspark-keys create <project> <tier>`). The owner key
`karthik-coding` is the pre-core key, imported as-is:

```bash
ssh spark "sed -n 's/^KARSPARK_API_KEY=//p' ~/core/env"
```

What's deployed: [docs/registry.md](docs/registry.md).

## Docs

- [docs/integrating.md](docs/integrating.md): how another project uses karspark (keys, priorities, jobs, MCP)
- [docs/api.md](docs/api.md) · [docs/mcp.md](docs/mcp.md): the HTTP API and the MCP tools
- [docs/concepts.md](docs/concepts.md) · [docs/modes.md](docs/modes.md) · [docs/scheduler.md](docs/scheduler.md) · [docs/logging.md](docs/logging.md)
- [docs/architecture.md](docs/architecture.md): why karspark-core, and how it fits together
- [docs/standard.md](docs/standard.md): layout and the checklist for adding a model
- [docs/registry.md](docs/registry.md): recipes, engines and ports actually on the box
- [docs/lessons.md](docs/lessons.md): working recipes and GB10 traps

## Commands

```bash
scripts/status.sh                                         # summary (also printed on SSH login)
scripts/fetch-model.sh <hf-repo> <revision-sha> <kind>/<name>   # weights at a pinned revision
scripts/deploy.sh                                         # validate config, build images, restart core (engines keep running)
scripts/karspark-keys list                                # API keys
(cd core && uv run pytest)                                # core's tests, no GPU
scripts/validate-comfy-graphs.sh                          # every ComfyUI graph through ComfyUI's validator, no GPU
```

## Access

`ssh spark` (Tailscale, works anywhere) · `ssh spark-lan` (`192.168.29.130`, at home) · `sudo` needs the password.
