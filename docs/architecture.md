# Architecture: one endpoint for every model (adopted 2026-10-22)

## What we want

1. **One endpoint, one API key.** Every client (laptop, apps, agents) talks to a single URL. Text, TTS,
   speech-to-text and images all go through it.
2. **Models load on demand.** A client asks for a model by name. If it isn't loaded, the gateway loads it
   (evicting whatever is in the way), then answers.
3. **Many recipes, one source.** The same weights can be served several ways (vLLM vs SGLang, MTP on/off,
   different context sizes). Each way is a named recipe, all behind the same endpoint and all reading the
   same files in `~/models`.

## The standard way: a swap-aware OpenAI-compatible gateway

```
 clients ──HTTPS/Bearer key──▶  gateway :8080  (llama-swap, systemd --user, the ONLY public port)
                                   │  routes by the request's "model" field
                                   │  starts/stops backends, one big GPU tenant at a time
                ┌──────────────────┼───────────────────┬───────────────────┐
                ▼                  ▼                   ▼                   ▼
        vLLM (docker)       SGLang (docker)     TTS server          image server
        127.0.0.1:5xxx      127.0.0.1:5xxx      (OpenAI /v1/audio)  (OpenAI /v1/images)
                └──────────── all read weights from ~/models/<kind>/<name> ──────────┘
```

**llama-swap** ([github.com/mostlygeek/llama-swap](https://github.com/mostlygeek/llama-swap)) is the
de-facto tool for exactly this on a single box:

- One OpenAI-compatible endpoint. It covers chat/completions/responses/embeddings, Anthropic `/v1/messages`,
  `/v1/audio/speech`, `/v1/audio/transcriptions`, and `/v1/images/generations`/`edits`.
- **API keys** on the gateway.
- **On-demand loading:** each model is a `cmd`, which can be a `docker run`, so any engine works. The model
  starts on the first request, and `ttl` unloads it when idle.
- **`matrix`** declares which models may run together (e.g. 27B + TTS + images), and everything else swaps.
- **`aliases`** give stable names, so `qwen3.8-flash` points at whichever recipe is the default.
- A web UI at `/ui`: playground, live logs, manual load/unload, token metrics.

Backends bind to `127.0.0.1` only. The gateway is the only thing on the network.

### Recipes = model entries in one config

```yaml
# config.yaml (excerpt). Generated from this repo, never hand-edited on the box.
apiKeys: ["${env.KARSPARK_API_KEY}"]
models:
  "qwen3.8-flash:vllm-mtp":            # recipe 1 (default)
    cmd: docker run --rm --name ks-flash ... karspark/vllm-flash:1 /models/llm/qwen38-flash-next-nvfp4 --port ${PORT} ...
    cmdStop: docker stop ks-flash
    aliases: ["qwen3.8-flash"]          # what clients normally ask for
    ttl: 3600
  "qwen3.8-flash:vllm-fp8hybrid":     # recipe 2: same family, different recipe, opt-in by full name
    cmd: ...
  "qwen3.8-27b:vllm-mtp":
    cmd: ...
    aliases: ["qwen3.8-27b"]
  "qwen3.8-27b:sglang-dflash2":
    cmd: ...
  "indextts-2":
    cmd: ...                            # OpenAI /v1/audio/speech wrapper
  "flux2-dev":
    cmd: ...                            # OpenAI /v1/images wrapper around ComfyUI
matrix: ...                             # e.g. 27b + indextts-2 + flux2-dev may coexist; flash runs alone
```

Naming is `<model>:<recipe>`. The alias without the recipe is the default. Switching the default recipe is
a one-line alias change, so clients don't change at all.

### The one real gap: TTS and images need an OpenAI-shaped server

vLLM and SGLang already speak the OpenAI API. IndexTTS-2, VoxCPM2 and ComfyUI don't. Each one gets a thin
wrapper we own (`engines/<name>-api`, a small FastAPI app):

- **TTS:** `POST /v1/audio/speech {model, input, voice}` → IndexTTS-2 / VoxCPM2 → audio.
- **Images:** `POST /v1/images/generations {model, prompt, size}` → runs a saved ComfyUI workflow
  (FLUX.2) → PNG. ComfyUI stays behind it on localhost.

## Why not the alternatives

| option | verdict |
|---|---|
| **LiteLLM proxy** | Excellent at per-person virtual keys, spend limits, logging and cloud fallbacks, but it needs Postgres and it doesn't load or swap local models. Add it **in front of** llama-swap later only if you need per-app keys or usage tracking. Not needed on day 1. |
| Ollama / LocalAI | All-in-one, but they run their own engine builds. Our best Spark recipes need custom vLLM images (PLE-mmap, prefix-cache patches, NVFP4), so we'd lose them. |
| NVIDIA Dynamo / Triton / KServe | Built for multi-node clusters with Kubernetes. Far too heavy for one box. |
| One systemd unit per model (what we had) | Exactly what made the box unreadable: many ports, many keys, manual switching. |

## Running several models / engines at once

Every recipe is just a command plus a port, so engines mix freely: vLLM, SGLang, llama.cpp, Python TTS and
the image wrapper can all be loaded together. `gateway/routing.yaml` (the llama-swap **matrix**) lists the
combinations allowed to coexist. A request for a model outside the current combination evicts the fewest,
cheapest models needed to make room. Limits:

- **Memory:** everything loaded together must fit in 121 GiB.
- **Fractions:** vLLM `--gpu-memory-utilization` and SGLang `--mem-fraction-static` are fractions of the
  *whole* GPU. Co-resident recipes must have fractions that sum to less than ~0.85, plus a Docker `--memory` cap each.
- **Behaviour differs per engine:** tool-call parser, chat template and reasoning output come from the
  engine, so each recipe README records them.
- **Shared bandwidth:** concurrent models slow each other down.

## Things to know up front

- **A config reload unloads every loaded model.** `scripts/deploy.sh` only rewrites the config when it
  actually changed, and warns when that will unload something.

- **Cold starts are real.** The first request to an unloaded model waits for it to load: about 1–2 min for
  the 27B, about 15 min for Flash-Next. Clients need long timeouts, or you preload from `/ui`.
- **One big tenant at a time.** If you ask for Flash while the 27B is serving someone, the 27B gets stopped.
  `matrix` makes the allowed combinations explicit.
- **Adding a model is still deliberate:** download the weights → add a recipe entry plus a registry row in
  this repo → deploy. The gateway reloads its config without dropping the endpoint. "Load any model from
  here" means any model that has a recipe, not arbitrary HF repos at request time. That's what keeps the
  box clean.

## Repo changes this implies (to `standard.md`)

```
gateway/            llama-swap install + unit + config.template.yaml (the single service)
recipes/<model>/    one folder per model: README (source + revision), <recipe>.yaml snippets, Dockerfile(s)
engines/<name>-api/ our OpenAI wrappers for TTS and images
scripts/deploy.sh   renders recipes/* into the gateway config, pushes it, reloads; never one-off units
```

`services/` goes away. The gateway is the only service.

## Proposed build order

1. Gateway (llama-swap) plus one small text model, to prove keys, swap, TTL and the UI end to end.
2. Qwen3.8-27B (vLLM MTP), the everyday coding/agent model.
3. Flash-Next recipe (rebuild the image from a Dockerfile with the two patches from `lessons.md`).
4. TTS wrapper + IndexTTS-2 / VoxCPM2.
5. Image wrapper + ComfyUI + FLUX.2.
6. (Optional) LiteLLM in front for per-app keys and usage.
