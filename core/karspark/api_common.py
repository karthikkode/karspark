"""Shared pieces of the HTTP API: auth, errors, request-log detail and the public job shape."""
from __future__ import annotations

import importlib.util
import ipaddress
import os
import random
import sys
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .jobs import JobError
from .keys import Principal, bearer

INPUTS = {  # job kind -> input name -> (media type, list?)
    "image": {"reference_images": ("image", True)},
    "layers": {"image": ("image", False)},
    "video": {"first_frame": ("image", False), "last_frame": ("image", False)},
    "upscale_image": {"image": ("image", False)},
    "upscale_video": {"video": ("video", False)},
    "speech": {"voice": ("audio", False)},
    "text": {},
}


def err(status, message, type_="invalid_request_error", **extra):
    return JSONResponse({"error": {"message": message, "type": type_, **extra}}, status_code=status)


def log_info(request: Request) -> dict:
    return request.scope.setdefault("state", {}).setdefault("log", {})


async def principal(request: Request) -> Principal:
    """FastAPI dependency: the caller's key -> Principal (401), then its rate limit (429)."""
    app = request.app.state.k
    p = await app.keys.verify(bearer(request.headers))
    if not p:
        raise HTTPException(401, "missing or invalid API key")
    info = log_info(request)
    info.update(key_id=p.key_id, project=p.name, tier=p.tier.name)
    wait = app.ratelimit.check_request(p)
    if wait:
        app.log.scheduler_event("rate_limited", project=p.name, wait_s=round(wait, 1))
        raise HTTPException(429, f"rate limit for tier {p.tier.name}; retry in {wait:.0f}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    return p


def job_view(j: dict, eta=None) -> dict:
    out = {k: j.get(k) for k in ("id", "kind", "model", "state", "priority", "priority_req", "project", "created",
                                 "started", "finished", "run_s", "progress", "outputs", "error", "preemptions",
                                 "paused_by", "attempts")}
    out["object"] = "job"
    out["params"] = j.get("params")
    if out["progress"] and "partial" in out["progress"]:  # text jobs: don't ship the whole partial on every poll
        p = dict(out["progress"])
        p["partial_chars"] = len(p.pop("partial") or "")
        p["reasoning_chars"] = len(p.pop("reasoning", None) or "")   # thinking models reason before answering
        out["progress"] = p
    if eta is not None:
        out["eta_s"] = eta
    return out


def check_webhook(url):
    if url is None:
        return None
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise JobError("webhook must be an http(s) URL")
    try:
        ip = ipaddress.ip_address(u.hostname)
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            raise JobError("webhook can't point at this machine")
    except ValueError:
        if u.hostname in ("localhost",) or u.hostname.endswith(".localhost"):
            raise JobError("webhook can't point at this machine")
    return url


_wf = None


def workflows():
    """The adapter's workflows.py (pure Python), used to validate media params at submit time, so a bad
    request is a 400 now instead of a failed job later. Bundled into the core image; the repo in tests."""
    global _wf
    if _wf is None:
        candidates = [os.environ.get("KARSPARK_WORKFLOWS", ""), str(Path(__file__).parent / "_workflows.py"),
                      str(Path(__file__).resolve().parents[2] / "engines/comfyui/adapter/workflows.py")]
        path = next(p for p in candidates if p and Path(p).is_file())
        spec = importlib.util.spec_from_file_location("karspark_workflows", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["karspark_workflows"] = mod
        spec.loader.exec_module(mod)
        _wf = mod
    return _wf


def default_model(kind):
    return workflows().MODELS[kind][0]


def validate_media(kind, params, inputs):
    """Builds the graph with dummy file names; WorkflowError -> 400."""
    wf = workflows()
    dummy = {k: ([f"x{i}.png" for i, _ in enumerate(v)] if isinstance(v, list) else "x.png") for k, v in inputs.items()}
    try:
        wf.build(kind, params, dummy, {"job_dir": "/jobs/x", "run_id": "v", "out": "x/out"})
    except wf.WorkflowError as e:
        raise JobError(str(e))


def new_seed():
    return random.randint(0, 2**31 - 1)
