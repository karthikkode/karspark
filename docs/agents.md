# Using karspark from an agent

For LLM agents (Claude Code, opencode, the creative-mode planner, scripts written by agents) in **other
projects** that call karspark. It covers how to get results quickly without wasting the box. The contract
itself is [integrating.md](integrating.md); exact routes are in [api.md](api.md); MCP setup is in
[mcp.md](mcp.md). Agents changing **this repo** start at [CLAUDE.md](../CLAUDE.md) and [README.md](README.md).

The MCP server sends a short version of this page as its `instructions` on connect
(`core/karspark/mcp_server.py`). Keep the two in sync.

## The rules

1. **Use `model="text"`.** It is whatever LLM is loaded and never waits. Naming `qwen3.8-27b` while the box
   is in coding mode waits for a mode switch, which can take the whole coding hold (30 min) plus the load.
2. **Upload once, reuse the `file_id`.** Never re-send a character sheet as base64 on every job.
3. **Submit everything independent first, then wait.** ComfyUI runs one media job at a time. With a queue,
   the scheduler can run jobs for the already-loaded model first (up to 3 skips per job) and avoid 30–60 s
   weight swaps. Submit with `wait_s=0` (MCP) or no `wait` (HTTP), then `wait_job` each one.
4. **Plan by model family:** all FLUX images, then all layer splits, then all LTX videos, then all SeedVR2
   upscales. Interleaving them (image, video, image, video) forces a swap each time.
5. **Draft cheap, finish expensive.** Iterate on small images (`832x1216` or less), the default 12 steps,
   and 2–3 s clips at 768x512. Upscale or refine only what the user keeps. Fix the `seed` when only the
   prompt changes, so differences come from the prompt.
6. **Always send an idempotency key** (`Idempotency-Key` header, `idempotency_key` on MCP generation tools;
   the Python SDK does it for you). A retried call then returns the same job instead of a duplicate. After a
   dropped call without one, `list_jobs` before resubmitting.
7. **Wait with long-polls, never tight loops.** `wait_job` / `GET /v1/jobs/{id}/wait?timeout=50` returns as
   soon as the job ends. Webhooks are better still for services. Never poll faster than every 5 s.
8. **Read `eta_s` and tell the user.** If it is large (a mode switch or a long queue), say so and do other
   work. Don't cancel and resubmit; that only moves you to the back of the queue.
9. **Use the lowest priority that is good enough.** P3 (batch) for bulk and overnight work: it runs whenever
   the GPU is free and steps aside for urgent work without losing progress. Your tier caps priority anyway.
10. **Pause instead of cancel** when the work might still be wanted. A paused job keeps every finished step
    and resumes exactly.
11. **Don't set `max_tokens`.** karspark sets no cap, so the model finishes its answer however long it is.
    Only a model stuck repeating itself is stopped, with an error that says so. The planner thinks before it
    answers, and on long prose that can take many minutes: submit long writing as a text job
    (`generate_text(as_job=true)` on MCP; it survives pauses and mode switches), or pass `reasoning_effort="none"` when you want only the text.
    `progress.reasoning_chars` shows the thinking as it grows.
12. **Videos are silent; voices come from TTS.** `audio="sfx"` adds sound effects only; the video model never
    makes music or voices. For narration or dialogue use `generate_speech` (IndexTTS-2.5, built-in
    `narrator-male` or a cloned clip) and mux it onto the video. Music is never generated.

## Before a big batch: look first

`status()` (MCP) or `GET /v1/status` tells you:

| field | what to do with it |
|---|---|
| `mode.current` = `coding`, with a `hold` | media waits until the hold ends (up to 30 min after the owner's last coding request). Submit anyway, report `eta_s`, don't spin |
| `mode.switching` | a switch is loading models; text and jobs wait for it |
| queue depth by engine and priority | a long P1/P2 queue ahead of your P3 batch means hours, not minutes |
| `engines[].loaded` for ComfyUI | which model family is warm: start with jobs for that family |
| an engine in `failed` | report it to the user; retrying won't help |

`queue()` shows the exact dispatch order and why (priority, aging, fair share), so you can see where your
jobs stand.

## What happens to your request in each mode

| box is in | you send | result |
|---|---|---|
| `coding` | text, `model="text"` | served by Flash-Next at once (any key). It does not extend the coding hold |
| `coding` | a media job | queued. It runs after the hold, when the box switches to `creative` by itself; `eta_s` includes the switch |
| `coding` | `request_mode("creative")` | owner: switches now. Other keys: `202 pending`, applied by itself after the hold |
| `creative` | text, `model="text"` | the planner LLM (4 concurrent streams) |
| `creative` | text, `model="qwen3.8-flash"` | owner keys only: the box switches to coding (~4.5 min load) and running media jobs pause. Avoid unless the owner asked |
| `creative` | a media job | queued for ComfyUI, one at a time, in priority / fair-share order |

## What things cost

Measured on the box where a number is given; the rest is measured in phase 0 and filled in here.

| operation | engine | time |
|---|---|---|
| text, Flash-Next | coding mode | 70 tok/s one stream, ~100 tok/s total under load (6 concurrent streams) |
| text, planner | creative mode | Qwen3.8-27B: ~24 tok/s; a 30 s shot plan ~65 s; prompts for 4 beats ~130 s with thinking |
| switch coding → creative | | ~4.5 min (262 s measured: Flash-Next out, planner 249 s, ComfyUI 10 s) |
| switch creative → coding | | ~4.5 min (266 s measured) |
| FLUX.2 image, 12 steps, 1280x720 | ComfyUI | 29 s warm; ~65 s when FLUX must load first; ~100 s with a reference image after a swap |
| layer split, 4 layers at 640 | ComfyUI | ~165 s |
| SeedVR2-7B image upscale ×2 | ComfyUI | ~15 s warm |
| ComfyUI model-family swap (FLUX ↔ LTX ↔ SeedVR2 ↔ Qwen-Layered) | ComfyUI | 30–60 s |
| LTX-2.5 video, 3 s 768x512 | ComfyUI | ~56 s (5 s 720p: longer, not measured yet) |
| SeedVR2-3B video upscale ×2 | ComfyUI | ~300 s for a 3 s clip, 5× slower than generating it: upscale finals only |

`GET /v1/mode` reports measured switch costs (`switch_costs_s`), and `eta_s` uses measured job durations
once real runs exist.

## Patterns

**Storyboard to video (one agent, MCP).**
1. `generate_text` to write the shot list and one FLUX prompt per keyframe.
2. `upload_file` the character sheet once.
3. Submit every keyframe as `generate_image(..., reference_images=[file_id], seed=S, wait_s=0)`.
4. `wait_job` each one; show the user; regenerate rejects with the same seed and an edited prompt.
5. Submit every shot as `generate_video(prompt, first_frame=A, last_frame=B, wait_s=0)`.
6. Upscale the approved shots last (`upscale_video`), in one batch.

**Bulk generation from a service (HTTP or SDK).** Use a `batch` key, P3, a webhook and an idempotency key
per item (for example `"<dataset>-<row>-v1"`). Submit the whole set; karspark spreads it over idle time.

**Long writing you may need to pause.** Submit it as a `text` job instead of a stream. Pause keeps the
partial text; resume continues it.

**Character consistency.** Up to 6 `reference_images` per FLUX job (face, outfit, style). For a recurring
character with a trained LoRA, pass it in `loras` as well.

## When something goes wrong

| you get | it means | do |
|---|---|---|
| `400` | a bad parameter; the message names it | fix it; never retry unchanged |
| `401` / `403` | a bad key, or a model or priority your tier can't use | don't retry; tell the user |
| `404` on a job | not yours, or wrong id | check the id; you only see your own jobs |
| `409` | the job's state doesn't allow it (e.g. resume a finished job) | `get_job` and act on its real state |
| `429` | rate or open-job limit | wait `Retry-After`, then retry |
| `502` / `503` | an engine failed or is restarting | retry with backoff; running jobs resume from their checkpoint by themselves |
| job `failed` | `error` says why | fix the input if it's yours; if it's an engine error, retry once, then report |
| job `paused`, `paused_by: system` | preempted or a mode switch | nothing; it resumes by itself |
| job `paused`, `paused_by: user` | you or the owner paused it | `resume_job` when wanted |
| a stream goes quiet | waiting for a slot or a switch; keepalives keep it open | keep the read timeout at 10 min or more |

## A snippet for your project's CLAUDE.md / AGENTS.md

```markdown
## karspark (images, video, text on the DGX Spark)
- MCP server `karspark` (http://100.101.249.74:8080/mcp), key in `KARSPARK_API_KEY`. Playbook:
  docs/agents.md in the karspark repo (the MCP server also sends a summary when you connect).
- Use model "text". Upload references once and reuse the file_id. Submit all jobs first (wait_s=0),
  then wait_job each. Group work by model (images → layers → video → upscale). Draft small, upscale finals.
- Always pass idempotency_key. Report eta_s to the user instead of retrying. Videos are silent; sfx only.
```
