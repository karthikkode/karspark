# Integrating a project with karspark

karspark is a black box. Your project sends work and gets results. It never thinks about GPUs, which
model is loaded, or queues. This page is the contract. Agents: [agents.md](agents.md) shows how to use it
efficiently.

## 1. Get a key

One key per project, never shared. The owner creates it:

```bash
scripts/karspark-keys create my-project standard      # tiers: critical | standard | batch (owner = the owner only)
```

The key is printed once. Put it in your project's secret store as `KARSPARK_API_KEY`. Send it as
`Authorization: Bearer <key>` (or `x-api-key`, for Anthropic SDKs). Base URL: `http://100.101.249.74:8080`.

| tier | highest priority | rate limits | open jobs | fair-share weight | use for |
|---|---|---|---|---|---|
| `owner` | P0 | none | 1000 | 8 | the owner's coding agent and tools |
| `critical` | P1 | 600 req/min, 2M tokens/min | 200 | 4 | projects whose results someone is waiting on |
| `standard` | P2 | 120 req/min, 500K tokens/min | 50 | 2 | normal projects |
| `batch` | P3 | 60 req/min, 200K tokens/min | 1000 | 1 | bulk, overnight, "whenever" work (can be preempted) |

## 2. Priorities

Every request has a priority, P0 (most urgent) to P3 (batch). Ask with `"priority": 0..3` on a job, or the
`X-Karspark-Priority` header on text calls. **You get at most your tier's ceiling.** Asking higher is
allowed and is capped. Every job reports `priority` (effective) and `priority_req` (what you asked).

- Work waiting too long moves up a class: P3 after 30 min, P2 after 10 min, P1 after 3 min. It never
  reaches P0. Nothing starves.
- Inside a class, projects share by tier weight. Your flood can't starve another project, and theirs can't
  starve yours.
- P0 and P1 work from `owner` and `critical` keys may **preempt** a running P3 job. The P3 job is paused,
  keeps its progress, and resumes first when the urgent work is done (at most 3 times per job).

## 3. Text: use the SDK you already have

```python
from openai import OpenAI
client = OpenAI(base_url="http://100.101.249.74:8080/v1", api_key=os.environ["KARSPARK_API_KEY"])
r = client.chat.completions.create(model="text", messages=[{"role": "user", "content": "Plan 6 shots for ..."}])
```

- **`model="text"`** = whatever text model the current mode has loaded (the planner in creative mode,
  Flash-Next in coding mode). It never waits for a model switch. Use it unless you need one model.
- Naming a model (`qwen3.8-27b`) waits until its mode is loaded, which can take minutes. `qwen3.8-flash`
  is reserved for coding keys.
- Streams get keepalive pings (an SSE comment, or an Anthropic `ping` event) while they wait. Allow long
  read timeouts (10 min or more).
- Anthropic SDK: `base_url="http://100.101.249.74:8080"`, same key, `/v1/messages`.
- karspark adds no `max_tokens`: a request runs until the model stops or its context is full (256K tokens
  for both the planner and Flash-Next). A model stuck repeating itself is stopped with an error. Set
  `max_tokens` only if you really want a cap.
- The planner (`qwen3.8-27b`) thinks before answering, which can take many minutes on long prose. Send
  `reasoning_effort: "none"` (text jobs) / `chat_template_kwargs: {"enable_thinking": false}` (chat) to skip
  it. Non-streamed calls give up after 30 min (MCP `generate_text`: 10 min), so stream, or use a text job.
- Long writing you might want to pause: submit it as a `text` **job** (below). A pause keeps the partial
  text, and resume continues it.

## 4. Media: jobs

Images, layers, video and upscaling are **jobs**. Submit one, get an id, then collect the result.

```bash
curl -s $KS/v1/jobs -H "Authorization: Bearer $KEY" -H "Idempotency-Key: shot-12-v1" \
  -d '{"type": "image", "prompt": "...", "size": "832x1216", "priority": 2, "webhook": "https://my.app/hooks/ks"}'
# -> 201 {"id": "job_...", "state": "queued", "eta_s": 240, ...}
curl -s "$KS/v1/jobs/job_.../wait?timeout=50" -H "Authorization: Bearer $KEY"   # long-poll; repeat until final
curl -s $KS/v1/files/file_.../content -H "Authorization: Bearer $KEY" -o out.png
```

| `type` | default model | inputs | key parameters |
|---|---|---|---|
| `image` | `flux2-dev` (Turbo LoRA, 12 steps); `flux3-klein` (16 steps, ~3.5x faster) | `reference_images` (≤ 6) | `prompt`, `size` (WxH, ÷16), `loras`, `steps`, `seed`, `turbo` |
| `layers` | `qwen-image-layered` | `image` | `layers` (4), `prompt` (whole scene), `resolution` (640 or 1024) |
| `video` | `ltx-2.5` | `first_frame`, `last_frame` | `prompt`, `duration` (1–20 s), `size` (÷64, 1280x720), `fps`, `audio` (`none` default, or `sfx`) |
| `upscale_image` | `seedvr2-7b` | `image` | `model` (`seedvr2-7b-sharp`, `seedvr2-3b`, `flux2-refine`, `esrgan-anime`), `scale` (≤ 4) |
| `upscale_video` | `seedvr2-3b` | `video` | `model` (`seedvr2-7b`, `seedvr2-7b-sharp`, `esrgan-anime`: anime, `scale: 3` = 720p to 4K, clips < ~8 s; `esrgan-animevideo`: ~10x faster, softer), `scale` (≤ 4) |
| `speech` | `indextts-2.5` | `voice` (a 3–15 s clip to clone; optional) | `text` (≤ 20000 chars; blank line = paragraph), `voice` (`narrator-male` built in), `lang` (`EN` default, `ZH`, `JA`, `ES`, `FR`, `DE`, `KO`, `YUE`), `pause_ms` (600), `seed` |
| `remove_background` | `birefnet` | `image` | none. Outputs `mask` (white = subject) and `cutout` (RGBA) |
| `depth` | `da3-mono-large` | `image` | `resolution` (longest side the model sees, ÷14, default 1008). Outputs `depth` (near = white) and `sky` |
| `inpaint` | `lama` | `image`, `mask` (white = fill) | `auto_mask` (`foreground`: mask the subject itself instead of a `mask` input), `grow` (px, default 16) |
| `text` | `text` | `images` (the creative planner can see them) | `prompt` or `messages`, `system`, `max_tokens`, `reasoning_effort`, `json_schema` (answer is JSON matching it) |

The inputs choose the video type. With no frames it's text-to-video; with `first_frame` it's
image-to-video; with `first_frame` + `last_frame` it's a first-to-last transition. Videos are **silent** by
default; `"audio": "sfx"` adds sound effects only, never music or voices.

**Inputs** are a `file_id` (from `POST /v1/files`), a karspark file URL, or base64 (`data:` URLs are fine).
URLs elsewhere on the internet are never fetched. Upload once, and reuse the `file_id` for every job that
needs the character sheet.

**Collecting results**, best first:
1. a **webhook**. karspark POSTs `{"type": "job.succeeded", "job": {...}}` on succeeded, failed, cancelled
   and paused. It retries 3 times.
2. **long-poll** `GET /v1/jobs/{id}/wait?timeout=50`. Each call returns at once when the job ends.
3. **SSE** `GET /v1/jobs/{id}/stream`, which sends `state` and `progress` events.
4. polling `GET /v1/jobs/{id}` (every 5 s or more, please).

**`eta_s`** on queued jobs counts the work ahead of yours plus any mode switch. It improves as karspark
measures real runs.

## 5. Controlling your jobs

```
POST  /v1/jobs/{id}/pause      stops at the next sampling step; progress is kept on disk
POST  /v1/jobs/{id}/resume     continues from that step (same seed, same result)
POST  /v1/jobs/{id}/cancel     stops now, discards partial work
PATCH /v1/jobs/{id}            {"priority": 1}   (capped by your tier)
POST  /v1/jobs/cancel          {"state": "queued", "type": "video"}   bulk, your jobs only
GET   /v1/jobs/{id}/events     timeline: queued, started, paused by whom, preempted by which job, resumed
```

Only the key that created a job (or an owner key) can see or control it. Anything else gets 404.

## 6. Modes

The box runs one **mode** at a time: `coding` (Flash-Next alone, the default) or `creative` (planner LLM
plus every media model). You rarely need to care, because media jobs switch the box to `creative`
automatically when nothing more urgent is running. To ask explicitly: `POST /v1/mode {"mode": "creative",
"hold_minutes": 60}`. An owner key switches at once. Other keys get `202 {"status": "pending",
"retry_after": ..., "reason": "coding hold"}` and the switch happens by itself later. See [modes.md](modes.md).

## 7. Do / don't

- **Do** send an `Idempotency-Key` header on every job, so a retry after a timeout or Wi-Fi drop never
  makes a duplicate. The same key returns the same job.
- **Do** fix your `seed` when you need reproducible results. Jobs without one get a seed at submit time, so
  pause and resume stay exact.
- **Do** use `batch` keys for bulk work. It runs whenever the GPU is free and steps aside for urgent work.
- **Do** handle `429` (back off for `Retry-After`) and `503` text errors (the engine is restarting; retry).
- **Don't** poll faster than every 5 s, or open a new SSE stream per second.
- **Don't** name a specific text model unless you need it. `text` never waits.
- **Don't** expect files forever. Outputs are kept for your tier's retention (7 days for standard).
  Download what you keep.

## 8. MCP (agents)

Agents connect to `http://100.101.249.74:8080/mcp` (Streamable HTTP) with the same key, and get tools for
everything above plus live status. Setup for opencode and Claude Code: [mcp.md](mcp.md).

## 9. Python

`clients/python` is a small SDK (`pip install ./clients/python`, import `karspark_client`):
`Karspark().image("...").wait().save("out.png")`. See [its README](../clients/python/README.md).
