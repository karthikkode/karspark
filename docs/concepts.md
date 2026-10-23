# Concepts

| word | meaning | example |
|---|---|---|
| **model** | a public name callers use | `flux2-dev`, `qwen3.8-flash`, `text` |
| **recipe** | how one or more models are served: image, args, port, memory (`recipes/<model>/<recipe>.yaml`) | `comfyui:comfy` |
| **engine** | one running container started from a recipe, on `127.0.0.1:<port>`, labelled `karspark.recipe=<id>` | `ks-comfyui-comfy` |
| **mode** | the set of engines loaded together. Exactly one is active (`config/modes.yaml`) | `coding`, `creative` |
| **key** | one project's credential; stored only as an HMAC | `karthik-coding` |
| **tier** | a key's class: priority ceiling, limits, fair-share weight (`config/tiers.yaml`) | `owner`, `critical`, `standard`, `batch` |
| **priority** | P0 (most urgent) to P3 (batch); effective = what you asked, capped at the tier's ceiling | P2 |
| **job** | one unit of async work with an id and a state; survives restarts, pauses and mode switches | `job_3f9c…` |
| **text stream** | a synchronous OpenAI/Anthropic call; takes a slot on the mode's text engine | `/v1/chat/completions` |
| **slot** | concurrency of an engine: text sequences, or one media job for ComfyUI | Flash-Next 6 |
| **checkpoint** | a paused job's saved state on disk (latent + step, finished stages, finished chunks, partial text) | `jobs/<id>/ckpt/` |
| **file** | an upload or a job output, owned by a key, kept for the tier's retention | `file_…` |

## How a request flows

```
key → auth + tier → rate limit → route
  text   → scheduler grants a slot on the current mode's text engine (waits if the model needs another mode)
           → proxied to vLLM, keepalives while waiting/silent → logged with tokens and timings
  job    → validated against the graph builder → queued (karspark.db)
           → scheduler picks it (priority, aging, fair share, affinity) → switches mode if needed
           → worker runs it on the engine (ComfyUI adapter / vLLM) → outputs registered as files
           → webhook / wait / SSE
```

Everything is decided in one asyncio loop in karspark-core, so scheduler state has one writer and no locks.
Engines are separate containers; core never runs GPU code.

See also: [modes.md](modes.md), [scheduler.md](scheduler.md), [logging.md](logging.md),
[architecture.md](architecture.md).
