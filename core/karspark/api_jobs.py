"""Jobs, files, mode, status: the karspark API proper (docs/api.md)."""
from __future__ import annotations

import asyncio
import base64
import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .api_common import err, job_view, log_info, principal
from .jobs import FINAL, JobError
from .workers import file_view

router = APIRouter()


def svc(request: Request):
    return request.app.state.k.svc


async def _json(request):
    try:
        b = await request.json()
    except ValueError:
        raise JobError("body must be JSON")
    if not isinstance(b, dict):
        raise JobError("body must be a JSON object")
    return b


# ---------------------------------------------------------------- jobs
@router.post("/v1/jobs")
async def create_job(request: Request, p=Depends(principal)):
    body = await _json(request)
    info = log_info(request)
    job, created = await svc(request).submit(p, body, info.get("request_id"), request.headers.get("idempotency-key"))
    info.update(job_id=job["id"], model=job["model"], priority_req=job["priority_req"], priority=job["priority"])
    if body.get("wait"):
        view = await svc(request).wait(p, job["id"], float(body["wait"]) if body["wait"] is not True else 50)
    else:
        view = await svc(request).get(p, job["id"])
    return JSONResponse(view, status_code=201 if created else 200)


@router.get("/v1/jobs")
async def list_jobs(request: Request, state: str | None = None, type: str | None = None, mine: bool = True,
                    limit: int = 50, p=Depends(principal)):
    return {"object": "list", "data": await svc(request).list(p, state, type, mine, limit)}


@router.post("/v1/jobs/cancel")
async def cancel_many(request: Request, p=Depends(principal)):
    body = await _json(request) if await request.body() else {}
    return await svc(request).cancel_many(p, body.get("state"), body.get("type"))


@router.get("/v1/jobs/{job_id}")
async def get_job(request: Request, job_id: str, p=Depends(principal)):
    return await svc(request).get(p, job_id)


@router.get("/v1/jobs/{job_id}/wait")
async def wait_job(request: Request, job_id: str, timeout: float = 50, p=Depends(principal)):
    return await svc(request).wait(p, job_id, timeout)


@router.post("/v1/jobs/{job_id}/pause")
async def pause_job(request: Request, job_id: str, p=Depends(principal)):
    return await svc(request).pause(p, job_id)


@router.post("/v1/jobs/{job_id}/resume")
async def resume_job(request: Request, job_id: str, p=Depends(principal)):
    return await svc(request).resume(p, job_id)


@router.post("/v1/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str, p=Depends(principal)):
    return await svc(request).cancel(p, job_id)


@router.patch("/v1/jobs/{job_id}")
async def patch_job(request: Request, job_id: str, p=Depends(principal)):
    body = await _json(request)
    if "priority" not in body:
        raise JobError("only priority can be changed")
    return await svc(request).set_priority(p, job_id, body["priority"])


@router.get("/v1/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str, p=Depends(principal)):
    return {"object": "list", "data": await svc(request).events(p, job_id)}


@router.get("/v1/jobs/{job_id}/stream")
async def job_stream(request: Request, job_id: str, p=Depends(principal)):
    """SSE: `state` events on every change, `progress` events while it runs; ends when the job is final."""
    k = request.app.state.k
    await k.jobs.owned(p, job_id)

    async def gen():
        with k.bus.subscribe() as q:
            j = await k.jobs.get(job_id)
            yield f"event: state\ndata: {json.dumps(job_view(j))}\n\n"
            if j["state"] in FINAL:
                return
            while True:
                try:
                    topic, ev = await asyncio.wait_for(q.get(), 15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev.get("job_id") != job_id:
                    continue
                if topic == "progress":
                    yield f"event: progress\ndata: {json.dumps(ev['progress'])}\n\n"
                elif topic == "job":
                    j = await k.jobs.get(job_id)
                    yield f"event: state\ndata: {json.dumps(job_view(j))}\n\n"
                    if j["state"] in FINAL:
                        return

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- OpenAI-compatible image shortcut
@router.post("/v1/images/generations")
async def images_generations(request: Request, p=Depends(principal)):
    """OpenAI SDK compatible: runs an `image` job and waits for it (up to `timeout`, default 300 s; after
    that it answers 202 with the job). Prefer /v1/jobs for anything non-interactive."""
    k = request.app.state.k
    body = await _json(request)
    n = max(1, min(int(body.pop("n", 1) or 1), 4))
    fmt = body.pop("response_format", "b64_json")
    timeout = float(body.pop("timeout", 300))
    body = {**body, "type": "image"}
    if body.get("image") and not body.get("reference_images"):
        body["reference_images"] = [body.pop("image")]
    jobs = []
    for i in range(n):
        b = dict(body)
        if b.get("seed") is not None:
            b["seed"] = int(b["seed"]) + i
        job, _ = await k.svc.submit(p, b, log_info(request).get("request_id"))
        jobs.append(job)
    log_info(request).update(job_id=jobs[0]["id"], model=jobs[0]["model"])
    deadline = time.monotonic() + timeout
    views = []
    for j in jobs:
        v = await k.svc.wait(p, j["id"], max(0, deadline - time.monotonic()))
        views.append(v)
    if any(v["state"] not in FINAL for v in views):
        return JSONResponse({"object": "list", "data": [], "jobs": views,
                             "message": "still running; poll GET /v1/jobs/{id}"}, status_code=202)
    failed = [v for v in views if v["state"] != "succeeded"]
    if failed:
        return err(500, f"image job {failed[0]['id']} {failed[0]['state']}", "server_error", detail=failed[0]["error"])
    data = []
    for v in views:
        for o in v["outputs"] or []:
            item = {"url": o["url"], "file_id": o["file_id"], "seed": v["params"].get("seed")}
            if fmt == "b64_json":
                f = await k.files.get(p, o["file_id"])
                item["b64_json"] = base64.b64encode(k.files.path(f).read_bytes()).decode()
            data.append(item)
    log_info(request)["response_body"] = json.dumps({"jobs": [v["id"] for v in views]})
    return {"created": int(time.time()), "data": data}


# ---------------------------------------------------------------- files
@router.post("/v1/files")
async def upload(request: Request, p=Depends(principal)):
    """multipart/form-data (field `file`), or JSON {"name": ..., "data": "<base64>"}."""
    k = request.app.state.k
    log_info(request)["skip_request_body"] = True
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        up = form.get("file")
        if up is None or not hasattr(up, "read"):
            raise JobError("multipart field 'file' is required")
        raw, name = await up.read(), up.filename or "upload"
    else:
        body = await _json(request)
        try:
            raw = base64.b64decode((body.get("data") or "").split(",", 1)[-1])
        except ValueError:
            raise JobError("data must be base64")
        name = body.get("name") or "upload"
    if not raw:
        raise JobError("empty file")
    f = await k.files.save(p, name, raw)
    return JSONResponse({**file_view(f), "object": "file", "expires": f["expires"]}, status_code=201)


@router.get("/v1/files")
async def list_files(request: Request, limit: int = 50, p=Depends(principal)):
    k = request.app.state.k
    rows = await k.db.all("SELECT * FROM files WHERE key_id = ? ORDER BY created DESC LIMIT ?",
                          (p.key_id, min(limit, 500)))
    return {"object": "list", "data": [{**file_view(f), "job_id": f["job_id"], "expires": f["expires"]} for f in rows]}


@router.get("/v1/files/{file_id}")
async def file_meta(request: Request, file_id: str, p=Depends(principal)):
    f = await request.app.state.k.files.get(p, file_id)
    return {**file_view(f), "object": "file", "job_id": f["job_id"], "created": f["created"], "expires": f["expires"]}


@router.get("/v1/files/{file_id}/content")
async def file_content(request: Request, file_id: str, p=Depends(principal)):
    k = request.app.state.k
    f = await k.files.get(p, file_id)
    log_info(request)["skip_request_body"] = True
    return FileResponse(k.files.path(f), media_type=f["mime"], filename=f["name"])


@router.delete("/v1/files/{file_id}")
async def delete_file(request: Request, file_id: str, p=Depends(principal)):
    k = request.app.state.k
    f = await k.files.get(p, file_id)
    await k.files.delete(f)
    return {"id": file_id, "deleted": True}


# ---------------------------------------------------------------- mode, status, queue, models, usage
@router.get("/v1/mode")
async def get_mode(request: Request, p=Depends(principal)):
    return request.app.state.k.sched.mode_view()


@router.post("/v1/mode")
async def set_mode(request: Request, p=Depends(principal)):
    body = await _json(request)
    res = await request.app.state.k.sched.request_mode(p, body.get("mode"), body.get("hold_minutes"), body.get("priority"))
    status = 200 if res["status"] == "active" else 202
    headers = {"Retry-After": str(res["retry_after"])} if res.get("retry_after") else None
    return JSONResponse(res, status_code=status, headers=headers)


@router.get("/v1/status")
async def status(request: Request, p=Depends(principal)):
    return await svc(request).status(p)


@router.get("/v1/queue")
async def queue(request: Request, engine: str | None = None, p=Depends(principal)):
    return {"object": "list", "data": await svc(request).queue(p, engine)}


@router.get("/v1/models")
async def models(request: Request, p=Depends(principal)):
    return {"object": "list", "data": svc(request).models()}


@router.get("/v1/usage")
async def usage(request: Request, since: float | None = None, p=Depends(principal)):
    return await svc(request).usage(p, since)
