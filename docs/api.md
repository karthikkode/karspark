# karspark HTTP API

Base URL `http://100.101.249.74:8080`. Every route except `/health` and `/ui` needs a key:
`Authorization: Bearer <key>` (or `x-api-key`). Errors are OpenAI-shaped:
`{"error": {"message": "...", "type": "invalid_request_error"}}`. Every response carries `X-Request-Id`,
which matches the request log. A machine-readable schema is in [openapi.json](openapi.json) (`scripts/export-openapi.sh`); the
interactive version is at `/docs`.

| status | meaning |
|---|---|
| 400 | bad parameters (the message says which) |
| 401 / 403 | no or bad key / not allowed for your tier or key |
| 404 | no such job or file, or it isn't yours |
| 409 | the job's state doesn't allow that (e.g. resume a succeeded job) |
| 413 | upload larger than your tier allows |
| 429 | rate limit or open-job limit; honour `Retry-After` |
| 502 / 503 | the engine failed or is restarting; retry |

## Text (OpenAI / Anthropic compatible)

| route | notes |
|---|---|
| `POST /v1/chat/completions` | OpenAI chat; `stream` supported |
| `POST /v1/completions` | OpenAI completions |
| `POST /v1/responses` | OpenAI Responses |
| `POST /v1/messages` | Anthropic Messages; `stream` supported |
| `POST /v1/messages/count_tokens` | Anthropic |

- `model`: `text` (the loaded mode's text model, never waits), or a model name from `GET /v1/models`.
- Priority: header `X-Karspark-Priority: 0..3` or `P0..P3`, or a `priority` field (it is removed before the
  request reaches the engine). Echoed back in the `X-Karspark-Priority` response header.
- `max_tokens` defaults per model when omitted (Flash-Next 16384, planner 8192).
- The body is otherwise passed through to vLLM: `tools`, `response_format`, `chat_template_kwargs`
  (`{"enable_thinking": true}`), `reasoning_effort`, and so on.
- Streams start immediately and send keepalives (`: keepalive` comments, or Anthropic `ping` events) while
  waiting for a slot or a mode switch, and whenever the engine is silent for 15 s. Disconnecting frees the
  slot and aborts generation.

## Jobs

### `POST /v1/jobs`

```jsonc
{
  "type": "image",                    // image | layers | video | upscale_image | upscale_video | text
  "model": "flux2-dev",               // optional: the type's default
  "priority": 2,                      // optional: your tier's default; capped at its ceiling
  "webhook": "https://...",           // optional: POSTed on succeeded/failed/cancelled/paused
  "idempotency_key": "...",           // optional (or the Idempotency-Key header)
  "wait": 30,                         // optional: hold the response up to N s (max 300) for the result
  // ...type-specific parameters and inputs (see integrating.md section 4)
}
```

Returns `201` with the job (`200` with the existing job for a repeated idempotency key). Parameters are
checked against the real graph builder at submit time, so mistakes are a `400` now, not a failed job later.

### The job object

```jsonc
{
  "object": "job", "id": "job_3f9c...", "kind": "video", "model": "ltx-2.5",
  "state": "running",                 // queued | running | pausing | paused | preempting | cancelling
                                      // | succeeded | failed | cancelled
  "priority": 2, "priority_req": 1,   // effective (after the ceiling and aging) / as asked
  "project": "my-project", "created": 1792736000.1, "started": 1792736120.4, "finished": null,
  "run_s": 41.2,                      // GPU time so far (paused time not counted)
  "progress": {"stage": "ltx-s1.0", "step": 5, "total": 8},
  "outputs": [{"file_id": "file_...", "name": "video_00001_.mp4", "mime": "video/mp4", "size": 2311042,
               "url": "/v1/files/file_.../content"}],
  "error": null, "preemptions": 0, "paused_by": null, "attempts": 1,
  "params": {...}, "eta_s": 95       // eta_s only while queued
}
```

`paused_by` is `user` (you or an owner paused it) or `system` (preempted, or the mode switched; it resumes
by itself). Text jobs: `outputs[0].text` holds the answer, and `progress.partial_chars` grows while it runs.

| route | |
|---|---|
| `GET /v1/jobs?state=queued,running&type=video&limit=50&mine=true` | list (owner keys: `mine=false` for all projects) |
| `GET /v1/jobs/{id}` | one job |
| `GET /v1/jobs/{id}/wait?timeout=50` | long-poll until final or paused (max 300 s) |
| `GET /v1/jobs/{id}/stream` | SSE: `event: state` (job object), `event: progress` |
| `GET /v1/jobs/{id}/events` | timeline `[{t, state, actor, reason, data}]` |
| `POST /v1/jobs/{id}/pause` · `/resume` · `/cancel` | control |
| `PATCH /v1/jobs/{id}` `{"priority": 1}` | reprioritise |
| `POST /v1/jobs/cancel` `{"state": "queued", "type": "image"}` | bulk-cancel your jobs |

### `POST /v1/images/generations` (OpenAI SDK compatible)

This runs an `image` job and waits for it. It returns `{"created", "data": [{"b64_json", "url", "file_id",
"seed"}]}`, or `202` with the job list if it doesn't finish within `timeout` (default 300 s). It accepts
`n` (≤ 4), `size`, `response_format` (`b64_json` | `url`), `image` (a reference image) and the `image` job
parameters.

## Files

| route | |
|---|---|
| `POST /v1/files` | multipart field `file`, or JSON `{"name", "data": "<base64>"}` → `{"file_id", "url", "mime", "size", "expires"}` |
| `GET /v1/files` | your files, newest first |
| `GET /v1/files/{id}` · `/content` | metadata · bytes |
| `DELETE /v1/files/{id}` | delete |

Files and job outputs are kept for your tier's `retention_days`.

## Mode, status, models, usage

| route | |
|---|---|
| `GET /v1/mode` | `{current, since, switching, hold: {kind, ends_in_s}, pending, text_model, switch_costs_s, idle_return_in_s}` |
| `POST /v1/mode` `{"mode", "hold_minutes"?}` | `200 active` · `202 switching` · `202 pending {retry_after, reason}` · `403` (coding without a coding key) |
| `GET /v1/status` | mode, engines (state, loaded model family, load time, error), resources (memory, GPU), running jobs, text streams, queue depth by engine and priority |
| `GET /v1/queue?engine=` | dispatch order with `why` (priority, aging, fair share) and `eta_s`; other projects are anonymised |
| `GET /v1/models` | every model: capability, modes, loaded, `default_for` |
| `GET /v1/usage?since=` | requests, tokens and job counts (owner: per project) |

## Admin (owner keys)

| route | |
|---|---|
| `GET/POST /admin/keys` | list / create `{"name", "tier", "scopes"?}` (the secret is returned once) |
| `POST /admin/keys/{id}/rotate` · `/revoke`, `PATCH /admin/keys/{id}` `{"tier"}` | manage |
| `GET /admin/logs/requests?key=&model=&since=&status=&path=&limit=` | request log |
| `GET /admin/logs/requests/{request_id}` | one request with full bodies |
| `GET /admin/logs/scheduler?since=` | mode switches, preemptions, rate limits, key changes |
| `POST /admin/engines/{recipe}/restart` | restart an engine of the current mode |
| `GET /metrics` | Prometheus text |
