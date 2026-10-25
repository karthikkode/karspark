"""The `karspark` MCP server (Streamable HTTP at /mcp, same API keys as the HTTP API).

Generation tools submit jobs and wait up to `wait_s` (default 50 s): a finished image comes back as image
content plus its file URL; anything still running comes back as {job_id, state, eta_s} for get_job /
wait_job. Observability tools show what the box is doing right now, filtered by the caller's key
(owner keys see everything). Resources karspark://status, karspark://queue and karspark://jobs/{id} carry
the same data, with update notifications for clients that listen.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import tempfile
import time
from pathlib import Path

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.utilities.types import Image
from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated
from mcp.server.transport_security import TransportSecuritySettings

from .jobs import FINAL, JobError
from .keys import bearer
from .service import complete

INSTRUCTIONS = """karspark runs every model on the owner's DGX Spark behind one API. Full playbook: docs/agents.md.
Tools
- Text: generate_text (model "text" = whatever is loaded; it never waits for a model switch). json_schema makes
  the answer schema-valid JSON (plans, shot lists); images lets the creative planner look (check a keyframe
  against the brief). Ask about concrete things you can see ("what is the road surface made of? painted lines?"),
  not "anything out of period?", which it waves through. The planner serves 4 requests at once (4 scene plans
  in ~40 s against ~31 s for one): plan long pieces in parallel chunks.
- Images: generate_image (FLUX.2 + Turbo LoRA, 12 steps; model="flux3-klein" ~3.5x faster, but it copies reference images into every shot; for flux3-klein avoid 'painterly', 'watercolor' and 'texture' in its prompts (it paints them literally as blotchy brush strokes and paper grain); say 'smooth flat cel colours, crisp line art, no grain, no paper texture' instead), split_layers (Qwen-Image-Layered), upscale_image.
- Video: generate_video (LTX-2.5; first_frame = image-to-video, first_frame + last_frame = first-to-last
  transition; silent unless audio="sfx", which adds sound effects only, never music or voices), upscale_video.
- 2.5D (motion from stills, no video model): remove_background (character cut-out), estimate_depth
  (parallax map), inpaint_image (auto_mask="foreground" = the clean background behind them). 1-3 s each, and
  they stay loaded beside the image models, so mixing them with generate_image costs no weight swaps.
- Speech: generate_speech (IndexTTS-2.5): text -> narration WAV, built-in voice "narrator-male" or your own
  3-15 s clip as voice. This is the only source of voices: videos never get speech from the video model.
- Media tools are jobs: they wait up to wait_s (max 55), then return the job with eta_s. Continue with wait_job.
  pause_job / resume_job / cancel_job / set_priority control your own jobs; a paused job keeps its progress.
- Inputs: a file_id (upload_file once, reuse it), a karspark file URL, or base64. Outputs are karspark URLs.
- status / list_jobs / queue / job_events / list_models / usage show what is happening right now.
Working efficiently
- The GPU runs one media job at a time and one mode at a time (coding = Flash-Next only; creative = planner
  LLM + media). Media submitted in coding mode waits until the coding hold ends; status() shows it. Tell the
  user the eta instead of retrying.
- Submit every independent job first (wait_s=0), then wait_job each one. Queued work lets karspark group
  jobs by model and skip weight swaps; group your plan the same way (all images, then layers, then video).
- Draft cheap, finish expensive: small sizes and short clips while iterating, upscale only the keepers.
  Fix the seed when only the prompt changes.
- Pass idempotency_key on generation tools so a retried call never makes a duplicate job. After a dropped
  call without one, check list_jobs before resubmitting.
- Use the lowest priority that is good enough; your key's tier caps it anyway.
- Media waits for the box to cool between jobs, and a job that runs hot pauses (progress kept) and resumes by
  itself. status() shows mode.thermal_hold; long video batches take longer than the sum of job times.
- No max_tokens is needed: karspark sets no cap, and a model runs until it is done (a looping model is
  stopped). The creative planner reasons first, which can take many minutes on long prose: use
  generate_text(as_job=true) for long writing, or pass reasoning_effort="none" for the text alone."""


def build_mcp(k) -> MCPServer:
    bus = InMemorySubscriptionBus()
    mcp = MCPServer("karspark", title="karspark", instructions=INSTRUCTIONS, version="1.0", subscriptions=bus)
    mcp._karspark_bus = bus

    async def who(ctx: Context):
        p = await k.keys.verify(bearer(ctx.headers or {}))
        if not p:
            raise JobError("missing or invalid API key", 401)
        return p

    async def media(ctx, body, wait_s):
        p = await who(ctx)
        job, _ = await k.svc.submit(p, {k_: v for k_, v in body.items() if v is not None})
        view = await k.svc.wait(p, job["id"], min(float(wait_s or 0), 55))
        return await _result(k, p, view)

    # ---------------------------------------------------------------- generation
    @mcp.tool(description="Generate text with the loaded LLM (default model 'text'). Returns the answer. No "
                          "max_tokens needed (no cap). For long writing set as_job=true: it runs as a text job you can "
                          "wait_job, pause and resume, and the answer is in outputs[0].text. images (file_id/base64): "
                          "the creative planner can see them (check a keyframe against the brief). json_schema: the "
                          "answer is JSON that matches this schema, enforced while decoding (shot lists, plans).")
    async def generate_text(ctx: Context, prompt: str, system: str | None = None, max_tokens: int | None = None,
                            reasoning_effort: str | None = None, temperature: float | None = None,
                            model: str = "text", images: list[str] | None = None, json_schema: dict | None = None,
                            priority: int | None = None, as_job: bool = False,
                            wait_s: float = 50, idempotency_key: str | None = None):
        if as_job:
            return await media(ctx, {"type": "text", "prompt": prompt, "system": system, "max_tokens": max_tokens,
                                     "reasoning_effort": reasoning_effort, "temperature": temperature,
                                     "model": model, "images": images, "json_schema": json_schema,
                                     "priority": priority, "idempotency_key": idempotency_key}, wait_s)
        p = await who(ctx)
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / await k.files.resolve_input(p, x, Path(tmp), f"image_{i}", "image")
                     for i, x in enumerate(images or [])]
            return await complete(k, p, prompt=prompt, system=system, model=model, max_tokens=max_tokens,
                                  reasoning_effort=reasoning_effort, temperature=temperature, priority=priority,
                                  images=paths, json_schema=json_schema)

    @mcp.tool(description="Generate an image. model: flux2-dev (default; FLUX.2 [dev] + Turbo LoRA, 12 steps) or "
                          "flux3-klein (FLUX 3 Image Klein, 16 steps, faster). Up to 6 reference_images "
                          "(file_id/base64) for characters, outfits or style; loras for character LoRAs. size WxH.")
    async def generate_image(ctx: Context, prompt: str, size: str | None = None, reference_images: list[str] | None = None,
                             loras: list[dict] | None = None, steps: int | None = None, seed: int | None = None,
                             turbo: bool = True, model: str | None = None, priority: int | None = None,
                             idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "image", "model": model, "prompt": prompt, "size": size, "reference_images": reference_images,
                                 "loras": loras, "steps": steps, "seed": seed, "turbo": turbo, "priority": priority,
                                 "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Split an image into RGBA layers (background first) with Qwen-Image-Layered, for parallax. "
                          "prompt optionally describes the whole scene including hidden parts.")
    async def split_layers(ctx: Context, image: str, layers: int = 4, prompt: str | None = None,
                           resolution: int | None = None, seed: int | None = None, priority: int | None = None,
                           idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "layers", "image": image, "layers": layers, "prompt": prompt,
                                 "resolution": resolution, "seed": seed, "priority": priority,
                                 "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Cut the subject out of an image (BiRefNet matte). Returns two files: mask (white = "
                          "subject) and cutout (RGBA, background transparent). For 2.5D: the character layer.")
    async def remove_background(ctx: Context, image: str, priority: int | None = None,
                                idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "remove_background", "image": image, "priority": priority,
                                 "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Estimate depth (Depth Anything 3 mono-large). Returns depth (greyscale, near = white, "
                          "sky at the far end) and sky (white = sky), both at the input size. resolution: the longest "
                          "side the model sees, multiple of 14 (default 1008). For 2.5D: the parallax map.")
    async def estimate_depth(ctx: Context, image: str, resolution: int | None = None, priority: int | None = None,
                             idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "depth", "image": image, "resolution": resolution, "priority": priority,
                                 "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Fill part of an image (LaMa). Give mask (white = fill) or auto_mask='foreground' to "
                          "remove the subject in one job (the clean background behind characters for 2.5D). grow "
                          "widens the area in pixels (default 16). ~1 s; a flux3-klein edit is the slower, "
                          "better fill for big reveals.")
    async def inpaint_image(ctx: Context, image: str, mask: str | None = None, auto_mask: str | None = None,
                            grow: int | None = None, priority: int | None = None,
                            idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "inpaint", "image": image, "mask": mask, "auto_mask": auto_mask,
                                 "grow": grow, "priority": priority,
                                 "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Upscale an image. model: seedvr2-7b (default, best), seedvr2-7b-sharp, seedvr2-3b, "
                          "flux2-refine (anime-safe; optional prompt/loras), esrgan-anime (instant), esrgan-animevideo (faster, softer). scale 1-4.")
    async def upscale_image(ctx: Context, image: str, model: str = "seedvr2-7b", scale: float = 2,
                            prompt: str | None = None, loras: list[dict] | None = None, priority: int | None = None,
                            idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "upscale_image", "image": image, "model": model, "scale": scale,
                                 "prompt": prompt, "loras": loras, "priority": priority,
                                 "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Generate a video with LTX-2.5: text-to-video, image-to-video (first_frame), or "
                          "first-to-last (first_frame + last_frame). size multiple of 64, duration 1-20 s. "
                          "Silent by default; audio='sfx' adds sound effects only (never music or voices).")
    async def generate_video(ctx: Context, prompt: str, first_frame: str | None = None, last_frame: str | None = None,
                             duration: float = 5, size: str = "1280x720", fps: int = 24, audio: str = "none",
                             seed: int | None = None, priority: int | None = None,
                             idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "video", "prompt": prompt, "first_frame": first_frame, "last_frame": last_frame,
                                 "duration": duration, "size": size, "fps": fps, "audio": audio, "seed": seed,
                                 "priority": priority, "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Upscale a video, keeping fps. model: seedvr2-3b (default), seedvr2-7b (hero shots), "
                          "esrgan-anime (anime: clean lines; scale 3 takes 720p to 4K; clips under ~8 s), "
                          "esrgan-animevideo (anime video model, ~10x faster, a little softer). scale 1-4.")
    async def upscale_video(ctx: Context, video: str, model: str = "seedvr2-3b", scale: float = 2,
                            priority: int | None = None,
                            idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "upscale_video", "video": video, "model": model, "scale": scale,
                                 "priority": priority, "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Narrate text with IndexTTS-2.5 -> WAV (22.05 kHz mono). voice: 'narrator-male' (built in) "
                          "or a file_id of a 3-15 s clean speech clip to clone. lang EN (default), ZH, JA, ES, FR, DE, "
                          "KO, YUE. Blank lines are paragraph breaks (pause_ms of silence). Up to 20000 characters; "
                          "spell out numbers and abbreviations as you want them read.")
    async def generate_speech(ctx: Context, text: str, voice: str = "narrator-male", lang: str = "EN",
                              pause_ms: int = 600, seed: int | None = None, priority: int | None = None,
                              idempotency_key: str | None = None, wait_s: float = 50):
        return await media(ctx, {"type": "speech", "text": text, "voice": voice,
                                 "lang": lang, "pause_ms": pause_ms, "seed": seed, "priority": priority,
                                 "idempotency_key": idempotency_key}, wait_s)

    @mcp.tool(description="Upload a file (base64) for use as an input; returns its file_id.")
    async def upload_file(ctx: Context, name: str, data_base64: str) -> dict:
        import base64
        p = await who(ctx)
        f = await k.files.save(p, name, base64.b64decode(data_base64.split(",", 1)[-1]))
        return {"file_id": f["id"], "name": f["name"], "mime": f["mime"], "size": f["size"],
                "url": f"/v1/files/{f['id']}/content"}

    # ---------------------------------------------------------------- job control
    @mcp.tool(description="Get one of your jobs: state, progress (stage, step x/y), outputs, eta_s.")
    async def get_job(ctx: Context, job_id: str):
        p = await who(ctx)
        return await _result(k, p, await k.svc.get(p, job_id))

    @mcp.tool(description="Wait up to timeout_s (max 55) for a job to finish or pause; returns it.")
    async def wait_job(ctx: Context, job_id: str, timeout_s: float = 50):
        p = await who(ctx)
        return await _result(k, p, await k.svc.wait(p, job_id, min(float(timeout_s), 55)))

    @mcp.tool(description="Pause your job at its next safe point. Progress is kept; resume_job continues it.")
    async def pause_job(ctx: Context, job_id: str) -> dict:
        p = await who(ctx)
        return await k.svc.pause(p, job_id)

    @mcp.tool(description="Resume a paused job from its checkpoint.")
    async def resume_job(ctx: Context, job_id: str) -> dict:
        p = await who(ctx)
        return await k.svc.resume(p, job_id)

    @mcp.tool(description="Cancel a job (its partial work is discarded).")
    async def cancel_job(ctx: Context, job_id: str) -> dict:
        p = await who(ctx)
        return await k.svc.cancel(p, job_id)

    @mcp.tool(description="Change a job's priority (0 = most urgent .. 3 = batch), capped by your key's tier.")
    async def set_priority(ctx: Context, job_id: str, priority: int) -> dict:
        p = await who(ctx)
        return await k.svc.set_priority(p, job_id, priority)

    @mcp.tool(description="Ask for a mode (coding | creative | ...). Owner keys switch at once; others may be "
                          "deferred (coding hold, running work) and the switch happens automatically later.")
    async def request_mode(ctx: Context, mode: str, hold_minutes: float | None = None) -> dict:
        p = await who(ctx)
        return await k.sched.request_mode(p, mode, hold_minutes)

    # ---------------------------------------------------------------- observability
    @mcp.tool(description="What the box is doing now: mode (+hold, pending requests, ETA), engines and loaded "
                          "models, memory/GPU, running jobs and text streams, queue depth by engine and priority.")
    async def status(ctx: Context) -> dict:
        p = await who(ctx)
        return await k.svc.status(p)

    @mcp.tool(description="List jobs (yours; owner keys can pass mine=false). state: queued,running,paused,... ")
    async def list_jobs(ctx: Context, state: str | None = None, type: str | None = None, mine: bool = True,
                        limit: int = 20) -> dict:
        p = await who(ctx)
        return {"jobs": await k.svc.list(p, state, type, mine, limit)}

    @mcp.tool(description="The order the scheduler will run waiting work in, and why (priority, aging, fair share).")
    async def queue(ctx: Context, engine: str | None = None) -> dict:
        p = await who(ctx)
        return {"queue": await k.svc.queue(p, engine)}

    @mcp.tool(description="A job's timeline: queued, started, paused by whom, preempted by which job, resumed...")
    async def job_events(ctx: Context, job_id: str) -> dict:
        p = await who(ctx)
        return {"events": await k.svc.events(p, job_id)}

    @mcp.tool(description="Every model: capability, which mode serves it, loaded now or not, defaults.")
    async def list_models(ctx: Context) -> dict:
        await who(ctx)
        return {"models": k.svc.models()}

    @mcp.tool(description="Your usage (requests, tokens, jobs) since a unix time (default 30 days); owner: per project.")
    async def usage(ctx: Context, since: float | None = None) -> dict:
        p = await who(ctx)
        return await k.svc.usage(p, since)

    @mcp.tool(description="Owner only: the latest request log entries (id, project, model, status, timings).")
    async def recent_requests(ctx: Context, limit: int = 20, project: str | None = None, model: str | None = None) -> dict:
        p = await who(ctx)
        if not p.is_admin:
            raise JobError("owner only", 403)
        rows = await k.log.requests(model=model, limit=limit)
        if project:
            rows = [r for r in rows if r["project"] == project]
        return {"requests": rows}

    # ---------------------------------------------------------------- resources
    # Static resources get no caller context, so they show the view every key may see (other projects'
    # jobs anonymised); the status/queue tools give the caller's own detail.
    @mcp.resource("karspark://status", mime_type="application/json", description="Live status, anonymised.")
    async def status_res() -> str:
        return json.dumps(await k.svc.status(None), default=str)

    @mcp.resource("karspark://queue", mime_type="application/json", description="The dispatch order, anonymised.")
    async def queue_res() -> str:
        return json.dumps(await k.svc.queue(None), default=str)

    @mcp.resource("karspark://jobs/{job_id}", mime_type="application/json", description="One job, live.")
    async def job_res(job_id: str, ctx: Context) -> str:
        p = await who(ctx)
        return json.dumps(await k.svc.get(p, job_id), default=str)

    return mcp


PREVIEW_PX = 768
INLINE_BUDGET = 600 * 1024   # one MCP reply is one SSE event; clients cap it at 1 MB and base64 adds a third


def _preview(raw: bytes, mime: str):
    """A small inline copy of an output image (full size stays at its URL): JPEG, or PNG when it has alpha."""
    try:
        from PIL import Image as PImage
        im = PImage.open(io.BytesIO(raw))
        im.thumbnail((PREVIEW_PX, PREVIEW_PX))
        buf = io.BytesIO()
        if im.mode in ("RGBA", "LA", "P"):
            im.save(buf, "PNG", optimize=True)
            return buf.getvalue(), "png"
        im.convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue(), "jpeg"
    except Exception:             # not decodable: inline as is when small enough
        return raw, mime.split("/")[-1]


async def _result(k, p, view):
    """A finished image job -> small preview images + the job (full size at outputs[].url); otherwise the job."""
    if view["state"] == "succeeded" and view["kind"] in ("image", "layers", "upscale_image"):
        blocks = []
        total = 0
        for o in view["outputs"] or []:
            f = await k.files.get(p, o["file_id"])
            data, fmt = _preview(k.files.path(f).read_bytes(), o["mime"])
            if total + len(data) > INLINE_BUDGET:  # big batches: URLs only
                break
            total += len(data)
            blocks.append(Image(data=data, format=fmt))
        view = {**view, "note": f"inline images are previews (<= {PREVIEW_PX} px); full size at outputs[].url"}
        return [*blocks, json.dumps(view, default=str)]
    if view["state"] not in FINAL and view["state"] != "paused":
        view = {**view, "note": "still running; call wait_job(job_id) or get_job(job_id)"}
    return view


async def _relay(k, mcp):
    """Our event bus -> MCP resource-updated notifications."""
    bus = mcp._karspark_bus
    last_status = 0.0
    with k.bus.subscribe() as q:
        while True:
            topic, ev = await q.get()
            with contextlib.suppress(Exception):
                if topic == "job" and ev.get("job_id"):
                    await bus.publish(ResourceUpdated(uri=f"karspark://jobs/{ev['job_id']}"))
                    await bus.publish(ResourceUpdated(uri="karspark://queue"))
                if time.monotonic() - last_status > 1:  # coalesce bursts
                    last_status = time.monotonic()
                    await bus.publish(ResourceUpdated(uri="karspark://status"))


class _Auth:
    """Rejects /mcp requests without a valid key before the MCP session machinery sees them, and tags the
    request log row with the caller."""

    def __init__(self, app, k):
        self.app, self.k = app, k

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {kk.decode().lower(): v.decode() for kk, v in scope.get("headers", [])}
            p = await self.k.keys.verify(bearer(headers))
            if not p:
                body = b'{"error": {"message": "missing or invalid API key", "type": "authentication_error"}}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": body})
                return
            scope.setdefault("state", {}).setdefault("log", {}).update(key_id=p.key_id, project=p.name, tier=p.tier.name)
        await self.app(scope, receive, send)


def mount(app, k, mcp):
    """POST/GET/DELETE /mcp (exact path, no redirect). Returns the coroutine that relays notifications."""
    from starlette.routing import Route

    # Host checks are for browsers on localhost (DNS rebinding); every call here needs a key anyway.
    sub = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))
    app.router.routes.append(Route("/mcp", endpoint=_Auth(sub, k)))
    return lambda: _relay(k, mcp)
