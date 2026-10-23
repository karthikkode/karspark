from karspark.policy import (Demand, FairShare, Item, ModeState, aged_class, decide_mode, family, order, pick,
                             should_preempt)

AGE = {3: 1800, 2: 600, 1: 180}


def it(i, key="a", p=2, enq=0.0, w=2, model="flux2-dev", **kw):
    return Item(id=i, key_id=key, weight=w, priority=p, enqueued=enq, created=enq, model=model, **kw)


def test_strict_priority_then_fifo():
    items = [it("late-p2", p=2, enq=5), it("p3", p=3, enq=0), it("early-p2", p=2, enq=1), it("p1", p=1, enq=9)]
    assert [i.id for i in order(items, 10, AGE, FairShare())] == ["p1", "early-p2", "late-p2", "p3"]


def test_aging_promotes_but_never_to_p0():
    assert aged_class(it("x", p=3, enq=0), 1799, AGE) == 3
    assert aged_class(it("x", p=3, enq=0), 1800, AGE) == 2
    assert aged_class(it("x", p=3, enq=0), 1800 + 600, AGE) == 1
    assert aged_class(it("x", p=3, enq=0), 10**6, AGE) == 1


def test_fair_share_between_keys_by_weight():
    # key a (weight 4) floods 8 jobs first, key b (weight 1) has 4: a gets ~4x the turns, b is never starved.
    items = [it(f"a{n}", key="a", w=4, enq=n) for n in range(8)] + [it(f"b{n}", key="b", w=1, enq=100 + n) for n in range(4)]
    seq = [i.key_id for i in order(items, 1000, AGE, FairShare())]
    assert seq[:5].count("b") == 1 and "b" in seq[:2]
    assert seq.index("b") < 2


def test_preempted_job_resumes_first_within_key():
    items = [it("old", enq=0), it("victim", enq=5, preempted=True)]
    assert pick(items, 10, AGE, FairShare()).item.id == "victim"


def test_affinity_skip_budget():
    winner = it("ltx", model="ltx-2.5", enq=0)
    alt = it("flux", model="flux2-dev", enq=1)
    p = pick([winner, alt], 10, AGE, FairShare(), loaded_family="flux2", skip_budget=3)
    assert p.item.id == "flux" and p.bypassed.id == "ltx"
    winner.skipped = 3
    assert pick([winner, alt], 10, AGE, FairShare(), loaded_family="flux2", skip_budget=3).item.id == "ltx"
    # affinity never crosses a priority class
    urgent = it("ltx-p1", model="ltx-2.5", p=1, enq=5)
    assert pick([urgent, alt], 10, AGE, FairShare(), loaded_family="flux2", skip_budget=3).item.id == "ltx-p1"


def test_family():
    assert family("flux2-refine") == family("flux2-dev") == "flux2"
    assert family("esrgan-anime") is None
    assert family("seedvr2-7b") != family("seedvr2-3b")


def test_preempt_rules():
    assert should_preempt(3, 0, 1, True, 3, 3)
    assert not should_preempt(3, 3, 0, True, 3, 3)      # preempted enough
    assert not should_preempt(2, 0, 0, True, 3, 3)      # only batch is preemptible
    assert not should_preempt(3, 0, 1, False, 3, 3)     # tier can't preempt
    assert not should_preempt(3, 0, 2, True, 3, 3)      # P2 never preempts


def D(waiting=None, running=0, coding=False):
    return Demand(waiting=waiting or {}, running_here=running, coding_waiting=coding)


def kw(**o):
    base = dict(coding_mode="coding", default_mode="coding", coding_hold_s=1800, idle_return_s=3600)
    return {**base, **o}


def test_coding_request_wins_immediately():
    st = ModeState("creative", 0, last_activity=0)
    assert decide_mode(st, D({"creative": [1]}, running=1, coding=True), 10, **kw())[0] == "coding"


def test_coding_hold_blocks_demand_switch_then_releases():
    st = ModeState("coding", 0, last_coding=100, last_activity=100)
    d = D({"creative": [2]})
    assert decide_mode(st, d, 100 + 1799, **kw()) is None
    assert decide_mode(st, d, 100 + 1800, **kw())[0] == "creative"


def test_owner_request_is_immediate_standard_request_waits_for_hold():
    st = ModeState("coding", 0, last_coding=100, last_activity=100,
                   pending={"mode": "creative", "project": "x", "immediate": False, "priority": 2})
    assert decide_mode(st, D(), 200, **kw()) is None
    assert decide_mode(st, D(), 100 + 1800, **kw())[0] == "creative"
    st.pending["immediate"] = True
    assert decide_mode(st, D(), 200, **kw())[0] == "creative"


def test_request_does_not_cut_in_front_of_running_work():
    st = ModeState("creative", 0, pending={"mode": "test", "project": "x", "immediate": False, "priority": 2})
    assert decide_mode(st, D({"creative": [3]}, running=1), 10, **kw()) is None
    assert decide_mode(st, D({"creative": [3]}), 10, **kw())[0] == "test"     # only less urgent work waiting
    assert decide_mode(st, D({"creative": [1]}), 10, **kw()) is None


def test_demand_never_auto_enters_coding_and_waits_for_running_work():
    st = ModeState("creative", 0)
    assert decide_mode(st, D({"coding": [0]}), 10, **kw(default_mode="creative")) is None
    st = ModeState("test", 0)
    assert decide_mode(st, D({"creative": [2]}, running=1), 10, **kw()) is None
    assert decide_mode(st, D({"creative": [2]}), 10, **kw()) == ("creative", "1 waiting job(s) need creative")


def test_idle_return_to_default():
    st = ModeState("creative", 0, last_activity=0)
    assert decide_mode(st, D(), 3599, **kw()) is None
    assert decide_mode(st, D(), 3600, **kw())[0] == "coding"


def test_pin_blocks_demand():
    st = ModeState("creative", 0, pinned_until=500)
    assert decide_mode(st, D({"test": [2]}), 100, **kw()) is None
