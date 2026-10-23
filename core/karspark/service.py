"""The operations every front door shares (HTTP API, MCP tools, dashboard): submit and control jobs,
status, queue, models, usage. Ownership and tier rules are enforced here, once."""
from __future__ import annotations

import asyncio
import time

from .api_common import INPUTS, check_webhook, default_model, job_view, new_seed, validate_media
from .jobs import FINAL, JobError, new_id

CONTROL = {"type", "kind", "priority", "webhook", "idempotency_key", "wait"}


def parse_priority(v):
    if v is None or v == "":
        return None
    if isinstance(v, str) and v.upper().startswith("P"):
        v = v[1:]
    try:
        p = int(v)
    except (TypeError, ValueError):
        raise JobError("priority must be 0..3 (or P0..P3)")
    if not 0 <= p <= 3:
        raise JobError("priority must be 0..3 (or P0..P3)")
    return p


class Service:
    def __init__(self, k):
        self.k = k  # the app context (karspark.app.Karspark)

    # ---------------------------------------------------------------- jobs
    async def submit(self, p, body: dict, request_id=None, idem_key=None) -> tuple[dict, bool]:
        """Returns (job, created). An idempotency key returns the earlier job instead of a duplicate."""
        k = self.k
        kind = body.get("type") or body.get("kind")
        if kind not in INPUTS:
            raise JobError(f"type must be one of: {', '.join(INPUTS)}")
        idem_key = idem_key or body.get("idempotency_key")
        old = await k.jobs.by_idem(p, idem_key)
        if old:
            return old, False
        if await k.jobs.count_open(p.key_id) >= p.tier.max_queued:
            raise JobError(f"too many open jobs for tier {p.tier.name} ({p.tier.max_queued})", 429)
        priority = parse_priority(body.get("priority"))
        webhook = check_webhook(body.get("webhook"))
        params = {key: v for key, v in body.items() if key not in CONTROL and key not in INPUTS[kind]}
        if kind == "text":
            model, _coding = k.sched.resolve_text_model(p, body.get("model") or "text")
            if not (params.get("prompt") or params.get("messages")):
                raise JobError("prompt or messages is required")
        else:
            model = body.get("model") or default_model(kind)
            if model not in k.cfg.capability_models(kind):
                raise JobError(f"model {model!r} can't do {kind}; use one of: {', '.join(k.cfg.capability_models(kind))}")
        params["model"] = model
        if kind != "text" and params.get("seed") is None:
            params["seed"] = new_seed()  # fixed now, so a resumed job continues the same picture
        job_id = new_id()
        d = k.jobs.dir(job_id) / "in"
        inputs = {}
        try:
            for name, (media, many) in INPUTS[kind].items():
                v = body.get(name)
                if v in (None, "", []):
                    continue
                if many:
                    if not isinstance(v, list):
                        v = [v]
                    inputs[name] = [await k.files.resolve_input(p, x, d, f"{name}_{i}", media) for i, x in enumerate(v)]
                else:
                    inputs[name] = await k.files.resolve_input(p, v, d, name, media)
            if kind != "text":
                validate_media(kind, params, inputs)
        except JobError:
            k.jobs.purge(job_id, keep_inputs=False)
            raise
        job = await k.jobs.create(p, kind, model, params, priority, inputs, idem_key, webhook, request_id, job_id)
        k.sched.poke()
        return job, True

    async def get(self, p, job_id):
        j = await self.k.jobs.owned(p, job_id)
        return job_view(j, await self.eta(j))

    async def eta(self, j):
        if j["state"] != "queued":
            return None
        for row in await self.k.sched.queue_view():
            if row.get("job_id") == j["id"]:
                return row.get("eta_s")
        return None

    async def list(self, p, state=None, kind=None, mine=True, limit=50):
        key = p.key_id if (mine or not p.is_owner) else None
        return [job_view(j) for j in await self.k.jobs.list(key, state, kind, limit)]

    async def pause(self, p, job_id):
        k = self.k
        j = await k.jobs.owned(p, job_id)
        if j["state"] == "queued":
            await k.jobs.set(job_id, "paused", actor=p.name, reason="paused while queued", paused_by="user")
        elif j["state"] == "running":
            await k.sched.command(job_id, "pause", actor=p.name, reason="paused by owner of the job")
        elif j["state"] in ("paused", "pausing"):
            pass
        else:
            raise JobError(f"can't pause a {j['state']} job", 409)
        return await self.get(p, job_id)

    async def resume(self, p, job_id):
        k = self.k
        j = await k.jobs.owned(p, job_id)
        if j["state"] == "paused":
            await k.jobs.set(job_id, "queued", actor=p.name, reason="resumed", paused_by=None, enqueued=k.clock())
            k.sched.poke()
        elif j["state"] not in ("queued", "running"):
            raise JobError(f"can't resume a {j['state']} job", 409)
        return await self.get(p, job_id)

    async def cancel(self, p, job_id):
        k = self.k
        j = await k.jobs.owned(p, job_id)
        if j["state"] in ("queued", "paused"):
            await k.jobs.set(job_id, "cancelled", actor=p.name, reason="cancelled")
            k.jobs.purge(job_id)
        elif j["state"] in ("running", "pausing", "preempting"):
            await k.sched.command(job_id, "cancel", actor=p.name, reason="cancelled")
        elif j["state"] not in ("cancelling", "cancelled"):
            raise JobError(f"can't cancel a {j['state']} job", 409)
        return await self.get(p, job_id)

    async def cancel_many(self, p, state=None, kind=None):
        states = state or "queued,paused,running"
        jobs = await self.k.jobs.list(p.key_id, states, kind, 500)
        done = []
        for j in jobs:
            try:
                await self.cancel(p, j["id"])
                done.append(j["id"])
            except JobError:
                pass
        return {"cancelled": done}

    async def set_priority(self, p, job_id, priority):
        k = self.k
        j = await k.jobs.owned(p, job_id)
        if j["state"] in FINAL:
            raise JobError(f"job is {j['state']}", 409)
        req = parse_priority(priority)
        owner = await k.keys.get(j["key_id"]) or p
        eff = owner.effective_priority(req)
        await k.jobs.set(job_id, priority_req=req, priority=eff)
        k.log.job_event(job_id, "priority", actor=p.name, reason=f"P{eff} (asked P{req})")
        k.sched.poke()
        return await self.get(p, job_id)

    async def events(self, p, job_id):
        await self.k.jobs.owned(p, job_id)
        return await self.k.log.job_events(job_id)

    async def wait(self, p, job_id, timeout=50.0):
        """Long-poll: returns when the job finishes, pauses, or `timeout` passes."""
        k = self.k
        timeout = max(0.0, min(float(timeout), 300.0))
        deadline = time.monotonic() + timeout
        with k.bus.subscribe() as q:
            while True:
                j = await k.jobs.owned(p, job_id)
                if j["state"] in FINAL or j["state"] == "paused":
                    return job_view(j)
                left = deadline - time.monotonic()
                if left <= 0:
                    return job_view(j, await self.eta(j))
                try:
                    while True:
                        topic, ev = await asyncio.wait_for(q.get(), left)
                        if topic == "job" and ev["job_id"] == job_id:
                            break
                except asyncio.TimeoutError:
                    pass

    # ---------------------------------------------------------------- status
    async def status(self, p) -> dict:
        k = self.k
        running = [job_view(j) for j in await k.jobs.active()]
        queued = await k.jobs.waiting()
        owner = p is not None and p.is_owner
        if not owner:
            running = [j if p and j["project"] == p.name else
                       {"kind": j["kind"], "model": j["model"], "state": j["state"], "priority": j["priority"],
                        "project": "(another project)", "progress": j["progress"]} for j in running]
        depth: dict = {}
        for row in await k.sched.queue_view():
            key = f"{row['engine']}/P{row['priority']}"
            depth[key] = depth.get(key, 0) + 1
        return {"mode": k.sched.mode_view(), "engines": k.engines.view(), "resources": k.resources.latest,
                "running_jobs": running, "text_streams": k.sched.streams_view(p) if p else
                [{**x, "project": "(another project)", "id": None} for x in k.sched.streams_view()],
                "queued": {"total": len(queued), "by_engine_priority": depth,
                           "mine": sum(1 for j in queued if p and j["key_id"] == p.key_id)},
                "paused": len(await k.jobs.list(None if owner else (p.key_id if p else "-"), "paused", None, 500)),
                "you": p and {"project": p.name, "tier": p.tier.name, "max_priority": f"P{p.tier.ceiling}"}}

    async def queue(self, p, engine=None):
        rows = await self.k.sched.queue_view(engine)
        if p is None or not p.is_owner:
            for r in rows:
                if p is None or r.get("project") != p.name:
                    r.update(job_id=None, stream=None, project="(another project)")
        return rows

    def models(self) -> list[dict]:
        k = self.k
        cur = k.sched.state.current
        out = [{"id": "text", "object": "model", "owned_by": "karspark", "capability": "text",
                "serves": k.cfg.modes[cur].text, "modes": list(k.cfg.modes), "loaded": True,
                "note": "whichever text model the current mode has loaded; never waits for a switch"}]
        defaults = {"image": default_model("image"), "layers": default_model("layers"), "video": default_model("video"),
                    "upscale_image": default_model("upscale_image"), "upscale_video": default_model("upscale_video")}
        for model, rids in k.cfg.model_index.items():
            r = k.cfg.recipes[rids[0]]
            caps = [c for c in r.capabilities if c == "text" or model in k.cfg.capability_models(c)]
            if r.kind == "media":
                caps = [c for c in r.capabilities if model in default_model_list(c)]
            modes = k.cfg.mode_of_model(model)
            out.append({"id": model, "object": "model", "owned_by": "karspark", "capability": caps, "modes": modes,
                        "loaded": any(k.engines.ready(rid) for rid in rids) and cur in modes,
                        "default_for": [c for c, m in defaults.items() if m == model]})
        return out

    async def usage(self, p, since=None):
        since = since or time.time() - 30 * 86400
        rows = await self.k.log.usage(since, None if p.is_owner else p.key_id)
        return {"since": since, "rows": rows}


def default_model_list(cap):
    from .api_common import workflows
    return workflows().MODELS.get(cap, [])


async def complete(k, p, prompt=None, system=None, messages=None, model="text", max_tokens=None,
                   reasoning_effort=None, temperature=None, priority=None, timeout=600.0) -> dict:
    """One non-streamed chat completion through the scheduler (MCP generate_text, the dashboard)."""
    import uuid

    model, coding = k.sched.resolve_text_model(p, model or "text")
    msgs = list(messages or [])
    if not msgs:
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt or ""})
    prio = 0 if coding else p.effective_priority(parse_priority(priority))
    eng, s = await asyncio.wait_for(k.sched.acquire_text(p, model, prio, "mcp_" + uuid.uuid4().hex[:10], coding), timeout)
    try:
        body = {"model": eng.served_name, "messages": msgs, "max_tokens": int(max_tokens or eng.max_tokens_default)}
        if temperature is not None:
            body["temperature"] = float(temperature)
        if reasoning_effort:
            body["chat_template_kwargs"] = {"enable_thinking": reasoning_effort not in ("none", "off")}
        r = await k.http.post(eng.base_url + "/v1/chat/completions", json=body, timeout=timeout)
    finally:
        k.sched.release_text(s)
    if r.status_code != 200:
        raise JobError(f"text engine said {r.status_code}: {r.text[:500]}", 502)
    data = r.json()
    msg = data["choices"][0]["message"]
    usage = data.get("usage") or {}
    k.ratelimit.charge_tokens(p, usage.get("total_tokens", 0))
    return {"text": msg.get("content") or "", "reasoning": msg.get("reasoning_content") or msg.get("reasoning"),
            "tool_calls": msg.get("tool_calls"), "model": eng.served_name, "usage": usage,
            "finish_reason": data["choices"][0].get("finish_reason")}
