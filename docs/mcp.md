# The `karspark` MCP server

Streamable HTTP at **`http://100.101.249.74:8080/mcp`**, with the same keys as the HTTP API. Your key sets
your tier, so the same priorities, limits and ownership apply. Every tool call appears in the request log
with its tool name. The server sends usage guidance on connect; the full playbook is
[agents.md](agents.md).

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

**Generation.** Media tools wait up to `wait_s` (default 50, max 55). A finished image comes back as a preview
image (at most 768 px, JPEG or PNG with alpha, under 600 KB in total so one reply stays below the 1 MB SSE
event limit of MCP clients) plus the job, whose `outputs[].url` is the full-size file. Anything still running comes back as the job with `eta_s`; carry on
with `wait_job`.

| tool | defaults |
|---|---|
| `generate_text(prompt, system?, max_tokens?, reasoning_effort?, temperature?, model="text", images?, json_schema?, priority?, as_job=false, wait_s=50, idempotency_key?)` | the loaded text model; returns `{text, reasoning, usage}`. No cap unless you pass `max_tokens`; gives up after 10 min. `as_job=true` runs it as a text job instead (pausable, no time limit, answer in `outputs[0].text`). `json_schema` (a JSON Schema object): the answer is JSON matching it, enforced while decoding. `images` (file_id/base64): the creative planner sees them |
| `generate_image(prompt, size?, reference_images?, loras?, steps?, seed?, turbo=true, model?, priority?, wait_s=50)` | FLUX.2 + Turbo LoRA, 12 steps; `model="flux3-klein"` is ~3.5x faster (references leak into every image; no "painterly"/"watercolor"/"texture" in prompts) |
| `split_layers(image, layers=4, prompt?, resolution?, seed?, priority?, wait_s=50)` | Qwen-Image-Layered |
| `upscale_image(image, model="seedvr2-7b", scale=2, prompt?, loras?, priority?, wait_s=50)` | also `seedvr2-7b-sharp`, `seedvr2-3b`, `flux2-refine`, `esrgan-anime` |
| `remove_background(image, priority?, wait_s=50)` | BiRefNet: two files, `mask` (white = subject) and `cutout` (RGBA). The character layer for 2.5D |
| `estimate_depth(image, resolution=1008, priority?, wait_s=50)` | Depth Anything 3 mono-large: `depth` (near = white, sky at the far end) and `sky` (white = sky), at the input size |
| `inpaint_image(image, mask?, auto_mask?, grow=16, priority?, wait_s=50)` | LaMa: fills `mask` (white = fill), or `auto_mask="foreground"` removes the subject in one job (the clean background behind the characters). Soft on big holes; a `flux3-klein` edit fills big reveals better |
| `generate_video(prompt, first_frame?, last_frame?, duration=5, size="1280x720", fps=24, audio="none", seed?, priority?, wait_s=50)` | LTX-2.5; the frames choose t2v, i2v or first-to-last; `audio="sfx"` for sound effects only |
| `upscale_video(video, model="seedvr2-3b", scale=2, priority?, wait_s=50)` | SeedVR2 (`seedvr2-7b` for hero shots); `esrgan-anime` for anime: clean lines, fast, `scale=3` takes 720p to 4K (clips under ~8 s); `esrgan-animevideo` ~10x faster, softer |
| `generate_speech(text, voice="narrator-male", lang="EN", pause_ms=600, seed?, priority?, wait_s=50)` | IndexTTS-2.5 → WAV (22.05 kHz mono). `voice` is a built-in name or the `file_id` of a clip to clone. The only source of voices: videos stay silent, so mux the narration on afterwards |
| every generation tool | also takes `idempotency_key`: a repeated call returns the same job |
| `upload_file(name, data_base64)` | returns a `file_id` to pass as any image, video or audio input |

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
3. For parallax: `remove_background`, `estimate_depth` and `inpaint_image(auto_mask="foreground")` on the
   image (or `split_layers` for Qwen-Image-Layered's RGBA layers); `upscale_image(..., model="flux2-refine")` for finals.
4. `generate_video(prompt, first_frame=<start>, last_frame=<end>)`, which returns a job, then `wait_job` until done.
5. Need the GPU for something urgent? `pause_job` the video, and `resume_job` later. Nothing is lost.
