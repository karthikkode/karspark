# karspark-client (Python)

A thin client for karspark. [docs/integrating.md](../../docs/integrating.md) is the contract; this wraps it.
One dependency (`httpx`), Python ≥ 3.10.

```bash
pip install ./clients/python          # from a checkout of this repo
export KARSPARK_API_KEY=...           # your project's key (never commit it)
export KARSPARK_BASE_URL=http://100.101.249.74:8080   # optional; this is the default
```

```python
from karspark_client import Karspark

ks = Karspark()

# text: the loaded text model ("text"); for streaming/tools use the OpenAI SDK with base_url=<karspark>/v1
plan = ks.text("Plan 6 shots for a 30 s teaser about a lighthouse keeper", system="You are a film director.")

# images (FLUX.2, Turbo LoRA, 12 steps); inputs may be local paths, bytes, file_ids or karspark URLs
sheet = ks.upload("character.png")                       # upload once, reuse the file_id
img = ks.image("the keeper on the gallery at dusk", size="1216x832", reference_images=[sheet]).wait()
img.save("shots/")                                       # -> shots/<output name>.png

layers = ks.layers(img.outputs[0]["url"], layers=4).wait()
final = ks.upscale_image(img.outputs[0]["url"], model="flux2-refine").wait()

# video (LTX-2.5): no frames = text-to-video, first_frame = image-to-video, both = first-to-last.
# Silent by default; audio="sfx" adds sound effects only.
vid = ks.video("slow push-in, waves crash below", first_frame="start.png", last_frame="end.png", duration=6)
print(vid)                                               # <Job job_... video queued P2>
vid.wait().save("clip.mp4")
```

## Job control

```python
job = ks.video("...", priority=3, webhook="https://my.app/hooks/ks", idempotency_key="shot-12-v1")
job.pause()          # stops at the next step; progress is kept on disk
job.resume()         # continues from there (same seed, same result)
job.set_priority(1)  # capped by your tier
job.cancel()
job.refresh(); job.state, job.progress, job.eta_s, job.events()
ks.jobs(state="queued,running"); ks.cancel_jobs(state="queued", type="image")
```

`wait()` long-polls, waits through system pauses (preemption, mode switches), returns early if someone
pauses the job, and raises `JobFailed` if it fails or is cancelled (`raise_on_failure=False` to inspect).

Long text you might want to pause: `ks.text_job("write chapter 3 ...").wait().text`.

## The box

`ks.status()`, `ks.queue()`, `ks.models()`, `ks.mode()`, `ks.request_mode("creative", hold_minutes=60)`,
`ks.usage()`.

## Errors and retries

Errors raise `KarsparkError` (`.status`, `.message`, `.body`). Connection drops and `429`/`502`/`503` are
retried with backoff (honouring `Retry-After`), `retries=5` by default. Every job submission sends an
`Idempotency-Key` (random unless you pass one), so a retried submit never makes a duplicate. Pass your own
`idempotency_key` to make a submission safe across process restarts too.

Tested against the real app with fake engines: `core/tests/test_client.py`.
