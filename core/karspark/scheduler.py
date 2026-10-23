"""Scheduler runtime: owns the GPU. One asyncio loop decides the mode, starts and stops engines, dispatches
media jobs, leases text slots, preempts and enforces timeouts. All rules live in policy.py.

Everything that changes state runs on the event loop, so there are no locks around scheduler state; the
only lock is the engine manager's, which serialises container starts and stops.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from . import policy
from .config import Config, Recipe
from .engines import READY, EngineError
from .jobs import JobError
from .policy import FairShare, Item, ModeState


@dataclass
class TextWaiter:
    item: Item
    model: str
    coding: bool
    fut: asyncio.Future
    project: str


@dataclass
class Stream:
    id: str
    project: str
    model: str
    engine: str
    priority: int
    started: float
    tokens: int = 0
    coding: bool = False


CANCEL_FORCE_S = 120


@dataclass
class Run:
    job: dict
    engine: str
    task: asyncio.Task | None = None
    command: str | None = None        # pause | preempt | cancel | switch
    reason: str | None = None
    actor: str | None = None
    started: float = field(default_factory=time.time)
    command_t: float = 0.0            # when the command was given (a cancel the engine never confirms is forced)


class Scheduler:
    def __init__(self, cfg: Config, db, jobs, files, engines, log, bus, keys, workers, clock=time.time,
                 tick_s=1.0):
        self.cfg, self.db, self.jobs, self.files, self.engines = cfg, db, jobs, files, engines
        self.log, self.bus, self.keys, self.workers, self.clock, self.tick_s = log, bus, keys, workers, clock, tick_s
        s = cfg.sched
        self.coding_mode = next((m.name for m in cfg.modes.values() if m.coding), None)
        self.state = ModeState(current=s.default_mode, since=clock(), last_activity=clock())
        self.switching: str | None = None
        self.switch_reason: str | None = None
        self._tick_lock = asyncio.Lock()  # the loop and request_mode() both tick; one at a time (one switch)
        self.fair_media = FairShare()
        self.fair_text: dict[str, FairShare] = {}
        self.text_waiters: list[TextWaiter] = []
        self.streams: dict[str, Stream] = {}
        self.text_active: dict[str, int] = {}
        self.runs: dict[str, Run] = {}
        self.durations: dict[str, float] = {}     # "kind:model" -> EWMA seconds
        self.switch_costs: dict[str, float] = {}  # target mode -> seconds
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.switch_task: asyncio.Task | None = None
        self.started = False

    # ---------------------------------------------------------------- lifecycle
    async def start(self, adopt=True):
        saved = await self.db.kv_get("mode")
        if saved and saved.get("current") in self.cfg.modes:
            self.state = ModeState(**{**saved, "pending": saved.get("pending")})
        self.durations = await self.db.kv_get("durations", {})
        self.switch_costs = await self.db.kv_get("switch_costs", {})
        await self.jobs.recover()
        if adopt:
            await self.engines.adopt()
        running = {rid for rid, e in self.engines.engines.items() if e.state == READY}
        match = next((m for m in self.cfg.modes.values() if running and set(m.engines) == running), None)
        if match:
            self.state.current = match.name
        else:
            alive = [rid for rid, e in self.engines.engines.items() if await self.engines.runner.running(e.recipe)]
            if not alive and self.state.current != self.cfg.sched.default_mode:
                # a cold boot (no engine survived): start in the default mode, not wherever we were
                self.log.scheduler_event("cold_start", saved=self.state.current, mode=self.cfg.sched.default_mode)
                self.state.current, self.state.pending, self.state.pinned_until = self.cfg.sched.default_mode, None, 0
            self._begin_switch(self.state.current, "startup")
        self.task = asyncio.create_task(self._loop())
        self.started = True

    async def stop(self):
        for t in (self.task, self.switch_task, *[r.task for r in self.runs.values()]):
            if t:
                t.cancel()
        await self._persist()

    async def _persist(self):
        st = self.state
        await self.db.kv_set("mode", {"current": st.current, "since": st.since, "last_coding": st.last_coding,
                                      "last_activity": st.last_activity, "pinned_until": st.pinned_until,
                                      "pending": st.pending})
        await self.db.kv_set("durations", self.durations)
        await self.db.kv_set("switch_costs", self.switch_costs)

    def poke(self):
        self.wake.set()

    async def _loop(self):
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), self.tick_s)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            try:
                await self.tick()
            except Exception as e:  # the loop must survive anything
                self.log.scheduler_event("error", error=repr(e))

    # ---------------------------------------------------------------- the decision step
    async def tick(self):
        async with self._tick_lock:
            await self._tick()

    async def _tick(self):
        now = self.clock()
        if self.runs or self.streams:
            self.state.last_activity = now
        await self._timeouts(now)
        if self.switching:
            return
        queued = await self.jobs.waiting()
        demand = self._demand(queued)
        d = policy.decide_mode(self.state, demand, now, self.coding_mode, self.cfg.sched.default_mode,
                               self.cfg.sched.coding_hold_s, self.cfg.sched.idle_return_s)
        if d:
            self._begin_switch(*d)
            return
        await self._dispatch_media(queued, now)
        self._dispatch_text_jobs(queued)
        self._grant_text()

    def _model_mode_ok(self, model) -> bool:
        return self.cfg.engine_for(self.state.current, model) is not None

    def _modes_for(self, model) -> list[str]:
        return [m for m in self.cfg.mode_of_model(model) if m != self.coding_mode or model == self.cfg.modes[m].text]

    def _demand(self, queued) -> policy.Demand:
        waiting: dict[str, list[int]] = {}
        now = self.clock()
        for j in queued:
            if j["model"] == "text":
                continue  # follows whatever mode is loaded
            p = policy.aged_class(self._item(j), now, self.cfg.sched.age_after_s)
            for m in self.cfg.mode_of_model(j["model"]):
                if m != self.coding_mode:
                    waiting.setdefault(m, []).append(p)
        coding_waiting = False
        for w in self.text_waiters:
            if w.coding and self.coding_mode:
                coding_waiting = True
            elif w.model != "text":
                for m in self.cfg.mode_of_model(w.model):
                    if m != self.coding_mode:
                        waiting.setdefault(m, []).append(w.item.priority)
        running_here = len(self.runs) + len(self.streams)
        return policy.Demand(waiting=waiting, running_here=running_here, coding_waiting=coding_waiting)

    def _item(self, j) -> Item:
        tier = self.cfg.tiers.get(j.get("_tier") or "", None)
        return Item(id=j["id"], key_id=j["key_id"], weight=j.get("_weight") or (tier.weight if tier else 1),
                    priority=j["priority"], enqueued=j["enqueued"], created=j["created"], model=j["model"],
                    skipped=j["skipped"], preempted=j.get("paused_by") == "system")

    async def _weights(self, jobs):
        for j in jobs:
            if "_weight" not in j:
                p = await self.keys.get(j["key_id"])
                j["_weight"] = p.tier.weight if p else 1
                j["_can_preempt"] = bool(p and p.tier.preempt)

    # ---------------------------------------------------------------- media
    async def _dispatch_media(self, queued, now):
        eng: Recipe | None = self.cfg.media_engine(self.state.current)
        if not eng or not self.engines.ready(eng.id):
            return
        mine = [j for j in queued if j["kind"] != "text" and j["model"] in eng.models]
        if not mine:
            return
        await self._weights(mine)
        busy = [r for r in self.runs.values() if r.engine == eng.id]
        if busy:
            await self._maybe_preempt(busy[0], mine, now)
            return
        e = self.engines.engines[eng.id]
        p = policy.pick([self._item(j) for j in mine], now, self.cfg.sched.age_after_s, self.fair_media,
                        e.loaded_model, self.cfg.sched.affinity_skip_budget)
        job = next(j for j in mine if j["id"] == p.item.id)
        if p.bypassed:
            await self.db.update("jobs", "id", p.bypassed.id, {"skipped": p.bypassed.skipped + 1})
        self.fair_media.charge(job["key_id"], job["_weight"])
        await self._start_run(job, eng, p.reason)

    async def _maybe_preempt(self, run: Run, queued, now):
        if run.command:
            return
        p = policy.pick([self._item(j) for j in queued], now, self.cfg.sched.age_after_s, self.fair_media)
        head = next(j for j in queued if j["id"] == p.item.id)
        cls = policy.aged_class(p.item, now, self.cfg.sched.age_after_s)
        cur = await self.jobs.get(run.job["id"])
        if cur and policy.should_preempt(cur["priority"], cur["preemptions"], cls, head["_can_preempt"],
                                         self.cfg.sched.preempt_class, self.cfg.sched.max_preemptions):
            self.log.scheduler_event("preempt", victim=run.job["id"], by=head["id"], priority=cls)
            await self.command(run.job["id"], "preempt", actor="karspark",
                               reason=f"preempted by {head['id']} (P{cls}, {head['project']})")

    async def _start_run(self, job, eng: Recipe, reason):
        now = self.clock()
        await self.jobs.set(job["id"], "running", actor="karspark", reason=reason, started=job["started"] or now,
                            attempts=job["attempts"] + 1, paused_by=None)
        run = Run(job={**job, "state": "running"}, engine=eng.id, started=now)
        self.runs[job["id"]] = run
        run.task = asyncio.create_task(self._run_job(run, eng))

    async def _run_job(self, run: Run, eng: Recipe):
        job = run.job
        try:
            result = await self.workers.run(job, eng, run)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            result = {"state": "failed", "error": {"message": f"worker error: {e!r}"}}
        finally:
            self.runs.pop(job["id"], None)
        elapsed = self.clock() - run.started
        cur = await self.jobs.get(job["id"]) or job
        run_s = (cur.get("run_s") or 0) + elapsed
        st = result["state"]
        if st == "succeeded":
            k = f"{job['kind']}:{job['model']}"
            self.durations[k] = round(run_s if k not in self.durations else 0.7 * self.durations[k] + 0.3 * run_s, 1)
            await self.jobs.set(job["id"], "succeeded", actor="karspark", run_s=run_s, outputs=result.get("outputs"),
                                progress=result.get("progress"), data={"run_s": round(run_s, 1)})
            self.jobs.drop_checkpoints(job["id"])
        elif st == "paused":
            if run.command == "pause":
                await self.jobs.set(job["id"], "paused", actor=run.actor, reason=run.reason, run_s=run_s,
                                    paused_by="user", progress=result.get("progress"))
            else:  # preempt or mode switch: back in the queue, resumes first within its key
                extra = {"preemptions": cur["preemptions"] + 1} if run.command == "preempt" else {}
                await self.jobs.set(job["id"], "queued", actor=run.actor or "karspark", reason=run.reason, run_s=run_s,
                                    paused_by="system", enqueued=self.clock(), progress=result.get("progress"), **extra)
        elif st == "cancelled":
            await self.jobs.set(job["id"], "cancelled", actor=run.actor, reason=run.reason, run_s=run_s)
            self.jobs.purge(job["id"])
        elif st == "lost":  # engine crashed or restarted mid-job: retry from checkpoint
            if cur["attempts"] >= 3:
                await self.jobs.set(job["id"], "failed", actor="karspark", run_s=run_s,
                                    error={"message": "engine failed 3 times running this job"})
            else:
                await self.jobs.set(job["id"], "queued", actor="karspark", reason=result.get("error", "engine lost"),
                                    run_s=run_s, paused_by="system", enqueued=self.clock())
        else:
            await self.jobs.set(job["id"], "failed", actor="karspark", run_s=run_s, error=result.get("error"))
        await self.workers.notify(await self.jobs.get(job["id"]))
        self.poke()

    async def command(self, job_id, cmd, actor, reason=None):
        """pause | preempt | cancel | switch a running job (the worker carries it out at a safe point)."""
        run = self.runs.get(job_id)
        if not run:
            return False
        if run.command == "cancel":
            return True
        run.command, run.actor, run.reason, run.command_t = cmd, actor, reason, self.clock()
        state = {"pause": "pausing", "cancel": "cancelling"}.get(cmd, "preempting")
        await self.jobs.set(job_id, state, actor=actor, reason=reason)
        await self.workers.signal(run)
        return True

    async def _timeouts(self, now):
        for run in list(self.runs.values()):
            j = await self.jobs.get(run.job["id"])
            if j and run.command == "cancel" and now - run.command_t > CANCEL_FORCE_S:
                # the engine never confirmed (e.g. ComfyUI dropped the interrupt): stop waiting, free the slot
                if run.task:
                    run.task.cancel()
                self.runs.pop(j["id"], None)
                await self.jobs.set(j["id"], "cancelled", actor=run.actor or "karspark",
                                    reason=f"{run.reason or 'cancelled'}; forced after {CANCEL_FORCE_S}s without engine confirmation")
                self.jobs.purge(j["id"])
                await self.workers.notify(await self.jobs.get(j["id"]))
                continue
            if not j or run.command:
                continue
            limit = self.cfg.sched.job_timeout_s.get(j["kind"], 3600)
            if (j["run_s"] or 0) + (now - run.started) > limit:
                await self.command(j["id"], "cancel", actor="karspark", reason=f"timed out after {limit}s")
                run.reason = f"timed out after {limit}s"

    # ---------------------------------------------------------------- text
    def resolve_text_model(self, principal, model: str) -> tuple[str, bool]:
        """Returns (model, is_coding). Raises JobError for unknown or forbidden models."""
        model = model or "text"
        if model == "text":
            return "text", False
        modes = self.cfg.mode_of_model(model)
        if not modes or not any(self.cfg.recipes[r].kind == "text" for r in self.cfg.model_index.get(model, [])):
            raise JobError(f"unknown text model {model!r}; use 'text' (whatever is loaded) or GET /v1/models", 404)
        coding = self.coding_mode in modes and len(modes) == 1
        if coding and not principal.can_code:
            raise JobError(f"{model} is reserved for coding keys; use model 'text'", 403)
        return model, coding

    async def acquire_text(self, principal, model, priority, stream_id, coding=False) -> tuple[Recipe, Stream]:
        """Wait (in policy order) for a slot on the engine serving `model` in the current mode.
        Cancelling the wait (client gone) is safe. Pair with release_text()."""
        now = self.clock()
        if coding:
            self.state.last_coding = now
            priority = 0
        self.state.last_activity = now
        item = Item(id=stream_id, key_id=principal.key_id, weight=principal.tier.weight, priority=priority,
                    enqueued=now, created=now, model=model)
        w = TextWaiter(item, model, coding, asyncio.get_running_loop().create_future(), principal.name)
        self.text_waiters.append(w)
        self.poke()
        try:
            eng: Recipe = await w.fut   # the slot is counted in text_active when granted
        except asyncio.CancelledError:
            if w.fut.done() and not w.fut.cancelled():
                self.text_active[w.fut.result().id] -= 1
                self.poke()
            raise
        finally:
            if w in self.text_waiters:
                self.text_waiters.remove(w)
        s = Stream(stream_id, principal.name, model, eng.id, priority, self.clock(), coding=coding)
        self.streams[stream_id] = s
        return eng, s

    def release_text(self, s: Stream):
        if self.streams.pop(s.id, None) is None:
            return
        self.text_active[s.engine] -= 1
        if s.coding:
            self.state.last_coding = self.clock()
        self.state.last_activity = self.clock()
        self.poke()

    @contextlib.asynccontextmanager
    async def text_slot(self, principal, model, priority, stream_id, coding=False):
        eng, s = await self.acquire_text(principal, model, priority, stream_id, coding)
        try:
            yield eng, s
        finally:
            self.release_text(s)

    def _text_engine(self, model) -> Recipe | None:
        mode = self.cfg.modes[self.state.current]
        name = mode.text if model == "text" else model
        if not name:
            return None
        eng = self.cfg.engine_for(mode.name, name)
        return eng if eng and self.engines.ready(eng.id) else None

    def _grant_text(self):
        if self.switching or not self.text_waiters:
            return
        now = self.clock()
        by_engine: dict[str, list[TextWaiter]] = {}
        for w in self.text_waiters:
            if w.fut.done():
                continue
            eng = self._text_engine(w.model)
            if eng:
                by_engine.setdefault(eng.id, []).append(w)
        for rid, ws in by_engine.items():
            eng = self.cfg.recipes[rid]
            fair = self.fair_text.setdefault(rid, FairShare())
            while ws and self.text_active.get(rid, 0) < eng.slots:
                p = policy.pick([w.item for w in ws], now, self.cfg.sched.age_after_s, fair)
                w = next(x for x in ws if x.item is p.item)
                ws.remove(w)
                fair.charge(w.item.key_id, w.item.weight)
                self.text_active[rid] = self.text_active.get(rid, 0) + 1
                w.fut.set_result(eng)

    def _dispatch_text_jobs(self, queued):
        for j in queued:
            if j["kind"] == "text" and j["id"] not in self.runs and (j["model"] == "text" or self._model_mode_ok(j["model"])):
                run = Run(job=j, engine="", started=self.clock())
                self.runs[j["id"]] = run
                run.task = asyncio.create_task(self._run_text_job(run))

    async def _run_text_job(self, run: Run):
        """Async text job: waits for a text slot like any request, then runs; pausable (keeps partial text)."""
        j = run.job
        p = await self.keys.get(j["key_id"])
        if p is None:
            self.runs.pop(j["id"], None)
            await self.jobs.set(j["id"], "failed", actor="karspark", error={"message": "key revoked"})
            return
        try:
            async with self.text_slot(p, j["model"], j["priority"], j["id"]) as (eng, _s):
                await self.jobs.set(j["id"], "running", actor="karspark", reason=f"slot on {eng.id}",
                                    started=j["started"] or self.clock(), attempts=j["attempts"] + 1, paused_by=None)
                run.engine, run.started = eng.id, self.clock()
                run.job = {**j, "state": "running"}
                self.runs[j["id"]] = run
                await self._run_job(run, eng)
        except asyncio.CancelledError:
            self.runs.pop(j["id"], None)
            raise

    # ---------------------------------------------------------------- modes
    async def request_mode(self, principal, mode, hold_minutes=None, priority=None) -> dict:
        if mode not in self.cfg.modes:
            raise JobError(f"unknown mode {mode!r}; modes: {', '.join(self.cfg.modes)}", 404)
        if self.cfg.modes[mode].coding and not principal.can_code:
            raise JobError("only coding keys can enter coding mode", 403)
        now = self.clock()
        immediate = principal.tier.mode_change == "immediate"
        prio = principal.effective_priority(priority)
        if hold_minutes:
            hold_minutes = max(0, min(float(hold_minutes), 24 * 60))
        if mode == self.state.current and not self.switching:
            if hold_minutes and (immediate or mode != self.coding_mode):
                self.state.pinned_until = now + hold_minutes * 60
            self.state.pending = None
            return {"status": "active", "mode": mode}
        self.state.pending = {"mode": mode, "key_id": principal.key_id, "project": principal.name,
                              "immediate": immediate, "priority": prio, "t": now,
                              "hold_minutes": hold_minutes}
        self.log.scheduler_event("mode_request", mode=mode, project=principal.name, immediate=immediate)
        self.poke()
        await asyncio.sleep(0)
        await self.tick()
        if self.switching == mode or self.state.current == mode:
            return {"status": "switching", "mode": mode, "eta_s": self.switch_costs.get(mode)}
        return {"status": "pending", "mode": mode, "retry_after": self._hold_left(now),
                "reason": self._pending_reason(now)}

    def _hold_left(self, now):
        left = 0
        if self.state.current == self.coding_mode:
            left = max(left, self.cfg.sched.coding_hold_s - (now - self.state.last_coding))
        left = max(left, self.state.pinned_until - now)
        return int(left) if left > 0 else 5

    def _pending_reason(self, now):
        if self.state.current == self.coding_mode and now - self.state.last_coding < self.cfg.sched.coding_hold_s:
            return "coding hold"
        if now < self.state.pinned_until:
            return "mode pinned"
        return "waiting for running work in the current mode"

    def _begin_switch(self, target, reason):
        if self.switching:
            return
        self.switching, self.switch_reason = target, reason
        self.switch_task = asyncio.create_task(self._switch(target, reason))

    async def _switch(self, target, reason):
        t0 = self.clock()
        src = self.state.current
        self.log.scheduler_event("mode_switch_start", src=src, dst=target, reason=reason)
        self.bus.publish("status", {"switching": target})
        keep = set(self.cfg.modes[target].engines)
        try:
            # 1. running media jobs on engines that go away pause at their next step (progress kept)
            for run in list(self.runs.values()):
                if run.engine and run.engine not in keep and not run.command:
                    await self.command(run.job["id"], "switch", actor="karspark", reason=f"mode switch to {target}")
            await self._wait(lambda: not any(r.engine and r.engine not in keep for r in self.runs.values()), 180)
            # 2. text streams on those engines get a grace period
            await self._wait(lambda: not any(s.engine not in keep for s in self.streams.values()),
                             self.cfg.sched.stream_grace_s)
            for run in list(self.runs.values()):  # still stuck: the engine stop ends them; they resume later
                if run.engine and run.engine not in keep and run.task:
                    run.task.cancel()
                    self.runs.pop(run.job["id"], None)
                    await self.jobs.set(run.job["id"], "queued", actor="karspark", reason="engine stopped for mode switch",
                                        paused_by="system", enqueued=self.clock())
            # 3. engines
            try:
                await self.engines.ensure(list(self.cfg.modes[target].engines))
            except EngineError as e:
                self.log.scheduler_event("engine_failed", mode=target, error=str(e)[:2000])
                # one retry for each failed engine, then leave it failed and visible in /v1/status
                for rid in self.cfg.modes[target].engines:
                    if not self.engines.ready(rid):
                        with contextlib.suppress(EngineError):
                            await self.engines.restart(rid)
            now = self.clock()
            self.state.current, self.state.since = target, now
            if self.state.pending and self.state.pending["mode"] == target:
                hm = self.state.pending.get("hold_minutes")
                if hm:
                    self.state.pinned_until = now + hm * 60
                self.state.pending = None
            if self.cfg.modes[target].coding:
                self.state.last_coding = now
            self.state.last_activity = now
            cost = round(now - t0, 1)
            self.switch_costs[target] = cost
            self.log.scheduler_event("mode_switch_done", src=src, dst=target, reason=reason, seconds=cost,
                                     engines=self.engines.view())
        finally:
            self.switching = self.switch_reason = None
            await self._persist()
            self.bus.publish("status", {"mode": self.state.current})
            self.poke()

    async def _wait(self, cond, timeout):
        t = 0.0
        while not cond() and t < timeout:
            await asyncio.sleep(0.5)
            t += 0.5

    # ---------------------------------------------------------------- views
    async def queue_view(self, engine=None):
        now = self.clock()
        queued = await self.jobs.waiting()
        await self._weights(queued)
        out = []
        groups: dict[str, list] = {}
        for j in queued:
            eng = None
            for mode in [self.state.current] + list(self.cfg.modes):
                e = self.cfg.engine_for(mode, self.cfg.modes[mode].text if j["model"] == "text" else j["model"])
                if e:
                    eng = e.id
                    break
            groups.setdefault(eng or "?", []).append(j)
        for rid, js in groups.items():
            if engine and rid != engine:
                continue
            items = [self._item(j) for j in js]
            fair = self.fair_media if self.cfg.recipes.get(rid) and self.cfg.recipes[rid].kind == "media" else \
                self.fair_text.get(rid, FairShare())
            for pos, it in enumerate(policy.order(items, now, self.cfg.sched.age_after_s, fair)):
                j = next(x for x in js if x["id"] == it.id)
                cls = policy.aged_class(it, now, self.cfg.sched.age_after_s)
                why = [f"P{cls}"] + (["aged"] if cls < it.priority else []) + (["resumes first"] if it.preempted else [])
                out.append({"engine": rid, "position": pos + 1, "job_id": j["id"], "project": j["project"],
                            "kind": j["kind"], "model": j["model"], "priority": cls, "waited_s": round(now - j["enqueued"]),
                            "why": ", ".join(why) + f"; fair share {j['_weight']}", "eta_s": None})
        self._etas(out)
        for w in self.text_waiters:
            out.append({"engine": "text", "position": None, "stream": w.item.id, "project": w.project,
                        "kind": "text-request", "model": w.model, "priority": w.item.priority,
                        "waited_s": round(now - w.item.enqueued), "why": "waiting for a text slot"
                        + (" (coding: switch pending)" if w.coding and self.state.current != self.coding_mode else "")})
        return out

    def _etas(self, rows):
        ahead: dict[str, float] = {}
        for run in self.runs.values():
            k = f"{run.job['kind']}:{run.job['model']}"
            est = self.durations.get(k)
            if est and run.engine:
                ahead[run.engine] = ahead.get(run.engine, 0) + max(0, est - (self.clock() - run.started))
        for r in rows:
            est = self.durations.get(f"{r['kind']}:{r['model']}")
            modes = self.cfg.mode_of_model(r["model"]) if r["model"] != "text" else [self.state.current]
            switch = 0 if self.state.current in modes else self.switch_costs.get(modes[0] if modes else "", 0)
            if est is not None:
                r["eta_s"] = round(ahead.get(r["engine"], 0) + switch)
                ahead[r["engine"]] = ahead.get(r["engine"], 0) + est

    def mode_view(self):
        now = self.clock()
        st = self.state
        hold = None
        if st.current == self.coding_mode:
            left = self.cfg.sched.coding_hold_s - (now - st.last_coding)
            hold = {"kind": "coding", "ends_in_s": int(left)} if left > 0 else None
        if now < st.pinned_until:
            hold = {"kind": "pin", "ends_in_s": int(st.pinned_until - now)}
        return {"current": st.current, "since": st.since, "switching": self.switching, "switch_reason": self.switch_reason,
                "hold": hold, "pending": st.pending and {k: v for k, v in st.pending.items() if k != "key_id"},
                "default": self.cfg.sched.default_mode, "text_model": self.cfg.modes[st.current].text,
                "switch_costs_s": self.switch_costs,
                "idle_return_in_s": None if self.runs or self.streams or st.current == self.cfg.sched.default_mode
                else max(0, int(self.cfg.sched.idle_return_s - (now - st.last_activity)))}

    def streams_view(self, principal=None):
        return [{"id": s.id, "project": s.project, "model": s.model, "engine": s.engine, "priority": s.priority,
                 "running_s": round(self.clock() - s.started, 1), "tokens": s.tokens}
                for s in self.streams.values() if principal is None or principal.is_owner or s.project == principal.name]
