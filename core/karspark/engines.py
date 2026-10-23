"""Engine manager: starts, stops, watches and adopts the model containers (one per recipe).

Engines run detached (`docker run -d`) on their recipe's fixed port, so when core restarts it adopts the
ones still running instead of reloading minutes of weights. Core talks to the host's Docker through the
mounted socket, so every path in a recipe is a host path (${models}, ${engines}, ${data}).

Each engine carries a hash of its recipe (label `karspark.hash`). Adoption needs the hash to match, so a
deploy that changes a recipe reloads that engine and adopts the rest unchanged.

Before a start, core waits until the recipe's `memory_gib` is really free: on unified memory a stopped
model takes a while to hand its pages back, and vLLM sizes itself from free memory when it starts.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import time
from dataclasses import dataclass, field

import httpx

from .config import Recipe

STOPPED, STARTING, READY, STOPPING, FAILED = "stopped", "starting", "ready", "stopping", "failed"


def render_run(recipe: Recipe, paths: dict[str, str]) -> list[str]:
    text = recipe.run.replace("${PORT}", str(recipe.port))
    for k, v in paths.items():
        text = text.replace("${" + k + "}", v)
    if "${" in text:
        raise ValueError(f"{recipe.id}: unresolved variable in run: {text[text.index('${'):][:30]}")
    return shlex.split(text)


def recipe_hash(recipe: Recipe) -> str:
    return hashlib.sha256(f"{recipe.id}\n{recipe.port}\n{recipe.run}".encode()).hexdigest()[:12]


class DockerRunner:
    """The real thing: the docker CLI against the host daemon."""

    def __init__(self, paths: dict[str, str]):
        self.paths = paths

    async def _docker(self, *args, timeout=120) -> tuple[int, str]:
        p = await asyncio.create_subprocess_exec("docker", *args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            p.kill()
            return 124, "timeout"
        return p.returncode, out.decode(errors="replace")

    async def start(self, recipe: Recipe):
        await self._docker("rm", "-f", recipe.container)  # leftover from a crash
        args = ["run", "-d", "--init", "--name", recipe.container, "--label", "karspark.recipe=" + recipe.id,
                "--label", "karspark.hash=" + recipe_hash(recipe), *render_run(recipe, self.paths)]
        rc, out = await self._docker(*args, timeout=300)
        if rc != 0:
            raise RuntimeError(f"docker run failed: {out.strip()[-2000:]}")

    async def stop(self, recipe: Recipe):
        await self._docker("stop", "-t", str(recipe.stop_timeout_s), recipe.container,
                           timeout=recipe.stop_timeout_s + 30)
        await self._docker("rm", "-f", recipe.container)

    async def running(self, recipe: Recipe) -> bool:
        """Running AND started by karspark for this recipe (label), so a same-named container from
        anything else (e.g. the old llama-swap gateway) is never adopted or mistaken for ours."""
        rc, out = await self._docker("inspect", "-f", '{{.State.Running}} {{index .Config.Labels "karspark.recipe"}}',
                                     recipe.container, timeout=20)
        return rc == 0 and out.split() == ["true", recipe.id]

    async def current(self, recipe: Recipe) -> bool:
        """Running, ours, and started from this exact recipe (an edited recipe must reload)."""
        rc, out = await self._docker("inspect", "-f", '{{.State.Running}} {{index .Config.Labels "karspark.recipe"}} '
                                     '{{index .Config.Labels "karspark.hash"}}', recipe.container, timeout=20)
        return rc == 0 and out.split() == ["true", recipe.id, recipe_hash(recipe)]

    async def logs(self, recipe: Recipe, lines=40) -> str:
        _, out = await self._docker("logs", "--tail", str(lines), recipe.container, timeout=20)
        return out


def mem_available_gib() -> float:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return float("inf")


@dataclass
class Engine:
    recipe: Recipe
    state: str = STOPPED
    since: float = field(default_factory=time.time)
    error: str | None = None
    load_s: float | None = None
    restarts: int = 0
    loaded_model: str | None = None   # media engines: the model family ComfyUI holds
    task: asyncio.Task | None = None

    def view(self):
        return {"recipe": self.recipe.id, "state": self.state, "since": self.since, "error": self.error,
                "load_s": self.load_s, "models": list(self.recipe.models), "loaded_model": self.loaded_model,
                "port": self.recipe.port, "memory_gib": self.recipe.memory_gib}


class EngineManager:
    def __init__(self, recipes: dict[str, Recipe], runner, log, http: httpx.AsyncClient | None = None,
                 mem_free=mem_available_gib, mem_wait_s=300, poll_s=2.0, on_change=None):
        self.engines = {rid: Engine(r) for rid, r in recipes.items()}
        self.runner, self.log = runner, log
        self.http = http or httpx.AsyncClient(timeout=5)
        self.mem_free, self.mem_wait_s, self.poll_s = mem_free, mem_wait_s, poll_s
        self.on_change = on_change or (lambda e: None)
        self.lock = asyncio.Lock()  # one start/stop at a time: memory accounting depends on it

    def _set(self, e: Engine, state, error=None):
        e.state, e.since, e.error = state, time.time(), error
        self.log.engine_event(e.recipe.id, state, error=error)
        self.on_change(e)

    async def healthy(self, r: Recipe) -> bool:
        try:
            resp = await self.http.get(r.base_url + r.health, timeout=5)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def adopt(self):
        """After a core restart: our engines that are running and healthy are taken over as-is. One still
        loading is left alone; when its mode is wanted, _start waits for it instead of restarting it."""
        for e in self.engines.values():
            if await self.runner.current(e.recipe) and await self.healthy(e.recipe):
                self._set(e, READY)
                self.log.engine_event(e.recipe.id, "adopted")
            elif await self.runner.running(e.recipe):
                self.log.engine_event(e.recipe.id, "stale")  # recipe changed: reloaded when its mode is wanted

    async def ensure(self, wanted: list[str]):
        """Make exactly `wanted` run: stop the rest first (frees memory), then start what's missing, in order."""
        async with self.lock:
            for rid, e in self.engines.items():
                if rid not in wanted and (e.state in (READY, STARTING, FAILED) or await self.runner.running(e.recipe)):
                    await self._stop(e)
            for rid in wanted:
                e = self.engines[rid]
                if e.state != READY:
                    await self._start(e)

    async def stop(self, rid):
        async with self.lock:
            await self._stop(self.engines[rid])

    async def restart(self, rid):
        async with self.lock:
            e = self.engines[rid]
            await self._stop(e)
            e.restarts += 1
            await self._start(e)

    async def _stop(self, e: Engine):
        if e.state == STOPPED and not await self.runner.running(e.recipe):
            return
        self._set(e, STOPPING)
        try:
            await self.runner.stop(e.recipe)
        finally:
            e.loaded_model = None
            self._set(e, STOPPED)

    async def _start(self, e: Engine):
        r = e.recipe
        self._set(e, STARTING)
        t0 = time.monotonic()
        waited = 0.0
        if await self.runner.current(r):  # ours, still loading from before a core restart: wait for it
            return await self._wait_healthy(e, t0)
        if await self.runner.running(r):  # ours but from an older recipe: replace it
            await self.runner.stop(r)
        while self.mem_free() < r.memory_gib:
            if waited >= self.mem_wait_s:
                self._set(e, FAILED, f"only {self.mem_free():.0f} GiB free, need {r.memory_gib}")
                raise EngineError(e.error)
            await asyncio.sleep(self.poll_s)
            waited += self.poll_s
        try:
            await self.runner.start(r)
        except Exception as ex:
            self._set(e, FAILED, str(ex))
            raise EngineError(e.error)
        await self._wait_healthy(e, t0)

    async def _wait_healthy(self, e: Engine, t0: float):
        r = e.recipe
        deadline = t0 + r.load_timeout_s
        while time.monotonic() < deadline:
            if await self.healthy(r):
                e.load_s = round(time.monotonic() - t0, 1)
                self._set(e, READY)
                self.log.engine_event(r.id, "loaded", load_s=e.load_s)
                return
            if not await self.runner.running(r):
                tail = await self.runner.logs(r)
                self._set(e, FAILED, "exited while loading: " + tail[-1500:])
                raise EngineError(e.error)
            await asyncio.sleep(self.poll_s)
        tail = await self.runner.logs(r)
        await self.runner.stop(r)
        self._set(e, FAILED, f"not healthy after {r.load_timeout_s}s: " + tail[-1500:])
        raise EngineError(e.error)

    async def watch_once(self):
        """Called periodically: a READY engine that died or stopped answering is marked failed."""
        for e in self.engines.values():
            if e.state == READY and not await self.healthy(e.recipe):
                if not await self.runner.running(e.recipe):
                    tail = await self.runner.logs(e.recipe)
                    self._set(e, FAILED, "crashed: " + tail[-1500:])
                # running but slow to answer /health under load: leave it, the next check decides

    def ready(self, rid) -> bool:
        return self.engines[rid].state == READY

    def view(self):
        return [e.view() for e in self.engines.values()]


class EngineError(Exception):
    pass


def host_paths() -> dict[str, str]:
    """Host paths for recipe variables, passed to the core container by its unit."""
    home = os.environ.get("KARSPARK_HOST_HOME", os.path.expanduser("~"))
    return {"models": os.environ.get("KARSPARK_MODELS", f"{home}/models"),
            "engines": os.environ.get("KARSPARK_ENGINES", f"{home}/engines"),
            "data": os.environ.get("KARSPARK_HOST_DATA", f"{home}/data")}
