# Architecture: karspark-core, one service in front of every model (adopted 2026-10-23)

Replaces the llama-swap gateway (2026-10-22). The gateway loaded whatever the latest request named. It had
no queue, no priorities, no async jobs, no per-project keys and no request log, so mixed traffic thrashed
the GPU: Flash-Next alone takes ~100 of 121 GiB and ~6 min to load. karspark is now a **black box** that
other projects call without thinking about GPUs, loading or queues.

## What it guarantees

1. **One endpoint, one key per project.** OpenAI and Anthropic text APIs, async media jobs, files, MCP.
   Keys have tiers (owner, critical, standard, batch) that cap priority and set limits and fair shares.
2. **The GPU follows the work, not the last request.** A *mode* is a set of engines loaded together. The
   scheduler switches modes on demand, with a coding hold, deferred mode requests and an idle return.
3. **Nothing is lost.** Media jobs can pause at any sampling step and resume from a checkpoint. Urgent work
   and mode switches use the same pause. Long text jobs keep their partial output.
4. **Everything is recorded.** Every request with timings, tokens and bodies (30 days); every job, scheduler
   and engine event; resource samples.

## Shape

```
 projects / agents ── API key (tiered) ──▶ karspark-core :8080   (the only user-facing service)
   /v1/chat/completions /v1/messages · /v1/jobs · /v1/files · /v1/mode · /v1/status · /mcp · /ui · /admin
   ┌───────────────── karspark-core (one asyncio process, in Docker) ───────────────┐
   │ auth + tiers → rate limits → routes ──▶ scheduler (policy.py) ──▶ engine manager │
   │ logging service (async writer → logs.db)      jobs + files (karspark.db, WAL)    │
   └──────────────────────────────────────────────────────────────────────────────────┘
        mode coding (P0, default)               mode creative
        └ qwen3.8-flash:vllm-salient ~100 GiB   ├ qwen3.8-27b:vllm  ~32 GiB  (planner LLM, always on)
                                                └ comfyui:comfy     ~75 GiB  (FLUX.2, Qwen-Layered, LTX-2.5, SeedVR2)
 state: ~/data/karspark.db · ~/data/logs.db · ~/data/jobs/<id>/{in,out,ckpt} · ~/data/files/
```

- **Engines** are containers that core starts through the host's Docker socket. They run detached on fixed
  ports (`127.0.0.1:<port>` only). Core adopts them after its own restart, so a deploy never reloads a
  model. Only containers labelled `karspark.recipe=<id>` count as core's.
- **Async end to end.** Long work is a job: submit, then poll, stream SSE, long-poll or get a webhook.
  Text keeps the standard synchronous streaming APIs, so OpenAI and Anthropic SDKs and opencode work
  unchanged. One event loop owns all scheduler state, so it needs no locks and has one SQLite writer.
- **Media goes through the ComfyUI adapter** (`engines/comfyui/adapter`): one job is one ComfyUI run in a
  shared job folder. Every graph samples through `KarsparkSampler` (`engines/comfyui/karspark_nodes`). It
  writes progress each step and checkpoints the latent on a pause. The run continues from that step,
  skipping finished stages.

## Scheduling in one paragraph

Priority classes run P0 > P1 > P2 > P3, capped by the key's tier. Aging promotes waiting work one class
per `age_after_s`, never into P0. Keys inside a class share by weight (start-time fair queuing). Among
equals, a job for the model family ComfyUI already holds goes first, within a skip budget. P0/P1 work from
a tier allowed to preempt pauses a running P3 job, at most 3 times per job, with its progress kept.
Entering coding mode pauses media and gives text streams 60 s. Coding then holds for 30 min after the last
coding request. Any key may ask for a mode: owners switch at once; others wait out the hold and running
work. The full rules are in [scheduler.md](scheduler.md) and [modes.md](modes.md); the code is
`core/karspark/policy.py`, pure functions tested on a fake clock.

## Why build it rather than configure something

| option | verdict |
|---|---|
| llama-swap (what we had) | Great swap-on-request proxy, but it has no queue, priorities, jobs, per-project keys or request log. A request for another model unloads the current one mid-work. |
| LiteLLM proxy | Virtual keys, budgets, logging, but it neither loads nor swaps local models or runs media jobs, and needs Postgres. |
| Ollama / LocalAI | Their own engine builds; we'd lose the custom vLLM images our Spark recipes need. |
| Dynamo / Triton / KServe | Multi-node, Kubernetes. Far too heavy for one box. |
| ComfyUI's own queue | FIFO only, and an interrupt throws work away. We keep ComfyUI as an engine and own the queue. |

## Limits to keep in mind

- **Memory is per mode.** Engines in one mode must fit in 115 GiB together (checked at deploy).
  vLLM/SGLang memory fractions are of the whole 121 GiB.
- **Switching costs minutes.** Flash-Next loads in ~6 min warm and ~15 cold; creative mode in about 2 min.
  The scheduler batches by mode for that reason, and ETAs include the measured switch cost.
- **`model: "text"`** follows the loaded mode and never waits. Naming a model from another mode waits for
  that mode. Coding models are for coding keys only.
- **A config change restarts core, not the engines.** Text streams in flight are cut; jobs carry on.
