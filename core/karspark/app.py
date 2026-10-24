"""karspark-core: the only user-facing service on the Spark (:8080).

  uvicorn karspark.app:app            (the container's entrypoint; see core/Dockerfile)

Environment (set by karspark-core.service from ~/core/env, chmod 600):
  KARSPARK_ROOT        repo copy with recipes/ and config/        (default /opt/karspark)
  KARSPARK_DATA        data dir: karspark.db, logs.db, jobs/, files/  (default /data)
  KARSPARK_KEY_PEPPER  secret for key hashing (>= 32 chars)
  KARSPARK_API_KEY     the pre-core key: imported once as `karthik-coding` (owner) when no keys exist
  KARSPARK_MODELS / KARSPARK_ENGINES / KARSPARK_HOST_DATA   host paths for recipe variables
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import config as cfgmod
from .api_admin import router as admin_router
from .api_common import err
from .api_jobs import router as jobs_router
from .api_text import router as text_router
from .auditlog import AuditLog, AuditMiddleware
from .db import DB
from .engines import DockerRunner, EngineManager, host_paths
from .events import Bus
from .files import Files
from .jobs import JobError, JobStore
from .keys import Keys
from .ratelimit import RateLimiter
from .resources import Resources, hottest_c, maintenance
from .scheduler import Scheduler
from .service import Service
from .workers import Workers

# core polls engines every second; logging each poll buried the journal (requests are in logs.db)
logging.getLogger("httpx").setLevel(logging.WARNING)

LEGACY_KEY_NAME = "karthik-coding"


class Karspark:
    """Everything the routes need, built once per process."""

    def __init__(self, root: Path, data: Path, pepper: str, runner=None, http=None, clock=time.time,
                 adopt=True, background=True, tick_s=1.0, mem_free=None, poll_s=2.0, thermal=None):
        self.root, self.data, self.clock = root, data, clock
        self.cfg = cfgmod.load(root)
        self.bus = Bus()
        self.db = DB(data / "karspark.db")
        self.log = AuditLog(data / "logs.db", self.bus)
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30, read=1800), limits=httpx.Limits(max_connections=200))
        self.keys = Keys(self.db, self.cfg.tiers, pepper)
        self.ratelimit = RateLimiter()
        self.jobs = JobStore(self.db, self.log, data, clock)
        self.files = Files(self.db, data, clock)
        kw = {"mem_free": mem_free} if mem_free else {}
        self.engines = EngineManager(self.cfg.recipes, runner or DockerRunner(host_paths()), self.log, self.http,
                                     poll_s=poll_s, on_change=lambda e: self.bus.publish("status", {"engine": e.recipe.id}),
                                     **kw)
        self.workers = Workers(self.cfg, self.jobs, self.files, self.keys, self.engines, self.log, self.bus, self.http)
        self.sched = Scheduler(self.cfg, self.db, self.jobs, self.files, self.engines, self.log, self.bus, self.keys,
                               self.workers, clock, tick_s,
                               thermal=thermal or (hottest_c if background else (lambda: None)))
        self.resources = Resources(self.log)
        self.svc = Service(self)
        self.adopt, self.background = adopt, background
        self.tasks: list[asyncio.Task] = []

    async def start(self):
        await self.db.open()
        await self.log.open()
        legacy = os.environ.get("KARSPARK_API_KEY")
        if legacy and not await self.db.one("SELECT 1 AS x FROM keys LIMIT 1"):
            await self.keys.create(LEGACY_KEY_NAME, "owner", secret=legacy)
            self.log.scheduler_event("key_imported", name=LEGACY_KEY_NAME, tier="owner")
        await self.sched.start(adopt=self.adopt)
        if self.background:
            self.tasks += [asyncio.create_task(self.resources.run()), asyncio.create_task(maintenance(self)),
                           asyncio.create_task(self._watch_engines())]

    async def _watch_engines(self):
        while True:
            await asyncio.sleep(15)
            with contextlib.suppress(Exception):
                await self.engines.watch_once()

    async def stop(self):
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        for t in self.tasks:
            t.cancel()
        await self.sched.stop()
        await self.log.close()
        await self.db.close()
        await self.http.aclose()


def create_app(k: Karspark | None = None, with_mcp=True) -> FastAPI:
    if k is None:
        k = Karspark(Path(os.environ.get("KARSPARK_ROOT", "/opt/karspark")), Path(os.environ.get("KARSPARK_DATA", "/data")),
                     os.environ["KARSPARK_KEY_PEPPER"])
    mcp = None
    if with_mcp:
        from .mcp_server import build_mcp
        mcp = build_mcp(k)

    relay = {}

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await k.start()
        async with (mcp.session_manager.run() if mcp else contextlib.nullcontext()):
            if relay.get("fn"):
                k.tasks.append(asyncio.create_task(relay["fn"]()))
            try:
                yield
            finally:
                await k.stop()

    app = FastAPI(title="karspark", version="1.0", lifespan=lifespan, docs_url="/docs", redoc_url=None)
    app.state.k = k

    @app.exception_handler(JobError)
    async def job_error(request: Request, e: JobError):
        return err(e.status, str(e))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, e: HTTPException):
        typ = {401: "authentication_error", 403: "permission_error", 404: "not_found_error",
               429: "rate_limit_error"}.get(e.status_code, "invalid_request_error")
        return JSONResponse({"error": {"message": e.detail, "type": typ}}, status_code=e.status_code,
                            headers=getattr(e, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, e: RequestValidationError):
        return err(400, str(e.errors()[:3]))

    @app.get("/health")
    async def health():
        return {"ok": True, "mode": k.sched.state.current, "switching": k.sched.switching}

    app.include_router(text_router)
    app.include_router(jobs_router)
    app.include_router(admin_router)
    from .ui import router as ui_router
    app.include_router(ui_router)
    if mcp:
        from .mcp_server import mount
        relay["fn"] = mount(app, k, mcp)
    app.add_middleware(AuditMiddleware, log=k.log, mode_getter=lambda: k.sched.state.current)
    return app


def __getattr__(name):  # `uvicorn karspark.app:app` builds the app lazily (tests import this module freely)
    if name == "app":
        return create_app()
    raise AttributeError(name)
