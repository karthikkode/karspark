# The `karspark` MCP server

Streamable HTTP at **`http://100.101.249.74:8080/mcp`**, with the same keys as the HTTP API. Your key sets
your tier, so the same priorities, limits and ownership apply. Every tool call appears in the request log
with its tool name.

## Connect

**Claude Code**

```bash
claude mcp add --transport http karspark http://100.101.249.74:8080/mcp --header "Authorization: Bearer $KARSPARK_API_KEY"
```

**opencode** (`~/.config/opencode/opencode.jsonc`)

```jsonc
"mcp": {
  "karspark": {
    "type": "remote",
    "url": "http://100.101.249.74:8080/mcp",
    "headers": { "Authorization": "Bearer {env:KARSPARK_API_KEY}" }
  }
}
```

Any other client: Streamable HTTP, header `Authorization: Bearer <key>`.

## Tools

**Generation.** Media tools wait up to `wait_s` (default 50, max 55). A finished image comes back as image
content plus the job (with file URLs). Anything still running comes back as the job with `eta_s`; carry on
with `wait_job`.

| tool | defaults |
|---|---|
| `generate_text(prompt, system?, max_tokens?, reasoning_effort?, temperature?, model="text", priority?)` | the loaded text model; returns `{text, reasoning, usage}` |
| `generate_image(prompt, size?, reference_images?, loras?, steps?, seed?, turbo=true, priority?, wait_s=50)` | FLUX.2 + Turbo LoRA, 12 steps |
| `split_layers(image, layers=4, prompt?, resolution?, seed?, priority?, wait_s=50)` | Qwen-Image-Layered |
| `upscale_image(image, model="seedvr2-7b", scale=2, prompt?, loras?, priority?, wait_s=50)` | also `seedvr2-7b-sharp`, `seedvr2-3b`, `flux2-refine`, `esrgan-anime` |
| `generate_video(prompt, first_frame?, last_frame?, duration=5, size="1280x720", fps=24, audio="none", seed?, priority?, wait_s=50)` | LTX-2.5; the frames choose t2v, i2v or first-to-last; `audio="sfx"` for sound effects only |
| `upscale_video(video, model="seedvr2-3b", scale=2, priority?, wait_s=50)` | SeedVR2 |
| `upload_file(name, data_base64)` | returns a `file_id` to pass as any image or video input |

**Job control** (your own jobs; owner keys: any job)

`get_job(job_id)` · `wait_job(job_id, timeout_s=50)` · `pause_job(job_id)` · `resume_job(job_id)` ·
`cancel_job(job_id)` · `set_priority(job_id, priority)` · `request_mode(mode, hold_minutes?)`

**Observability**

| tool | returns |
|---|---|
| `status()` | mode (since, hold, pending requests), engines and loaded model family, memory/GPU, running jobs and text streams, queue depth by engine and priority |
| `list_jobs(state?, type?, mine=true, limit=20)` | jobs with priority, progress (stage, step x/y), `eta_s` |
| `queue(engine?)` | the order the scheduler will run waiting work in, and why |
| `job_events(job_id)` | a job's timeline |
| `list_models()` | every model, its capability and mode, whether it is loaded, defaults |
| `usage(since?)` | your requests, tokens and jobs (owner: per project) |
| `recent_requests(limit=20, project?, model?)` | owner only: the latest request log rows |

## Resources

| uri | content |
|---|---|
| `karspark://status` | live status, other projects anonymised |
| `karspark://queue` | the dispatch order, anonymised |
| `karspark://jobs/{job_id}` | one of your jobs |

Clients that listen (`subscriptions/listen`) get `resource updated` notifications when jobs change state
and when the status changes (coalesced to at most one a second).

## A typical agent loop

1. `generate_text` to plan the shots (the planner LLM is always loaded in creative mode).
2. `upload_file` for the character sheet → `generate_image(prompt, reference_images=[file_id])`.
3. `split_layers(image=<output url>)` for parallax, `upscale_image(..., model="flux2-refine")` for finals.
4. `generate_video(prompt, first_frame=<start>, last_frame=<end>)`, which returns a job, then `wait_job` until done.
5. Need the GPU for something urgent? `pause_job` the video, and `resume_job` later. Nothing is lost.
