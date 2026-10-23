#!/usr/bin/env python3
"""karspark ComfyUI adapter: one process in front of ComfyUI, inside the karspark/comfyui container.

karspark-core is the only caller. It owns jobs, queues and files; this adapter turns one job into one
ComfyUI run and reports on it. Jobs share a directory with core, mounted at /jobs:

  /jobs/<job_id>/in/...        inputs, written by core (ComfyUI's input folder is /jobs)
  /jobs/<job_id>/out/...       outputs (ComfyUI's output folder is /jobs too)
  /jobs/<job_id>/ckpt/...      sampler checkpoints (karspark_nodes), so a paused job keeps its progress
  /jobs/<job_id>/progress.json current stage and step
  /jobs/<job_id>/PAUSE         pause request, checked at every sampling step

API (127.0.0.1 only; the recipe publishes it to core, never to the network):
  POST /v1/run              {job_id, run_id, kind, params, inputs} -> 202 {prompt_id}  (400 on a bad graph)
  GET  /v1/jobs/{id}        {state: running|succeeded|paused|cancelled|failed, progress, outputs, error}
  POST /v1/jobs/{id}/pause  stop at the next sampling step, keeping a checkpoint
  POST /v1/jobs/{id}/cancel stop now; checkpoints are left for core to delete
  POST /v1/free             unload ComfyUI's cached models (between model families, or before a mode switch)
  GET  /v1/models, /health
  everything else is proxied to ComfyUI unchanged (UI, native API, websockets) for /comfyui/ on core.

Resume = POST /v1/run again with the same job_id and a new run_id: finished stages are read back and the
interrupted stage continues from its saved step.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, os.path.dirname(__file__))
import workflows  # noqa: E402

COMFY = "http://127.0.0.1:8188"
COMFY_ARGS = os.environ.get("COMFY_ARGS", "").split()
JOBS = Path(os.environ.get("JOBS_DIR", "/jobs"))
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv")


def start_comfy():
    cmd = [sys.executable, "/opt/comfyui/main.py", "--listen", "127.0.0.1", "--port", "8188",
           "--disable-auto-launch", "--output-directory", str(JOBS), "--input-directory", str(JOBS), *COMFY_ARGS]
    print("[adapter] starting:", " ".join(cmd), flush=True)
    return subprocess.Popen(cmd)


async def wait_comfy(session, proc):
    while True:
        if proc.poll() is not None:
            raise SystemExit(f"[adapter] ComfyUI exited with {proc.returncode}")
        try:
            async with session.get(f"{COMFY}/system_stats", timeout=aiohttp.ClientTimeout(total=3)) as r:
                if r.status == 200:
                    print("[adapter] ComfyUI is up", flush=True)
                    return
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        await asyncio.sleep(1)


def err(msg, status=400, **extra):
    return web.json_response({"error": {"message": msg, **extra}}, status=status)


def job_dir(job_id):
    if not job_id or "/" in job_id or job_id.startswith("."):
        raise web.HTTPBadRequest(text="bad job_id")
    return JOBS / job_id


# ---------------------------------------------------------------- jobs
# job_id -> {"prompt_id", "run_id", "cancel": bool, "final": dict | None}. Lost on restart, which also
# restarts ComfyUI; core then sees 404 and runs the job again from its checkpoints.
RUNS = {}


async def run(request):
    body = await request.json()
    job_id, kind = body.get("job_id"), body.get("kind")
    d = job_dir(job_id)
    cur = RUNS.get(job_id)
    if cur and cur["final"] is None:
        return err("job is already running", 409)
    (d / "out").mkdir(parents=True, exist_ok=True)
    (d / "PAUSE").unlink(missing_ok=True)
    (d / "progress.json").unlink(missing_ok=True)
    run_id = body.get("run_id") or str(time.time_ns())
    ctx = {"job_dir": str(d), "run_id": run_id, "out": f"{job_id}/out"}
    inputs = {k: (f"{job_id}/in/{v}" if isinstance(v, str) else [f"{job_id}/in/{x}" for x in v])
              for k, v in (body.get("inputs") or {}).items()}
    try:
        graph = workflows.build(kind, body.get("params") or {}, inputs, ctx)
    except workflows.WorkflowError as e:
        return err(str(e))
    session = request.app["session"]
    async with session.post(f"{COMFY}/prompt", json={"prompt": graph, "client_id": "karspark"}) as r:
        res = await r.json()
    if r.status != 200:
        return err("ComfyUI rejected the workflow", 400, detail=res)
    RUNS[job_id] = {"prompt_id": res["prompt_id"], "run_id": run_id, "cancel": False, "final": None}
    return web.json_response({"prompt_id": res["prompt_id"], "run_id": run_id}, status=202)


def _progress(d):
    try:
        return json.loads((d / "progress.json").read_text())
    except (OSError, ValueError):
        return None


async def _final_state(session, job_id, entry):
    """ComfyUI's verdict on a finished prompt, mapped onto karspark states."""
    status = entry.get("status", {})
    if status.get("completed") and RUNS[job_id]["cancel"]:
        return {"state": "cancelled"}   # finished before the cancel took effect; the caller asked to drop it
    if status.get("completed"):
        outputs = []
        for node_out in entry.get("outputs", {}).values():
            for key in ("images", "gifs", "videos", "audio"):
                for f in node_out.get(key) or []:
                    if isinstance(f, dict) and f.get("type", "output") == "output":
                        outputs.append("/".join(p for p in (f.get("subfolder"), f["filename"]) if p))
        return {"state": "succeeded", "outputs": outputs}
    msgs = [m[0] for m in status.get("messages", []) if m]
    if "execution_interrupted" in msgs:
        if RUNS[job_id]["cancel"]:
            return {"state": "cancelled"}
        return {"state": "paused"}
    detail = next((m[1] for m in status.get("messages", []) if m and m[0] == "execution_error"), None)
    if not isinstance(detail, dict):
        return {"state": "failed", "error": {"message": "ComfyUI job failed", "status": status}}
    # callers get the cause in one line; the full traceback stays in the ComfyUI container log
    return {"state": "failed", "error": {
        "message": f"{detail.get('node_type')} (node {detail.get('node_id')}): {detail.get('exception_type')}: "
                   f"{str(detail.get('exception_message', '')).strip()}",
        "node_type": detail.get("node_type"), "exception_type": detail.get("exception_type"),
        "traceback_tail": "".join(detail.get("traceback") or [])[-1500:]}}


async def job_status(request):
    job_id = request.match_info["id"]
    d = job_dir(job_id)
    cur = RUNS.get(job_id)
    if not cur:
        return err("unknown job", 404)
    if cur["final"] is None:
        session = request.app["session"]
        async with session.get(f"{COMFY}/history/{cur['prompt_id']}") as r:
            h = await r.json()
        if cur["prompt_id"] in h:
            cur["final"] = await _final_state(session, job_id, h[cur["prompt_id"]])
            (d / "PAUSE").unlink(missing_ok=True)
            async with session.post(f"{COMFY}/history", json={"delete": [cur["prompt_id"]]}):
                pass
        elif cur["cancel"] and not await _queued_or_running(session, cur["prompt_id"]):
            # deleted from ComfyUI's queue before it ran: it will never reach the history
            cur["final"] = {"state": "cancelled"}
            (d / "PAUSE").unlink(missing_ok=True)
    out = cur["final"] or {"state": "running"}
    return web.json_response({**out, "job_id": job_id, "run_id": cur["run_id"], "progress": _progress(d)})


async def _queued_or_running(session, prompt_id):
    async with session.get(f"{COMFY}/queue") as r:
        q = await r.json()
    return any(item[1] == prompt_id for key in ("queue_running", "queue_pending") for item in q.get(key, []))


async def pause(request):
    job_id = request.match_info["id"]
    cur = RUNS.get(job_id)
    if not cur or cur["final"] is not None:
        return err("job is not running", 409)
    (job_dir(job_id) / "PAUSE").touch()
    return web.json_response({"state": "pausing"}, status=202)


async def cancel(request):
    job_id = request.match_info["id"]
    cur = RUNS.get(job_id)
    if not cur or cur["final"] is not None:
        return err("job is not running", 409)
    cur["cancel"] = True
    # The PAUSE flag stops the sampler at its next step even if the prompt starts after the interrupt below
    # (ComfyUI skips an interrupt for a prompt that is dequeued but not executing yet).
    (job_dir(job_id) / "PAUSE").touch()
    session = request.app["session"]
    async with session.post(f"{COMFY}/queue", json={"delete": [cur["prompt_id"]]}):
        pass
    async with session.post(f"{COMFY}/interrupt", json={"prompt_id": cur["prompt_id"]}):
        pass
    return web.json_response({"state": "cancelling"}, status=202)


async def free(request):
    async with request.app["session"].post(f"{COMFY}/free", json={"unload_models": True, "free_memory": True}) as r:
        return web.json_response({"ok": r.status == 200})


async def models(request):
    return web.json_response({"object": "list", "data": [
        {"id": m, "object": "model", "owned_by": "karspark", "kind": kind, "default": i == 0}
        for kind, names in workflows.MODELS.items() for i, m in enumerate(names)]})


async def health(request):
    return web.Response(text="ok")


# ---------------------------------------------------------------- transparent proxy (UI, native API, websockets)
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}


async def proxy(request):
    session = request.app["session"]
    url = f"{COMFY}{request.rel_url}"
    if request.headers.get("upgrade", "").lower() == "websocket":
        ws_client = web.WebSocketResponse()
        await ws_client.prepare(request)
        async with session.ws_connect(url.replace("http://", "ws://")) as ws_up:
            async def up_to_client():
                async for msg in ws_up:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await ws_client.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await ws_client.send_bytes(msg.data)
            task = asyncio.ensure_future(up_to_client())
            async for msg in ws_client:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws_up.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await ws_up.send_bytes(msg.data)
            task.cancel()
        return ws_client
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP and k.lower() != "authorization"}
    body = await request.read()
    async with session.request(request.method, url, headers=headers, data=body or None, allow_redirects=False) as r:
        resp = web.StreamResponse(status=r.status, headers={k: v for k, v in r.headers.items() if k.lower() not in HOP})
        await resp.prepare(request)
        async for chunk in r.content.iter_chunked(1 << 16):
            await resp.write(chunk)
        await resp.write_eof()
        return resp


async def on_startup(app):
    app["session"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))
    await wait_comfy(app["session"], app["proc"])


async def on_cleanup(app):
    await app["session"].close()
    app["proc"].terminate()


def main():
    JOBS.mkdir(parents=True, exist_ok=True)
    app = web.Application(client_max_size=16 * 1024 * 1024)
    app["proc"] = start_comfy()
    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/run", run)
    app.router.add_get("/v1/jobs/{id}", job_status)
    app.router.add_post("/v1/jobs/{id}/pause", pause)
    app.router.add_post("/v1/jobs/{id}/cancel", cancel)
    app.router.add_post("/v1/free", free)
    app.router.add_route("*", "/{tail:.*}", proxy)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), print=None)


if __name__ == "__main__":
    main()
