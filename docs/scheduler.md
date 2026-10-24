# Scheduler

One asyncio loop owns all scheduling state. The policy is pure functions of (state, clock) in
`core/karspark/policy.py`, unit-tested on a fake clock (`core/tests/test_policy.py`). Settings are in
`config/scheduler.yaml` and `config/tiers.yaml`.

## Priority

- Classes P0 > P1 > P2 > P3. A request asks with `priority` (jobs) or `X-Karspark-Priority` (text).
- **Effective priority = the request, capped at the tier's ceiling** (owner P0, critical P1, standard P2,
  batch P3). Omitted = the tier's default. Both are reported (`priority`, `priority_req`).

## Dispatch order (per engine queue)

1. **Class after aging.** A job waiting `age_after_s[c]` in class c moves up one class: P3 after 30 min,
   P2 after 10 min, P1 after 3 min. Aging never reaches P0. Nothing starves.
2. **Fair share between keys inside a class.** Start-time fair queuing: each key has a virtual time that
   advances by `1/weight` per dispatch (weights: owner 8, critical 4, standard 2, batch 1). A key that was
   idle restarts at the current virtual time, so it can't bank credit while idle.
3. **Within one key:** jobs that were preempted first, then oldest first.
4. **Media affinity.** Switching ComfyUI between model families (FLUX.2, LTX, SeedVR2, Qwen-Layered) costs
   a 30–60 s weight load. Within the winning class, a job for the family already loaded may go first, but
   the job it passes over can be skipped at most `affinity_skip_budget` (3) times.

`GET /v1/queue` (MCP `queue`) shows exactly this order with the reason (`P2 (aged from P3)`,
`P1, flux2 already loaded`) and an `eta_s` for each item.

## Text

Text engines have slots (`slots:` in the recipe = vLLM `--max-num-seqs`). Waiting requests are granted
slots in the same order as above. A request naming a model of another mode waits for that mode (rules in
[modes.md](modes.md)); `text` never waits for a switch. A disconnect frees the slot and aborts generation.
Text streams are never preempted.

## Preemption

When the head of a queue is P0 or P1 from a tier with `preempt: true` (owner, critical), and the running
job on that engine is P3 (`preempt_class`), the running job is **paused**: it saves its checkpoint, goes
back to the queue marked preempted, and resumes first among its key's jobs. A job can be preempted at most
`max_preemptions` (3) times. Preemption is logged with the victim and the job that caused it.

## Lossless pause

Every pause (user, preemption, mode switch, thermal) keeps progress:

| work | safe point | what is saved |
|---|---|---|
| diffusion sampling (FLUX.2, Qwen-Layered, each LTX stage, flux2-refine) | one sampling step | latent, step index, sigmas, noise state (`KarsparkSampler` node) |
| multi-stage graphs (LTX stage 1 → upscale → stage 2) | end of a stage | the stage's output latent; finished stages are never redone |
| SeedVR2 video | one temporal chunk | finished chunks |
| text jobs | any token | the partial text; resume continues it (`continue_final_message`) |

Seeds are fixed at submit time, so a resumed job produces the same output as an unpaused one. A paused job
holds no GPU; its checkpoint stays on disk until it resumes, is cancelled, or passes retention.

## Thermal guard

GB10 boxes hard-power-off with no log when a thermal zone spikes during minutes of full GPU load. This box
did it twice, the second time on an LTX video run (lessons.md). Core reads the hottest ACPI thermal zone
(`/sys/class/thermal`) every tick. nvidia-smi's GPU temperature reads 8-15 C lower, so core doesn't use it here.

- A media job starts only while the hottest zone is below `thermal.start_below_c` (80 C), so the box
  cools between jobs. While it waits, `status` shows `mode.thermal_hold` (`since`, `hottest_c`,
  `resume_below_c`), and the log records `thermal_hold` / `thermal_release` scheduler events.
- A running media job that reaches `thermal.pause_c` (92 C) pauses at its next step, keeping its progress,
  and goes back to the queue (`paused_by: system`, reason "paused to cool"). It resumes first once the box
  is below `start_below_c`. This doesn't count as a preemption.
- Text is not gated: LLM decode is memory-bound and runs far cooler.

This is defence in depth. The main fix is the host clock cap (`host/gb10-clock-cap.service`). A spike can
outrun a 1 s check.

## ETA

Per-model job durations (EWMA of real runs) and measured mode-switch costs give `eta_s` for queued jobs:
the work ahead of the job in its queue, plus a switch if one is needed.

## Limits and safety

- Per key: requests/min and tokens/min (token buckets), max running and max queued jobs, upload size.
  Over a limit: `429` with `Retry-After`.
- `job_timeout_s` per kind (running time; paused time doesn't count). Past it the job fails.
- Engine crash: running jobs go back to the queue from their checkpoint; the engine is restarted.
- Core restart: queued and paused jobs survive (SQLite WAL); running jobs resume from their checkpoint;
  engines still running are adopted (only containers labelled `karspark.recipe=<id>`).
- Idempotency keys stop client retries from creating duplicates.
