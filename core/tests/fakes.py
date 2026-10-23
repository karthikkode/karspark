"""Fake engines for tests: a Docker runner that only tracks names, and httpx handlers that behave like a
vLLM text engine and the ComfyUI adapter (step-by-step sampling, pause, cancel, outputs)."""
import asyncio
import json
from pathlib import Path

import httpx

from karspark.engines import recipe_hash


class FakeRunner:
    def __init__(self):
        self.up: set[str] = set()
        self.starts: list[str] = []
        self.fail: set[str] = set()
        self.hashes: dict[str, str] = {}

    async def start(self, r):
        if r.id in self.fail:
            raise RuntimeError("boom")
        self.starts.append(r.id)
        self.up.add(r.id)
        self.hashes[r.id] = recipe_hash(r)

    async def stop(self, r):
        self.up.discard(r.id)

    async def running(self, r):
        return r.id in self.up

    async def current(self, r):
        return r.id in self.up and self.hashes.get(r.id) == recipe_hash(r)

    async def logs(self, r, lines=40):
        return "fake logs"


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay):
        self.chunks, self.delay = chunks, delay
        self.closed = False

    async def __aiter__(self):
        for c in self.chunks:
            if self.closed:
                return
            await asyncio.sleep(self.delay)
            yield c

    async def aclose(self):
        self.closed = True


class FakeText:
    def __init__(self, name, delay=0.0, words=5):
        self.name, self.delay, self.words = name, delay, words
        self.requests = []
        self.think_only = False   # a thinking model that spends the whole max_tokens on reasoning
        self.loop = None          # a phrase the model repeats (400 times)

    def handle(self, req: httpx.Request):
        if req.url.path == "/health":
            return httpx.Response(200)
        body = json.loads(req.content or b"{}")
        self.requests.append(body)
        words = [f"w{i} " for i in range(self.words)]
        if self.loop:
            words = ["Let me think about this. "] + [self.loop] * 400
        if body.get("continue_final_message"):
            words = ["more "]
        if not body.get("stream"):
            return httpx.Response(200, json={"id": "x", "model": body["model"], "choices": [
                {"index": 0, "message": {"role": "assistant", "content": f"{self.name}:" + "".join(words)},
                 "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": len(words), "total_tokens": 3 + len(words)}})
        key = "reasoning_content" if self.think_only else "content"
        chunks = [("data: " + json.dumps({"choices": [{"index": 0, "delta": {key: w}}]}) + "\n\n").encode() for w in words]
        if self.think_only:
            chunks.append(("data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]}) + "\n\n").encode())
        chunks.append(("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": len(words)}}) + "\n\n").encode())
        chunks.append(b"data: [DONE]\n\n")
        return httpx.Response(200, stream=Stream(chunks, self.delay), headers={"content-type": "text/event-stream"})


class FakeComfy:
    """Each poll of /v1/jobs/{id} advances one sampling step."""

    def __init__(self, data: Path, steps=5):
        self.data, self.steps = data, steps
        self.jobs = {}
        self.runs = []      # (job_id, start_step)
        self.frees = 0
        self.hold = False   # True: don't advance (simulates a long step)

    def handle(self, req: httpx.Request):
        p = req.url.path
        if p == "/health":
            return httpx.Response(200, text="ok")
        if p == "/v1/free":
            self.frees += 1
            return httpx.Response(200, json={"ok": True})
        if p == "/v1/run":
            b = json.loads(req.content)
            j = self.jobs.get(b["job_id"])
            if j and j["state"] == "running":
                return httpx.Response(409, json={"error": {"message": "running"}})
            if b["params"].get("prompt") == "explode":
                return httpx.Response(400, json={"error": {"message": "bad graph"}})
            start = j["step"] if j else 0   # resumes from its checkpoint
            self.jobs[b["job_id"]] = {"state": "running", "step": start, "pause": False, "cancel": False, "body": b}
            self.runs.append((b["job_id"], start))
            return httpx.Response(202, json={"prompt_id": "p"})
        parts = p.strip("/").split("/")
        jid = parts[2]
        j = self.jobs.get(jid)
        if not j:
            return httpx.Response(404, json={"error": {"message": "unknown job"}})
        if len(parts) == 4:
            if j["state"] != "running":
                return httpx.Response(409, json={})
            j[parts[3]] = True
            return httpx.Response(202, json={})
        if j["state"] == "running" and not self.hold:
            if j["cancel"]:
                j["state"] = "cancelled"
            elif j["pause"]:
                j["state"] = "paused"
            else:
                j["step"] += 1
                if j["step"] >= self.steps:
                    out = self.data / "jobs" / jid / "out"
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "image_00001_.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
                    j["state"] = "succeeded"
                    j["outputs"] = [f"{jid}/out/image_00001_.png"]
        return httpx.Response(200, json={"state": j["state"], "outputs": j.get("outputs"),
                                         "progress": {"stage": "flux2.0", "step": j["step"], "total": self.steps}})


def transport(cfg, handlers: dict, runner: FakeRunner):
    by_port = {cfg.recipes[rid].port: (rid, h) for rid, h in handlers.items()}

    async def handle(req: httpx.Request):
        if req.url.host != "127.0.0.1" or req.url.port not in by_port:
            return httpx.Response(599, text="no route")
        rid, h = by_port[req.url.port]
        if rid not in runner.up:
            raise httpx.ConnectError("connection refused")
        return h.handle(req)

    return httpx.MockTransport(handle)
