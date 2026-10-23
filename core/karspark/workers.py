"""Workers carry out one running job on its engine and report how it ended.

  media  -> the ComfyUI adapter's job API (POST /v1/run, poll /v1/jobs/{id}, pause/cancel at a step)
  text   -> the text engine's OpenAI API, streamed; a pause keeps the partial text and resume continues it
            (continue_final_message), so no generated token is lost

A worker returns {"state": succeeded|paused|cancelled|failed|lost, ...}; the scheduler owns state changes.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx

from .policy import family

POLL_S = 1.0
LOST_AFTER_S = 30


class Workers:
    def __init__(self, cfg, jobs, files, keys, engines, log, bus, http: httpx.AsyncClient | None = None):
        self.cfg, self.jobs, self.files, self.keys, self.engines = cfg, jobs, files, keys, engines
        self.log, self.bus = log, bus
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30, read=600))
        self.stops: dict[str, asyncio.Event] = {}
        self.signalled: set[str] = set()

    async def run(self, job, eng, run) -> dict:
        self.stops[job["id"]] = asyncio.Event()
        self.signalled.discard(job["id"])
        try:
            if job["kind"] == "text":
                return await self._text(job, eng, run)
            return await self._comfy(job, eng, run)
        finally:
            self.stops.pop(job["id"], None)
            self.signalled.discard(job["id"])

    async def signal(self, run):
        """A pause/preempt/switch/cancel command arrived for a running job."""
        jid = run.job["id"]
        ev = self.stops.get(jid)
        if ev:
            ev.set()
        if run.job["kind"] != "text" and run.engine and jid not in self.signalled:
            eng = self.cfg.recipes[run.engine]
            action = "cancel" if run.command == "cancel" else "pause"
            try:
                r = await self.http.post(f"{eng.base_url}/v1/jobs/{jid}/{action}", timeout=10)
                if r.status_code in (202, 409):
                    self.signalled.add(jid)
            except httpx.HTTPError:
                pass  # retried by the poll loop

    # ---------------------------------------------------------------- media (ComfyUI)
    async def _comfy(self, job, eng, run) -> dict:
        jid = job["id"]
        e = self.engines.engines[eng.id]
        fam = family(job["model"])
        if fam and e.loaded_model and e.loaded_model != fam:
            # ComfyUI keeps every model it has loaded; drop the previous family before loading the next.
            try:
                await self.http.post(f"{eng.base_url}/v1/free", timeout=60)
            except httpx.HTTPError:
                pass
        if fam:
            e.loaded_model = fam
        body = {"job_id": jid, "run_id": uuid.uuid4().hex[:12], "kind": job["kind"],
                "params": {**job["params"], "model": job["model"]}, "inputs": job["inputs"]}
        try:
            r = await self.http.post(f"{eng.base_url}/v1/run", json=body, timeout=60)
        except httpx.HTTPError as ex:
            return {"state": "lost", "error": f"adapter unreachable: {ex!r}"}
        if r.status_code == 400:
            return {"state": "failed", "error": r.json().get("error", {"message": r.text})}
        if r.status_code == 409:  # still running from before a core restart: just follow it
            pass
        elif r.status_code != 202:
            return {"state": "lost", "error": f"adapter said {r.status_code}: {r.text[:500]}"}
        last_ok, last_progress = time.monotonic(), None
        while True:
            await asyncio.sleep(POLL_S)
            if run.command and jid not in self.signalled:
                await self.signal(run)
            try:
                s = await self.http.get(f"{eng.base_url}/v1/jobs/{jid}", timeout=10)
            except httpx.HTTPError:
                if time.monotonic() - last_ok > LOST_AFTER_S:
                    return {"state": "lost", "error": "adapter stopped answering"}
                continue
            if s.status_code == 404:
                return {"state": "lost", "error": "engine restarted"}
            last_ok = time.monotonic()
            st = s.json()
            prog = st.get("progress")
            if prog and prog != last_progress:
                last_progress = prog
                await self.jobs.db.update("jobs", "id", jid, {"progress": prog})
                self.bus.publish("progress", {"job_id": jid, "progress": prog})
            state = st.get("state")
            if state == "running":
                continue
            if state == "succeeded":
                return {"state": "succeeded", "outputs": await self._register(job, st.get("outputs") or []),
                        "progress": prog}
            if state in ("paused", "cancelled"):
                return {"state": state, "progress": prog}
            return {"state": "failed", "error": st.get("error") or {"message": "ComfyUI job failed"}}

    async def _register(self, job, paths):
        p = await self.keys.get(job["key_id"])
        out = []
        for rel in paths:
            path = self.jobs.data / "jobs" / rel
            if not path.is_file() or not str(path.resolve()).startswith(str(self.jobs.dir(job["id"]).resolve())):
                continue
            f = await self.files.register_output(p, job["id"], path)
            out.append(file_view(f))
        return out

    # ---------------------------------------------------------------- text jobs
    async def _text(self, job, eng, run) -> dict:
        jid = job["id"]
        params = job["params"]
        prev = (job.get("progress") or {}).get("partial") or ""
        reasoning_prev = (job.get("progress") or {}).get("reasoning") or ""
        messages = list(params.get("messages") or [])
        if not messages:
            if params.get("system"):
                messages.append({"role": "system", "content": params["system"]})
            messages.append({"role": "user", "content": params.get("prompt", "")})
        body = {k: v for k, v in params.items() if k not in ("messages", "prompt", "system", "model", "reasoning_effort")}
        body.update({"model": eng.served_name or job["model"], "stream": True, "messages": messages})
        body.setdefault("max_tokens", eng.max_tokens_default)
        if params.get("reasoning_effort") in ("none", "off"):
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
        elif params.get("reasoning_effort"):
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = True
        if prev:
            body["messages"] = messages + [{"role": "assistant", "content": prev}]
            body["continue_final_message"], body["add_generation_prompt"] = True, False
            body["max_tokens"] = max(256, body["max_tokens"] - (job.get("progress") or {}).get("tokens", 0))
        text, reasoning, tokens, usage, finish = prev, reasoning_prev, (job.get("progress") or {}).get("tokens", 0), None, None
        stop = self.stops[jid]
        last_save = time.monotonic()
        try:
            async with self.http.stream("POST", f"{eng.base_url}/v1/chat/completions", json=body,
                                        timeout=httpx.Timeout(30, read=900)) as r:
                if r.status_code != 200:
                    return {"state": "failed", "error": {"message": f"engine said {r.status_code}",
                                                         "detail": (await r.aread()).decode()[:2000]}}
                async for line in r.aiter_lines():
                    if stop.is_set():
                        break
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        chunk = json.loads(line[6:])
                    except ValueError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for ch in chunk.get("choices") or []:
                        d = ch.get("delta") or {}
                        if d.get("content"):
                            text += d["content"]
                            tokens += 1
                        if d.get("reasoning_content") or d.get("reasoning"):
                            reasoning += d.get("reasoning_content") or d.get("reasoning")
                        finish = ch.get("finish_reason") or finish
                    if time.monotonic() - last_save > 2:
                        last_save = time.monotonic()
                        prog = {"partial": text, "reasoning": reasoning, "tokens": tokens}
                        await self.jobs.db.update("jobs", "id", jid, {"progress": prog})
                        self.bus.publish("progress", {"job_id": jid, "progress": {"tokens": tokens}})
        except httpx.HTTPError as ex:
            if not stop.is_set():
                return {"state": "lost", "error": f"text engine: {ex!r}",
                        "progress": {"partial": text, "reasoning": reasoning, "tokens": tokens}}
        prog = {"partial": text, "reasoning": reasoning, "tokens": tokens}
        if stop.is_set():
            if run.command == "cancel":
                return {"state": "cancelled"}
            await self.jobs.db.update("jobs", "id", jid, {"progress": prog})
            return {"state": "paused", "progress": prog}
        if not text.strip() and finish == "length":
            return {"state": "failed", "progress": prog, "error": {
                "message": f"the model spent all {body['max_tokens']} max_tokens reasoning and wrote no answer; "
                           "raise max_tokens or set reasoning_effort='none'", "reasoning_chars": len(reasoning)}}
        out_dir = self.jobs.dir(jid) / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output.txt").write_text(text)
        p = await self.keys.get(job["key_id"])
        f = await self.files.register_output(p, jid, out_dir / "output.txt")
        return {"state": "succeeded", "progress": {"tokens": tokens, "finish_reason": finish, "usage": usage},
                "outputs": [{**file_view(f), "text": text, "reasoning": reasoning or None}]}

    # ---------------------------------------------------------------- webhooks
    async def notify(self, job):
        if not job or not job.get("webhook") or job["state"] not in ("succeeded", "failed", "cancelled", "paused"):
            return
        asyncio.create_task(self._post_webhook(job))

    async def _post_webhook(self, job):
        from .api_common import job_view
        payload = {"type": f"job.{job['state']}", "job": job_view(job)}
        for attempt in range(3):
            try:
                r = await self.http.post(job["webhook"], json=payload, timeout=10)
                if r.status_code < 500:
                    self.log.job_event(job["id"], "webhook", reason=f"HTTP {r.status_code}")
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2 ** attempt * 5)
        self.log.job_event(job["id"], "webhook", reason="failed after 3 tries")


def file_view(f):
    return {"file_id": f["id"], "name": f["name"], "mime": f["mime"], "size": f["size"],
            "url": f"/v1/files/{f['id']}/content"}
