"""In-process pub/sub: job state changes, mode switches and status updates fan out to SSE streams,
webhooks and MCP resource notifications. Slow subscribers drop events rather than block the loop."""
from __future__ import annotations

import asyncio
import contextlib


class Bus:
    def __init__(self):
        self.subs: set[asyncio.Queue] = set()

    def publish(self, topic: str, data: dict):
        for q in list(self.subs):
            try:
                q.put_nowait((topic, data))
            except asyncio.QueueFull:
                pass

    @contextlib.contextmanager
    def subscribe(self, maxsize=1000):
        q: asyncio.Queue = asyncio.Queue(maxsize)
        self.subs.add(q)
        try:
            yield q
        finally:
            self.subs.discard(q)
