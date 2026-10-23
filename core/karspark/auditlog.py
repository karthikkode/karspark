"""The logging service: every request in detail, plus job, scheduler and engine events (~/data/logs.db).

Writes go through an asyncio queue and are inserted in batches by one task, so logging never slows a
request. Bodies (prompts, completions, job parameters) are kept `bodies_days`; metadata `meta_days`.
Media is never stored inline: job inputs and outputs are file references.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import aiosqlite

from .db import row_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  request_id TEXT PRIMARY KEY, t REAL NOT NULL, key_id TEXT, project TEXT, tier TEXT, ip TEXT,
  method TEXT, path TEXT, tool TEXT, model TEXT, mode TEXT, priority_req INTEGER, priority INTEGER,
  status INTEGER, error TEXT, total_ms REAL, queue_ms REAL, ttft_ms REAL, gen_ms REAL,
  tokens_in INTEGER, tokens_out INTEGER, bytes_in INTEGER, bytes_out INTEGER, job_id TEXT, stream INTEGER
);
CREATE INDEX IF NOT EXISTS requests_t ON requests(t);
CREATE INDEX IF NOT EXISTS requests_key ON requests(key_id, t);
CREATE TABLE IF NOT EXISTS bodies (
  request_id TEXT PRIMARY KEY, t REAL NOT NULL, request TEXT, response TEXT, truncated INTEGER
);
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, job_id TEXT NOT NULL, state TEXT NOT NULL,
  actor TEXT, reason TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id, id);
CREATE TABLE IF NOT EXISTS scheduler_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, kind TEXT NOT NULL, data TEXT
);
CREATE INDEX IF NOT EXISTS scheduler_events_t ON scheduler_events(t);
CREATE TABLE IF NOT EXISTS engine_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, engine TEXT NOT NULL, kind TEXT NOT NULL, data TEXT
);
CREATE TABLE IF NOT EXISTS resource_samples (
  t REAL PRIMARY KEY, mem_used_gib REAL, mem_avail_gib REAL, gpu_util REAL, gpu_temp REAL, gpu_power_w REAL
);
"""
BODY_CAP = 1 << 20  # 1 MiB per side; longer bodies are truncated (and flagged)

REQ_COLS = ("request_id", "t", "key_id", "project", "tier", "ip", "method", "path", "tool", "model", "mode",
            "priority_req", "priority", "status", "error", "total_ms", "queue_ms", "ttft_ms", "gen_ms",
            "tokens_in", "tokens_out", "bytes_in", "bytes_out", "job_id", "stream")


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:20]


def _clip(s):
    if s is None:
        return None, 0
    if not isinstance(s, str):
        s = json.dumps(s, ensure_ascii=False, default=str)
    return (s[:BODY_CAP], 1) if len(s) > BODY_CAP else (s, 0)


class AuditLog:
    def __init__(self, path: Path, bus=None, clock=time.time):
        self.path, self.bus, self.clock = path, bus, clock
        self.q: asyncio.Queue = asyncio.Queue()
        self.conn: aiosqlite.Connection | None = None
        self.task: asyncio.Task | None = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = row_dict
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        self.task = asyncio.create_task(self._writer())
        return self

    async def close(self):
        await self.flush()
        if self.task:
            self.task.cancel()
        if self.conn:
            await self.conn.close()

    async def flush(self):
        await self.q.join()

    # ---------------------------------------------------------------- producers (never block)
    def request(self, row: dict, req_body=None, resp_body=None):
        row = {c: row.get(c) for c in REQ_COLS}
        row["t"] = row["t"] or self.clock()
        self.q.put_nowait(("INSERT OR REPLACE INTO requests (%s) VALUES (%s)" % (
            ", ".join(REQ_COLS), ", ".join("?" * len(REQ_COLS))), tuple(row[c] for c in REQ_COLS)))
        if req_body is not None or resp_body is not None:
            rq, t1 = _clip(req_body)
            rs, t2 = _clip(resp_body)
            self.q.put_nowait(("INSERT OR REPLACE INTO bodies VALUES (?, ?, ?, ?, ?)",
                               (row["request_id"], row["t"], rq, rs, t1 | t2)))

    def job_event(self, job_id, state, actor=None, reason=None, data=None):
        t = self.clock()
        self.q.put_nowait(("INSERT INTO job_events (t, job_id, state, actor, reason, data) VALUES (?, ?, ?, ?, ?, ?)",
                           (t, job_id, state, actor, reason, json.dumps(data) if data else None)))
        if self.bus:
            self.bus.publish("job", {"t": t, "job_id": job_id, "state": state, "actor": actor, "reason": reason,
                                     "data": data})

    def scheduler_event(self, kind, **data):
        t = self.clock()
        self.q.put_nowait(("INSERT INTO scheduler_events (t, kind, data) VALUES (?, ?, ?)",
                           (t, kind, json.dumps(data, default=str))))
        if self.bus:
            self.bus.publish("scheduler", {"t": t, "kind": kind, **data})

    def engine_event(self, engine, kind, **data):
        t = self.clock()
        self.q.put_nowait(("INSERT INTO engine_events (t, engine, kind, data) VALUES (?, ?, ?, ?)",
                           (t, engine, kind, json.dumps(data, default=str))))
        if self.bus:
            self.bus.publish("engine", {"t": t, "engine": engine, "kind": kind, **data})

    def sample(self, **s):
        self.q.put_nowait(("INSERT OR REPLACE INTO resource_samples VALUES (?, ?, ?, ?, ?, ?)",
                           (self.clock(), s.get("mem_used_gib"), s.get("mem_avail_gib"), s.get("gpu_util"),
                            s.get("gpu_temp"), s.get("gpu_power_w"))))

    # ---------------------------------------------------------------- writer
    async def _writer(self):
        while True:
            first = await self.q.get()
            batch = [first]
            while len(batch) < 500:
                try:
                    batch.append(self.q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                for sql, args in batch:
                    await self.conn.execute(sql, args)
                await self.conn.commit()
            except Exception as e:  # never let logging take the service down
                print(f"[auditlog] write failed: {e!r}", flush=True)
            finally:
                for _ in batch:
                    self.q.task_done()

    # ---------------------------------------------------------------- queries
    async def all(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchall()

    async def job_events(self, job_id):
        rows = await self.all("SELECT t, state, actor, reason, data FROM job_events WHERE job_id = ? ORDER BY id",
                              (job_id,))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    async def requests(self, key_id=None, model=None, since=None, status=None, path=None, limit=100):
        where, args = [], []
        for col, v, op in (("key_id", key_id, "="), ("model", model, "="), ("t", since, ">="),
                           ("status", status, "="), ("path", path, "=")):
            if v is not None:
                where.append(f"{col} {op} ?")
                args.append(v)
        sql = "SELECT * FROM requests" + (" WHERE " + " AND ".join(where) if where else "")
        return await self.all(sql + " ORDER BY t DESC LIMIT ?", (*args, min(int(limit), 1000)))

    async def request_detail(self, request_id):
        rows = await self.all("SELECT r.*, b.request AS request_body, b.response AS response_body, b.truncated "
                              "FROM requests r LEFT JOIN bodies b USING (request_id) WHERE r.request_id = ?",
                              (request_id,))
        return rows[0] if rows else None

    async def usage(self, since, key_id=None):
        where, args = "t >= ?", [since]
        if key_id:
            where += " AND key_id = ?"
            args.append(key_id)
        return await self.all(
            f"SELECT project, key_id, COUNT(*) AS requests, SUM(COALESCE(tokens_in, 0)) AS tokens_in, "
            f"SUM(COALESCE(tokens_out, 0)) AS tokens_out, SUM(status >= 400) AS errors, "
            f"COUNT(job_id) AS jobs FROM requests WHERE {where} GROUP BY key_id ORDER BY requests DESC", args)

    async def scheduler_events(self, since=0, limit=100):
        rows = await self.all("SELECT t, kind, data FROM scheduler_events WHERE t >= ? ORDER BY id DESC LIMIT ?",
                              (since, limit))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    async def cleanup(self, bodies_days, meta_days):
        now = self.clock()
        b, m = now - bodies_days * 86400, now - meta_days * 86400
        for sql, args in (("DELETE FROM bodies WHERE t < ?", (b,)), ("DELETE FROM requests WHERE t < ?", (m,)),
                          ("DELETE FROM job_events WHERE t < ?", (m,)), ("DELETE FROM scheduler_events WHERE t < ?", (m,)),
                          ("DELETE FROM engine_events WHERE t < ?", (m,)),
                          ("DELETE FROM resource_samples WHERE t < ?", (m,))):
            await self.conn.execute(sql, args)
        await self.conn.commit()


class AuditMiddleware:
    """Pure ASGI: one `requests` row per HTTP call with timings, sizes and (capped) bodies.

    Handlers add detail through `scope["state"]["log"]` (a dict): key/project/tier, model, priorities,
    tokens, ttft, job_id, and for streams the assembled response text (`response_body`).
    """

    SKIP = ("/health", "/metrics", "/ui/static")

    def __init__(self, app, log: AuditLog, mode_getter=lambda: None):
        self.app, self.log, self.mode_getter = app, log, mode_getter

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(self.SKIP):
            return await self.app(scope, receive, send)
        rid = new_request_id()
        info: dict = {"request_id": rid}
        scope.setdefault("state", {})["log"] = info
        t0 = time.perf_counter()
        req_chunks, resp_chunks = [], []
        sizes = {"in": 0, "out": 0}
        status = {"code": 500, "json": False}

        async def recv():
            msg = await receive()
            if msg["type"] == "http.request":
                body = msg.get("body", b"")
                sizes["in"] += len(body)
                if sizes["in"] <= BODY_CAP:
                    req_chunks.append(body)
            return msg

        async def snd(msg):
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
                headers = list(msg.get("headers", []))
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                status["json"] = b"json" in ctype
                headers.append((b"x-request-id", rid.encode()))
                msg = {**msg, "headers": headers}
            elif msg["type"] == "http.response.body":
                body = msg.get("body", b"")
                sizes["out"] += len(body)
                if status["json"] and sizes["out"] <= BODY_CAP:
                    resp_chunks.append(body)
            await send(msg)

        try:
            await self.app(scope, recv, snd)
        finally:
            client = scope.get("client") or (None,)
            row = {**{k: v for k, v in info.items() if k in REQ_COLS}, "t": time.time(),
                   "method": scope["method"], "path": scope["path"], "ip": client[0], "status": status["code"],
                   "total_ms": round((time.perf_counter() - t0) * 1000, 1), "bytes_in": sizes["in"],
                   "bytes_out": sizes["out"], "mode": info.get("mode") or self.mode_getter()}
            req_body = info.get("request_body")
            if req_body is None and req_chunks and not info.get("skip_request_body"):
                req_body = b"".join(req_chunks).decode("utf-8", "replace")
            if scope["path"] == "/mcp" and req_body and not row.get("tool"):
                row["tool"] = _mcp_tool(req_body)
            resp_body = info.get("response_body")
            if resp_body is None and resp_chunks:
                resp_body = b"".join(resp_chunks).decode("utf-8", "replace")
            self.log.request(row, req_body, resp_body)


def _mcp_tool(body: str):
    """The tool (or method) of an MCP JSON-RPC call, for the request log."""
    try:
        msg = json.loads(body)
    except ValueError:
        return None
    if isinstance(msg, list):
        msg = msg[0] if msg else {}
    if not isinstance(msg, dict):
        return None
    if msg.get("method") == "tools/call":
        return (msg.get("params") or {}).get("name")
    return msg.get("method")
