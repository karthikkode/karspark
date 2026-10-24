"""Resource samples every 10 s (memory, GPU utilisation, temperature, power) for /v1/status and logs.db,
plus hourly maintenance: expired files, finished job folders, log retention."""
from __future__ import annotations

import asyncio
import glob
import shutil
import time

from .engines import mem_available_gib


THERMAL_ZONES = "/sys/class/thermal/thermal_zone*/temp"


def hottest_c(pattern=THERMAL_ZONES) -> float | None:
    """The hottest ACPI thermal zone in deg C. This is what the GB10's power-off protection reacts to; nvidia-smi's GPU
    temperature reads 8-15 C lower under load (docs/lessons.md). A sysfs read, cheap enough for every tick."""
    temps = []
    for path in glob.glob(pattern):
        try:
            with open(path) as f:
                temps.append(int(f.read().strip()) / 1000)
        except (OSError, ValueError):
            pass
    return max(temps) if temps else None


class Resources:
    def __init__(self, log, interval=10):
        self.log, self.interval = log, interval
        self.latest: dict = {}

    async def sample(self):
        s = {}
        try:
            with open("/proc/meminfo") as f:
                m = {line.split(":")[0]: int(line.split()[1]) for line in f}
            s["mem_total_gib"] = round(m["MemTotal"] / 1048576, 1)
            s["mem_avail_gib"] = round(mem_available_gib(), 1)
            s["mem_used_gib"] = round(s["mem_total_gib"] - s["mem_avail_gib"], 1)
        except (OSError, KeyError, ValueError):
            pass
        if shutil.which("nvidia-smi"):
            try:
                p = await asyncio.create_subprocess_exec(
                    "nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(p.communicate(), 10)
                parts = [x.strip() for x in out.decode().splitlines()[0].split(",")]
                for key, v in zip(("gpu_util", "gpu_temp", "gpu_power_w"), parts):
                    try:
                        s[key] = float(v)
                    except ValueError:
                        pass
            except (asyncio.TimeoutError, OSError, IndexError):
                pass
        therm = hottest_c()
        if therm is not None:
            s["therm_c"] = therm
        s["t"] = time.time()
        self.latest = s
        self.log.sample(**s)
        return s

    async def run(self):
        while True:
            try:
                await self.sample()
            except Exception as e:
                print(f"[resources] {e!r}", flush=True)
            await asyncio.sleep(self.interval)


async def maintenance(k, every_s=3600):
    """Hourly: delete expired files and finished jobs' folders past retention; trim the logs."""
    while True:
        await asyncio.sleep(60)
        try:
            for f in await k.files.expired():
                await k.files.delete(f)
            now = time.time()
            for j in await k.db.all("SELECT j.id, j.key_id, j.finished, k.tier FROM jobs j LEFT JOIN keys k USING (key_id) "
                                    "WHERE j.state IN ('succeeded','failed','cancelled') AND j.finished IS NOT NULL"):
                tier = k.cfg.tiers.get(j["tier"] or "", None)
                days = tier.retention_days if tier else 7
                if now - j["finished"] > days * 86400 and k.jobs.dir(j["id"]).exists():
                    shutil.rmtree(k.jobs.dir(j["id"]), ignore_errors=True)
            await k.log.cleanup(k.cfg.sched.bodies_days, k.cfg.sched.meta_days)
        except Exception as e:
            print(f"[maintenance] {e!r}", flush=True)
        await asyncio.sleep(every_s)
