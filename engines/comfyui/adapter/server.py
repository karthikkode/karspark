#!/usr/bin/env python3
"""karspark ComfyUI adapter: one process in front of ComfyUI, inside the karspark/comfyui container.

  - starts ComfyUI on 127.0.0.1:8188 (inside the container) and waits for it
  - POST /v1/images/generations   OpenAI-style images API (FLUX.2), see workflows.py
  - POST /v1/videos/generations   LTX-2.5 image/text-to-video (karspark extension, same shape)
  - everything else (UI, /prompt, /history, /view, /ws) is proxied to ComfyUI unchanged, so the
    gateway's /comfyui/ endpoint serves the full ComfyUI app and native API.

Listens on 0.0.0.0:8000 in the container; the recipe publishes it on 127.0.0.1 only.
"""
import asyncio
import base64
import json
import os
import random
import subprocess
import sys
import time
import uuid

import aiohttp
from aiohttp import web

sys.path.insert(0, os.path.dirname(__file__))
import workflows  # noqa: E402

COMFY = "http://127.0.0.1:8188"
COMFY_ARGS = os.environ.get("COMFY_ARGS", "").split()
JOB_TIMEOUT_S = int(os.environ.get("JOB_TIMEOUT_S", "3600"))


def start_comfy():
    cmd = [sys.executable, "/opt/comfyui/main.py", "--listen", "127.0.0.1", "--port", "8188",
           "--disable-auto-launch", "--output-directory", "/tmp/comfy-out", "--input-directory", "/tmp/comfy-in",
           *COMFY_ARGS]
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
        except aiohttp.ClientError:
            pass
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(1)


# ---------------------------------------------------------------- jobs through ComfyUI's native API
async def upload_image(session, b64):
    raw = base64.b64decode(b64.split(",", 1)[-1])
    form = aiohttp.FormData()
    form.add_field("image", raw, filename=f"ref-{uuid.uuid4().hex}.png", content_type="image/png")
    form.add_field("overwrite", "true")
    async with session.post(f"{COMFY}/upload/image", data=form) as r:
        r.raise_for_status()
        return (await r.json())["name"]


async def run_graph(session, graph):
    """Queue an API-format graph, wait for it, return the list of output files (bytes, filename)."""
    client_id = uuid.uuid4().hex
    async with session.post(f"{COMFY}/prompt", json={"prompt": graph, "client_id": client_id}) as r:
        body = await r.json()
        if r.status != 200:
            raise web.HTTPBadRequest(text=json.dumps({"error": {"message": "ComfyUI rejected the workflow", "detail": body}}),
                                     content_type="application/json")
        pid = body["prompt_id"]
    deadline = time.time() + JOB_TIMEOUT_S
    while time.time() < deadline:
        async with session.get(f"{COMFY}/history/{pid}") as r:
            h = await r.json()
        if pid in h:
            entry = h[pid]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise web.HTTPInternalServerError(text=json.dumps({"error": {"message": "ComfyUI job failed", "detail": status}}),
                                                  content_type="application/json")
            if status.get("completed"):
                files = []
                for out in entry.get("outputs", {}).values():
                    for key in ("images", "gifs", "videos", "audio"):
                        for f in out.get(key, []):
                            params = {"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
                            async with session.get(f"{COMFY}/view", params=params) as v:
                                files.append((await v.read(), f["filename"]))
                return files
        await asyncio.sleep(0.5)
    raise web.HTTPGatewayTimeout(text="ComfyUI job timed out")


def openai_error(msg, status=400):
    return web.json_response({"error": {"message": msg, "type": "invalid_request_error"}}, status=status)


async def images_generations(request):
    req = await request.json()
    session = request.app["session"]
    try:
        # "image" (the picture to split, for qwen-image-layered) goes first, then any reference images.
        sources = ([req["image"]] if req.get("image") else []) + list(req.get("reference_images") or [])
        refs = [await upload_image(session, b) for b in sources]
        n = max(1, min(int(req.get("n", 1)), 8))
        seed = req.get("seed")
        seed = int(seed) if seed is not None else random.randint(0, 2**31 - 1)
        data = []
        for i in range(n):
            graph = workflows.build_image(req, refs, seed + i)
            for raw, name in await run_graph(session, graph):
                data.append({"b64_json": base64.b64encode(raw).decode(), "revised_prompt": req.get("prompt"), "seed": seed + i})
    except workflows.WorkflowError as e:
        return openai_error(str(e))
    return web.json_response({"created": int(time.time()), "data": data})


async def videos_generations(request):
    req = await request.json()
    session = request.app["session"]
    try:
        image = await upload_image(session, req["image"]) if req.get("image") else None
        seed = int(req["seed"]) if req.get("seed") is not None else random.randint(0, 2**31 - 1)
        graph = workflows.build_video(req, image, seed)
        files = await run_graph(session, graph)
    except workflows.WorkflowError as e:
        return openai_error(str(e))
    vids = [(raw, name) for raw, name in files if name.lower().endswith((".mp4", ".webm", ".mov"))] or files
    return web.json_response({"created": int(time.time()), "seed": seed,
                              "data": [{"b64_json": base64.b64encode(raw).decode(), "filename": name} for raw, name in vids]})


async def models(request):
    return web.json_response({"object": "list", "data": [{"id": m, "object": "model", "owned_by": "karspark"} for m in workflows.MODELS]})


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
    os.makedirs("/tmp/comfy-out", exist_ok=True)
    os.makedirs("/tmp/comfy-in", exist_ok=True)
    app = web.Application(client_max_size=256 * 1024 * 1024)
    app["proc"] = start_comfy()
    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/images/generations", images_generations)
    app.router.add_post("/v1/videos/generations", videos_generations)
    app.router.add_route("*", "/{tail:.*}", proxy)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), print=None)


if __name__ == "__main__":
    main()
