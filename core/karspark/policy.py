"""Scheduling policy as pure functions of (state, clock), so every rule is unit-tested on a fake clock.

Order of dispatch inside one engine's queue (docs/scheduler.md):
  1. strict priority class (P0 > P1 > P2 > P3), after aging: a job waiting `age_after_s[c]` in class c
     moves up one class (never into P0), so nothing starves;
  2. weighted fair share between keys inside a class (virtual time; tier weight), so one project's flood
     can't starve another at the same level;
  3. inside one key: jobs that were preempted first, then FIFO;
  4. media affinity: among the winning class, a job for the model family ComfyUI already holds may jump
     ahead of the winner, but the winner can be passed over at most `affinity_skip_budget` times.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Item:
    """What the policy needs to know about one waiting unit of work (a job or a text request)."""
    id: str
    key_id: str
    weight: int
    priority: int            # effective priority when it (re)entered the queue
    enqueued: float
    created: float
    model: str = ""
    skipped: int = 0
    preempted: bool = False  # paused by the system: resumes first within its key


def family(model: str) -> str | None:
    """ComfyUI model families: switching family costs a 30-60 s weight load; None = negligible."""
    if model.startswith("flux2"):
        return "flux2"
    if model in ("esrgan-anime", "esrgan-animevideo"):
        return None
    return model or None


def aged_class(item: Item, now: float, age_after_s: dict[int, int]) -> int:
    p, waited = item.priority, now - item.enqueued
    while p > 1 and p in age_after_s and waited >= age_after_s[p]:
        waited -= age_after_s[p]
        p -= 1
    return p


@dataclass
class FairShare:
    """Start-time fair queuing between keys. vt[key] advances by 1/weight each dispatch; a key that was
    idle restarts at the current virtual time instead of spending credit it banked while idle."""
    vt: dict[str, float] = field(default_factory=dict)
    now_vt: float = 0.0

    def tag(self, key_id: str) -> float:
        return max(self.vt.get(key_id, 0.0), self.now_vt)

    def charge(self, key_id: str, weight: int):
        start = self.tag(key_id)
        self.now_vt = start
        self.vt[key_id] = start + 1.0 / max(1, weight)


def order(items: list[Item], now: float, age_after_s: dict[int, int], fair: FairShare) -> list[Item]:
    """Full dispatch order (for the `queue` view and ETAs). Simulates fair-share charging as it goes."""
    sim = FairShare(dict(fair.vt), fair.now_vt)
    left = list(items)
    out = []
    while left:
        best = _best(left, now, age_after_s, sim)
        out.append(best)
        left.remove(best)
        sim.charge(best.key_id, best.weight)
    return out


def _best(items, now, age_after_s, fair):
    return min(items, key=lambda i: (aged_class(i, now, age_after_s), fair.tag(i.key_id), not i.preempted,
                                     i.created, i.id))


@dataclass
class Pick:
    item: Item
    reason: str
    bypassed: Item | None = None  # passed over for affinity (its skip count goes up)


def pick(items: list[Item], now: float, age_after_s: dict[int, int], fair: FairShare,
         loaded_family: str | None = None, skip_budget: int = 0) -> Pick | None:
    if not items:
        return None
    best = _best(items, now, age_after_s, fair)
    cls = aged_class(best, now, age_after_s)
    reason = f"P{cls}" + (f" (aged from P{best.priority})" if cls < best.priority else "")
    fam = family(best.model)
    if loaded_family and fam not in (None, loaded_family) and best.skipped < skip_budget:
        same = [i for i in items if aged_class(i, now, age_after_s) == cls and family(i.model) in (None, loaded_family)]
        if same:
            alt = _best(same, now, age_after_s, fair)
            return Pick(alt, f"P{cls}, {loaded_family} already loaded", bypassed=best)
    return Pick(best, reason)


def should_preempt(running_priority: int, running_preemptions: int, head_class: int, head_can_preempt: bool,
                   preempt_class: int, max_preemptions: int) -> bool:
    """P0/P1 work from a tier allowed to preempt may pause a running P3 job (progress kept), at most
    max_preemptions times per job. Text streams are never preempted (they aren't jobs)."""
    return (head_can_preempt and head_class <= 1 and running_priority >= preempt_class
            and running_preemptions < max_preemptions)


# ---------------------------------------------------------------- modes
@dataclass
class ModeState:
    current: str
    since: float
    last_coding: float = 0.0          # last coding request (start of the hold)
    last_activity: float = 0.0        # last request or running job of any kind
    pinned_until: float = 0.0         # mode requested with hold_minutes
    pending: dict | None = None       # {"mode", "key_id", "project", "immediate": bool, "t"}


@dataclass
class Demand:
    """What is waiting or running, per mode, in a form the controller can weigh."""
    waiting: dict[str, list[int]]     # mode -> priority classes of waiting work that only that mode can serve
    running_here: int                 # jobs/streams running in the current mode
    coding_waiting: bool              # a coding request is waiting (P0)


def decide_mode(st: ModeState, d: Demand, now: float, coding_mode: str | None, default_mode: str,
                coding_hold_s: int, idle_return_s: int) -> tuple[str, str] | None:
    """Returns (target mode, reason) when the box should switch now, else None."""
    cur = st.current
    if coding_mode and d.coding_waiting and cur != coding_mode:
        return coding_mode, "coding request (P0)"
    in_hold = cur == coding_mode and now - st.last_coding < coding_hold_s
    pinned = now < st.pinned_until

    if st.pending and st.pending["mode"] != cur:
        if st.pending.get("immediate"):
            return st.pending["mode"], f"requested by {st.pending.get('project')}"
        # A request waits out the coding hold and a pin, and never cuts in front of work the current mode
        # is running or has waiting at the same or a more urgent priority.
        req_p = st.pending.get("priority", 2)
        if (not in_hold and not pinned and not d.running_here
                and not any(p <= req_p for p in d.waiting.get(cur, []))):
            return st.pending["mode"], f"requested by {st.pending.get('project')}"

    if in_hold or pinned:
        return None
    others = {m: ps for m, ps in d.waiting.items() if m != cur and ps and m != coding_mode}
    if others and not d.running_here and not d.waiting.get(cur):
        # Most urgent waiting work first, then the most of it.
        target = min(others, key=lambda m: (min(others[m]), -len(others[m]), m))
        return target, f"{len(others[target])} waiting job(s) need {target}"
    if (cur != default_mode and not d.running_here and not any(d.waiting.values())
            and now - st.last_activity >= idle_return_s):
        return default_mode, f"idle {int(now - st.last_activity)}s"
    return None
