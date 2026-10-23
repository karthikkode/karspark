"""karspark.db: keys, jobs, files and small state (WAL, one connection, one writer: the event loop).

The request log lives in its own database (auditlog.py) so heavy logging never contends with job state.
"""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
  key_id    TEXT PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE,      -- project name
  tier      TEXT NOT NULL,
  hash      TEXT NOT NULL UNIQUE,      -- HMAC-SHA256(pepper, key)
  hint      TEXT NOT NULL,             -- last 4 characters, for humans
  scopes    TEXT NOT NULL DEFAULT '[]',
  overrides TEXT NOT NULL DEFAULT '{}',
  created   REAL NOT NULL,
  revoked   REAL,
  last_used REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id           TEXT PRIMARY KEY,
  key_id       TEXT NOT NULL,
  project      TEXT NOT NULL,
  kind         TEXT NOT NULL,
  model        TEXT NOT NULL,
  params       TEXT NOT NULL,
  inputs       TEXT NOT NULL DEFAULT '{}',
  priority_req INTEGER,                -- as asked (NULL = tier default)
  priority     INTEGER NOT NULL,       -- effective (after ceiling and aging)
  state        TEXT NOT NULL,
  created      REAL NOT NULL,
  enqueued     REAL NOT NULL,          -- when it last entered the queue (aging clock)
  started      REAL,
  finished     REAL,
  run_s        REAL NOT NULL DEFAULT 0,
  attempts     INTEGER NOT NULL DEFAULT 0,
  preemptions  INTEGER NOT NULL DEFAULT 0,
  skipped      INTEGER NOT NULL DEFAULT 0,
  paused_by    TEXT,                   -- 'user' | 'system'
  progress     TEXT,
  outputs      TEXT,
  error        TEXT,
  idem_key     TEXT,
  webhook      TEXT,
  request_id   TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS jobs_key ON jobs(key_id, created);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idem ON jobs(key_id, idem_key) WHERE idem_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS files (
  id       TEXT PRIMARY KEY,
  key_id   TEXT NOT NULL,
  job_id   TEXT,                       -- set for job outputs
  name     TEXT NOT NULL,
  path     TEXT NOT NULL,              -- relative to the data dir
  size     INTEGER NOT NULL,
  mime     TEXT NOT NULL,
  created  REAL NOT NULL,
  expires  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS files_key ON files(key_id, created);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

JSON_COLS = {"params", "inputs", "progress", "outputs", "error", "scopes", "overrides"}


def row_dict(cur, row) -> dict:
    d = {}
    for (name, *_), v in zip(cur.description, row):
        d[name] = json.loads(v) if name in JSON_COLS and v is not None else v
    return d


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = row_dict
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        return self

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def all(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchall()

    async def one(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def run(self, sql, args=()):
        await self.conn.execute(sql, args)
        await self.conn.commit()

    async def insert(self, table, row: dict):
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        await self.run(f"INSERT INTO {table} ({cols}) VALUES ({qs})", [_enc(k, v) for k, v in row.items()])

    async def update(self, table, pk: str, pk_val, fields: dict):
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self.run(f"UPDATE {table} SET {sets} WHERE {pk} = ?", [*(_enc(k, v) for k, v in fields.items()), pk_val])

    async def kv_get(self, k, default=None):
        r = await self.one("SELECT v FROM kv WHERE k = ?", (k,))
        return json.loads(r["v"]) if r else default

    async def kv_set(self, k, v):
        await self.run("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                       (k, json.dumps(v)))


def _enc(k, v):
    return json.dumps(v) if k in JSON_COLS and v is not None else v
