"""Owner/admin routes: keys, the request log, engines, metrics (docs/api.md#admin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .api_common import principal
from .jobs import JobError

router = APIRouter()


async def admin(request: Request):
    p = await principal(request)
    if not p.is_admin:
        raise HTTPException(403, "admin only")
    return p


async def _json(request):
    try:
        b = await request.json()
    except ValueError:
        raise JobError("body must be JSON")
    return b if isinstance(b, dict) else {}


@router.get("/admin/keys")
async def list_keys(request: Request, p=Depends(admin)):
    return {"object": "list", "data": await request.app.state.k.keys.list()}


@router.post("/admin/keys")
async def create_key(request: Request, p=Depends(admin)):
    b = await _json(request)
    if not b.get("name") or not b.get("tier"):
        raise JobError("name and tier are required")
    if b["tier"] == "owner" and not p.is_owner:
        raise HTTPException(403, "only owner keys create owner keys")
    try:
        kid, secret = await request.app.state.k.keys.create(b["name"], b["tier"], b.get("scopes") or [])
    except ValueError as e:
        raise JobError(str(e))
    except Exception as e:  # unique name
        raise JobError(f"could not create key: {e}", 409)
    request.app.state.k.log.scheduler_event("key_created", name=b["name"], tier=b["tier"], by=p.name)
    return {"key_id": kid, "name": b["name"], "tier": b["tier"], "key": secret,
            "note": "shown once; store it in the project's secret store"}


@router.post("/admin/keys/{key_id}/rotate")
async def rotate_key(request: Request, key_id: str, p=Depends(admin)):
    secret = await request.app.state.k.keys.rotate(key_id)
    request.app.state.k.log.scheduler_event("key_rotated", key_id=key_id, by=p.name)
    return {"key_id": key_id, "key": secret, "note": "shown once; the old key stops working now"}


@router.post("/admin/keys/{key_id}/revoke")
async def revoke_key(request: Request, key_id: str, p=Depends(admin)):
    if key_id == p.key_id:
        raise JobError("you can't revoke the key you are using")
    await request.app.state.k.keys.revoke(key_id)
    request.app.state.k.log.scheduler_event("key_revoked", key_id=key_id, by=p.name)
    return {"key_id": key_id, "revoked": True}


@router.patch("/admin/keys/{key_id}")
async def patch_key(request: Request, key_id: str, p=Depends(admin)):
    b = await _json(request)
    try:
        await request.app.state.k.keys.set_tier(key_id, b.get("tier"))
    except ValueError as e:
        raise JobError(str(e))
    return {"key_id": key_id, "tier": b.get("tier")}


@router.get("/admin/logs/requests")
async def log_requests(request: Request, key: str | None = None, model: str | None = None, since: float | None = None,
                       status: int | None = None, path: str | None = None, limit: int = 100, p=Depends(admin)):
    return {"object": "list", "data": await request.app.state.k.log.requests(key, model, since, status, path, limit)}


@router.get("/admin/logs/requests/{request_id}")
async def log_request(request: Request, request_id: str, p=Depends(admin)):
    r = await request.app.state.k.log.request_detail(request_id)
    if not r:
        raise HTTPException(404, "no such request")
    return r


@router.get("/admin/logs/scheduler")
async def log_scheduler(request: Request, since: float = 0, limit: int = 100, p=Depends(admin)):
    return {"object": "list", "data": await request.app.state.k.log.scheduler_events(since, limit)}


@router.post("/admin/engines/{recipe_id}/restart")
async def restart_engine(request: Request, recipe_id: str, p=Depends(admin)):
    k = request.app.state.k
    if recipe_id not in k.cfg.recipes:
        raise HTTPException(404, "no such recipe")
    if recipe_id not in k.cfg.modes[k.sched.state.current].engines:
        raise JobError("that engine isn't part of the current mode", 409)
    await k.engines.restart(recipe_id)
    return {"recipe": recipe_id, "state": k.engines.engines[recipe_id].state}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request, p=Depends(admin)):
    k = request.app.state.k
    lines = []

    def g(name, value, **labels):
        lab = ",".join(f'{a}="{b}"' for a, b in labels.items())
        lines.append(f"karspark_{name}{{{lab}}} {value}")

    for m in k.cfg.modes:
        g("mode_active", int(m == k.sched.state.current), mode=m)
    for e in k.engines.engines.values():
        g("engine_ready", int(e.state == "ready"), recipe=e.recipe.id)
    rows = await k.db.all("SELECT state, kind, COUNT(*) AS n FROM jobs GROUP BY state, kind")
    for r in rows:
        g("jobs", r["n"], state=r["state"], kind=r["kind"])
    g("text_streams", len(k.sched.streams))
    g("text_waiting", len(k.sched.text_waiters))
    for key, v in (k.resources.latest or {}).items():
        if isinstance(v, (int, float)):
            g(key, v)
    return "\n".join(lines) + "\n"
