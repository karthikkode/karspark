"""Static configuration: recipes, modes, tiers and scheduler settings, all from the repo.

  recipes/<model>/<recipe>.yaml   how one engine (container) is started and what it serves
  config/modes.yaml               which engines are loaded together, and the mode rules
  config/tiers.yaml               API key tiers: priority ceilings, limits, fair-share weights
  config/scheduler.yaml           aging, preemption, timeouts, retention

load() validates everything and raises ConfigError listing every problem, so a bad config never
reaches the Spark (scripts/deploy.sh runs `python -m karspark.config` first).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MEMORY_LIMIT_GIB = 115  # of 121 GiB unified; the OS and desktop need the rest
CAPABILITIES = ("text", "image", "layers", "video", "upscale_image", "upscale_video", "speech")
JOB_KINDS = ("text", "image", "layers", "video", "upscale_image", "upscale_video")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Recipe:
    id: str                 # <model>:<recipe>
    kind: str               # text | media
    port: int               # fixed host port on 127.0.0.1 (lets core adopt a running engine after a restart)
    memory_gib: int         # must be free before start (ks-docker KS_NEED_GIB)
    models: tuple[str, ...]  # public model names served
    run: str                # docker run arguments after the image-independent prefix
    slots: int = 1          # concurrent requests (text) or jobs (media)
    health: str = "/health"
    load_timeout_s: int = 1800
    stop_timeout_s: int = 60
    capabilities: tuple[str, ...] = ("text",)
    served_name: str | None = None  # model name the backend expects (text engines)

    @property
    def container(self) -> str:
        return "ks-" + self.id.replace(":", "-")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@dataclass(frozen=True)
class Mode:
    name: str
    engines: tuple[str, ...]  # recipe ids
    text: str | None          # public text model served in this mode (what the `text` alias means)
    coding: bool = False


@dataclass(frozen=True)
class Tier:
    name: str
    ceiling: int            # highest priority allowed (0 = P0)
    default: int
    coding: bool            # may use coding mode (and so P0)
    mode_change: str        # immediate | request
    preempt: bool
    weight: int             # fair-share weight inside a priority class
    rpm: int | None
    tpm: int | None
    max_running: int
    max_queued: int
    max_upload_mb: int
    retention_days: int


@dataclass(frozen=True)
class SchedulerCfg:
    age_after_s: dict[int, int]       # class -> seconds waited before promotion to the next class
    max_preemptions: int
    affinity_skip_budget: int         # times a job may be passed over for a loaded-model job
    preempt_class: int                # only jobs of this class can be preempted
    job_timeout_s: dict[str, int]
    coding_hold_s: int
    idle_return_s: int
    default_mode: str
    stream_grace_s: int               # text streams get this long to finish before a mode switch
    keepalive_s: int
    bodies_days: int
    meta_days: int


@dataclass
class Config:
    recipes: dict[str, Recipe]
    modes: dict[str, Mode]
    tiers: dict[str, Tier]
    sched: SchedulerCfg
    model_index: dict[str, list[str]] = field(default_factory=dict)  # public model -> recipe ids

    def mode_of_model(self, model: str) -> list[str]:
        """Modes in which a public model is served."""
        rids = set(self.model_index.get(model, []))
        return [m.name for m in self.modes.values() if rids & set(m.engines)]

    def engine_for(self, mode: str, model: str) -> Recipe | None:
        for rid in self.modes[mode].engines:
            if model in self.recipes[rid].models:
                return self.recipes[rid]
        return None

    def media_engine(self, mode: str) -> Recipe | None:
        for rid in self.modes[mode].engines:
            if self.recipes[rid].kind == "media":
                return self.recipes[rid]
        return None

    def capability_models(self, cap: str) -> list[str]:
        out = []
        for r in self.recipes.values():
            if cap in r.capabilities:
                out += [m for m in r.models if m not in out]
        return out


def _load_yaml(p: Path):
    with open(p) as f:
        return yaml.safe_load(f) or {}


def load(root: Path) -> Config:
    """root = the repo (or the copy deployed to the Spark): needs recipes/ and config/."""
    errors: list[str] = []
    recipes: dict[str, Recipe] = {}
    ports: dict[int, str] = {}
    for path in sorted(root.glob("recipes/*/*.yaml")):
        rid = f"{path.parent.name}:{path.stem}"
        where = str(path.relative_to(root))
        try:
            d = _load_yaml(path)
            r = Recipe(id=rid, kind=d["kind"], port=int(d["port"]), memory_gib=int(d["memory_gib"]),
                       models=tuple(d["models"]), run=d["run"], slots=int(d.get("slots", 1)),
                       health=d.get("health", "/health"), load_timeout_s=int(d.get("load_timeout_s", 1800)),
                       stop_timeout_s=int(d.get("stop_timeout_s", 60)),
                       capabilities=tuple(d.get("capabilities", ["text"])), served_name=d.get("served_name"))
        except (KeyError, TypeError, ValueError) as e:
            errors.append(f"{where}: missing or bad field ({e})")
            continue
        errors += [f"{where}: {e}" for e in _check_recipe(r)]
        if r.port in ports:
            errors.append(f"{where}: port {r.port} already used by {ports[r.port]}")
        ports[r.port] = rid
        recipes[rid] = r

    index: dict[str, list[str]] = {}
    for r in recipes.values():
        for m in r.models:
            index.setdefault(m, []).append(r.id)
    if "text" in index:
        errors.append("'text' is a reserved alias; no recipe may serve a model called text")

    md = _load_yaml(root / "config/modes.yaml")
    modes: dict[str, Mode] = {}
    for name, m in (md.get("modes") or {}).items():
        mode = Mode(name=name, engines=tuple(m.get("engines", [])), text=m.get("text"), coding=bool(m.get("coding")))
        for rid in mode.engines:
            if rid not in recipes:
                errors.append(f"mode {name}: unknown recipe {rid}")
        used = sum(recipes[r].memory_gib for r in mode.engines if r in recipes)
        if used > MEMORY_LIMIT_GIB:
            errors.append(f"mode {name}: engines need {used} GiB together, limit {MEMORY_LIMIT_GIB}")
        if mode.text and not any(mode.text in recipes[r].models for r in mode.engines if r in recipes):
            errors.append(f"mode {name}: text model {mode.text} is not served by its engines")
        if sum(1 for r in mode.engines if r in recipes and recipes[r].kind == "media") > 1:
            errors.append(f"mode {name}: at most one media engine")
        modes[name] = mode
    if not modes:
        errors.append("config/modes.yaml: no modes")
    if sum(1 for m in modes.values() if m.coding) > 1:
        errors.append("config/modes.yaml: only one coding mode")

    td = _load_yaml(root / "config/tiers.yaml")
    tiers: dict[str, Tier] = {}
    for name, t in (td.get("tiers") or {}).items():
        try:
            tier = Tier(name=name, ceiling=int(t["ceiling"]), default=int(t["default"]), coding=bool(t.get("coding")),
                        mode_change=t.get("mode_change", "request"), preempt=bool(t.get("preempt")),
                        weight=int(t["weight"]), rpm=t.get("rpm"), tpm=t.get("tpm"),
                        max_running=int(t["max_running"]), max_queued=int(t["max_queued"]),
                        max_upload_mb=int(t.get("max_upload_mb", 64)), retention_days=int(t.get("retention_days", 7)))
        except (KeyError, TypeError, ValueError) as e:
            errors.append(f"tier {name}: missing or bad field ({e})")
            continue
        if not 0 <= tier.ceiling <= tier.default <= 3:
            errors.append(f"tier {name}: need 0 <= ceiling <= default <= 3")
        if tier.mode_change not in ("immediate", "request"):
            errors.append(f"tier {name}: mode_change must be immediate or request")
        tiers[name] = tier
    if "owner" not in tiers:
        errors.append("config/tiers.yaml: an 'owner' tier is required")

    sd = _load_yaml(root / "config/scheduler.yaml")
    try:
        sched = SchedulerCfg(
            age_after_s={int(k): int(v) for k, v in sd["age_after_s"].items()},
            max_preemptions=int(sd.get("max_preemptions", 3)),
            affinity_skip_budget=int(sd.get("affinity_skip_budget", 3)),
            preempt_class=int(sd.get("preempt_class", 3)),
            job_timeout_s={k: int(v) for k, v in sd["job_timeout_s"].items()},
            coding_hold_s=int(md.get("coding_hold_s", 1800)),
            idle_return_s=int(md.get("idle_return_s", 3600)),
            default_mode=md.get("default_mode", ""),
            stream_grace_s=int(md.get("stream_grace_s", 60)),
            keepalive_s=int(sd.get("keepalive_s", 15)),
            bodies_days=int(sd.get("bodies_days", 30)),
            meta_days=int(sd.get("meta_days", 365)),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ConfigError("\n".join(errors + [f"config/scheduler.yaml: missing or bad field ({e})"]))
    if sched.default_mode not in modes:
        errors.append(f"config/modes.yaml: default_mode {sched.default_mode!r} is not a mode")
    for k in JOB_KINDS:
        if k not in sched.job_timeout_s:
            errors.append(f"config/scheduler.yaml: job_timeout_s.{k} missing")

    if errors:
        raise ConfigError("\n".join(errors))
    return Config(recipes=recipes, modes=modes, tiers=tiers, sched=sched, model_index=index)


def _check_recipe(r: Recipe) -> list[str]:
    e = []
    if r.kind not in ("text", "media"):
        e.append("kind must be text or media")
    for c in r.capabilities:
        if c not in CAPABILITIES:
            e.append(f"unknown capability {c}")
    if not 10001 <= r.port <= 10999:
        e.append("port must be in 10001..10999")
    if "-p 127.0.0.1:${PORT}:" not in r.run:
        e.append("must publish only on 127.0.0.1:${PORT}")
    if re.search(r"(^|\s)-p\s+(?!127\.0\.0\.1:)", r.run) or "--network host" in r.run or "--net=host" in r.run:
        e.append("publishes on a non-loopback address")
    if re.search(r"@sha256:[0-9a-f]{64}|(^|\s)karspark/\S+:\S+", r.run) is None:
        e.append("pin the image by digest, or use a karspark/ image built here")
    if "HF_HUB_OFFLINE=1" not in r.run:
        e.append("set -e HF_HUB_OFFLINE=1 (weights come from ${models}, never a cache)")
    if "${models}" not in r.run:
        e.append("mount weights from ${models}/<kind>/<name>")
    if re.search(r"(api[-_]?key|token)\s*[=:]?\s*[A-Za-z0-9_\-]{20,}", r.run, re.I):
        e.append("looks like a literal secret")
    if "~/scratch" in r.run or "/scratch/" in r.run:
        e.append("nothing may depend on ~/scratch")
    return e


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    try:
        cfg = load(root)
    except ConfigError as e:
        print("karspark config: refusing\n  " + str(e).replace("\n", "\n  "), file=sys.stderr)
        sys.exit(1)
    for m in cfg.modes.values():
        used = sum(cfg.recipes[r].memory_gib for r in m.engines)
        print(f"mode {m.name:<9} {used:>3} GiB  {', '.join(m.engines)}  text={m.text}")
