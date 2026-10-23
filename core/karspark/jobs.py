"""Jobs: one unit of async work (an image, a video, an upscale, a long text), owned by the key that made it.

States (docs/api.md):
  queued -> running -> succeeded | failed
  running -> pausing -> paused            (user pause: checkpoint saved, slot freed; resume -> queued)
  running -> preempting -> queued         (system pause for urgent work or a mode switch: resumes first)
  queued -> paused (user) / any non-final -> cancelling -> cancelled
"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from .db import DB

FINAL = ("succeeded", "failed", "cancelled")
ACTIVE = ("running", "pausing", "preempting", "cancelling")


class JobError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def new_id(prefix="job"):
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class JobStore:
    def __init__(self, db: DB, log, data_dir: Path, clock=time.time):
        self.db, self.log, self.data, self.clock = db, log, data_dir, clock

    def dir(self, job_id) -> Path:
        return self.data / "jobs" / job_id

    async def by_idem(self, principal, idem_key):
        if not idem_key:
            return None
        return await self.db.one("SELECT * FROM jobs WHERE key_id = ? AND idem_key = ?", (principal.key_id, idem_key))

    async def create(self, principal, kind, model, params, priority_req, inputs=None, idem_key=None,
                     webhook=None, request_id=None, job_id=None) -> dict:
        now = self.clock()
        job = {"id": job_id or new_id(), "key_id": principal.key_id, "project": principal.name, "kind": kind, "model": model,
               "params": params, "inputs": inputs or {}, "priority_req": priority_req,
               "priority": principal.effective_priority(priority_req), "state": "queued", "created": now,
               "enqueued": now, "idem_key": idem_key, "webhook": webhook, "request_id": request_id}
        (self.dir(job["id"]) / "in").mkdir(parents=True, exist_ok=True)
        await self.db.insert("jobs", job)
        self.log.job_event(job["id"], "queued", actor=principal.name, reason="created",
                           data={"kind": kind, "model": model, "priority": job["priority"]})
        return await self.get(job["id"])

    async def get(self, job_id) -> dict | None:
        return await self.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    async def owned(self, principal, job_id) -> dict:
        j = await self.get(job_id)
        if not j or (j["key_id"] != principal.key_id and not principal.is_owner):
            raise JobError("no such job", 404)  # never reveal other projects' job ids
        return j

    async def list(self, key_id=None, state=None, kind=None, limit=50, before=None):
        where, args = [], []
        for col, v in (("key_id", key_id), ("kind", kind)):
            if v:
                where.append(f"{col} = ?")
                args.append(v)
        if state:
            states = state if isinstance(state, (list, tuple)) else [s for s in str(state).split(",") if s]
            where.append("state IN (%s)" % ",".join("?" * len(states)))
            args += states
        if before:
            where.append("created < ?")
            args.append(before)
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "")
        return await self.db.all(sql + " ORDER BY created DESC LIMIT ?", (*args, min(int(limit), 500)))

    async def count_open(self, key_id):
        r = await self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE key_id = ? AND state NOT IN "
                              "('succeeded','failed','cancelled')", (key_id,))
        return r["n"]

    async def waiting(self):
        return await self.db.all("SELECT * FROM jobs WHERE state = 'queued' ORDER BY created")

    async def active(self):
        return await self.db.all("SELECT * FROM jobs WHERE state IN ('running','pausing','preempting','cancelling')")

    async def set(self, job_id, state=None, actor=None, reason=None, data=None, **fields):
        if state:
            fields["state"] = state
            if state in FINAL:
                fields.setdefault("finished", self.clock())
        await self.db.update("jobs", "id", job_id, fields)
        if state:
            self.log.job_event(job_id, state, actor=actor, reason=reason, data=data)

    async def recover(self):
        """After a core restart: work that was running resumes from its checkpoints; cancels complete."""
        for j in await self.active():
            if j["state"] == "cancelling":
                await self.set(j["id"], "cancelled", actor="karspark", reason="core restarted while cancelling")
                self.purge(j["id"], keep_inputs=False)
            elif j["state"] == "pausing":
                await self.set(j["id"], "paused", actor="karspark", reason="core restarted; resume continues from checkpoint",
                               paused_by="user")
            else:
                await self.set(j["id"], "queued", actor="karspark", reason="core restarted; resumes from checkpoint",
                               paused_by="system", enqueued=self.clock())

    def purge(self, job_id, keep_inputs=True):
        d = self.dir(job_id)
        for sub in ("ckpt", "out") + (() if keep_inputs else ("in",)):
            shutil.rmtree(d / sub, ignore_errors=True)
        for f in ("PAUSE", "progress.json"):
            (d / f).unlink(missing_ok=True)

    def drop_checkpoints(self, job_id):
        shutil.rmtree(self.dir(job_id) / "ckpt", ignore_errors=True)
