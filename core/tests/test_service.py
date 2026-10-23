"""End to end through the HTTP API with fake engines: modes, text proxy, jobs, pause/resume, preemption,
ownership, idempotency."""
import asyncio
import json

import httpx
import pytest

from karspark import workers
from karspark.app import Karspark, create_app
from conftest import REPO, Clock
from fakes import FakeComfy, FakeRunner, FakeText, transport

PEPPER = "x" * 40
OWNER = "sk-ks-owner-test-key-0000000000"


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "POLL_S", 0.01)
    monkeypatch.setenv("KARSPARK_API_KEY", OWNER)
    clock = Clock()
    runner = FakeRunner()
    from karspark import config
    cfg = config.load(REPO)
    flash, planner, tiny = FakeText("flash"), FakeText("planner"), FakeText("tiny")
    comfy = FakeComfy(tmp_path)
    handlers = {"qwen3.8-flash:vllm-salient": flash, "qwen3.8-27b:vllm": planner, "qwen3-0.6b:vllm": tiny,
                "comfyui:comfy": comfy}
    http = httpx.AsyncClient(transport=transport(cfg, handlers, runner))
    k = Karspark(REPO, tmp_path, PEPPER, runner=runner, http=http, clock=clock, adopt=True, background=False,
                 tick_s=0.01, mem_free=lambda: 1000, poll_s=0.01)
    app = create_app(k, with_mcp=False)
    await k.start()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=10)
    e = type("E", (), {})()
    e.k, e.c, e.clock, e.runner, e.comfy, e.flash, e.planner = k, client, clock, runner, comfy, flash, planner
    e.owner = {"Authorization": f"Bearer {OWNER}"}

    async def key(name, tier):
        r = await client.post("/admin/keys", json={"name": name, "tier": tier}, headers=e.owner)
        return {"Authorization": f"Bearer {r.json()['key']}"}

    e.key = key

    async def until(cond, timeout=5):
        for _ in range(int(timeout / 0.01)):
            v = cond()
            if asyncio.iscoroutine(v):
                v = await v
            if v:
                return v
            await asyncio.sleep(0.01)
        raise AssertionError("condition not met")

    e.until = until
    await until(lambda: k.sched.state.current == "coding" and not k.sched.switching)
    yield e
    await client.aclose()
    await k.stop()


async def test_boots_into_coding_and_proxies_text(env):
    r = await env.c.post("/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]},
                         headers=env.owner)
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"].startswith("flash:")
    assert env.flash.requests[-1]["model"] == "qwen3.8-flash" and env.flash.requests[-1]["max_tokens"] == 16384
    r = await env.c.post("/v1/chat/completions", json={"model": "text", "stream": True,
                                                       "messages": [{"role": "user", "content": "hi"}]}, headers=env.owner)
    assert "w4" in r.text and "[DONE]" in r.text
    await env.k.log.flush()
    rows = await env.k.log.requests(path="/v1/chat/completions")
    assert rows[0]["project"] == "karthik-coding" and rows[0]["tokens_out"] == 5 and rows[0]["stream"] == 1
    detail = await env.k.log.request_detail(rows[0]["request_id"])
    assert "w0 w1" in detail["response_body"]


async def test_auth_and_coding_model_reserved(env):
    assert (await env.c.get("/v1/status")).status_code == 401
    std = await env.key("proj-a", "standard")
    r = await env.c.post("/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": []}, headers=std)
    assert r.status_code == 403
    r = await env.c.post("/v1/chat/completions", json={"model": "text", "messages": [{"role": "user", "content": "x"}]}, headers=std)
    assert r.status_code == 200


async def test_media_job_waits_for_coding_hold_then_runs(env):
    std = await env.key("proj-a", "standard")
    # coding activity starts the hold
    await env.c.post("/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": []}, headers=env.owner)
    r = await env.c.post("/v1/jobs", json={"type": "image", "prompt": "a cat"}, headers=std)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["state"] == "queued" and job["model"] == "flux2-dev" and job["priority"] == 2
    await asyncio.sleep(0.1)
    assert env.k.sched.state.current == "coding"
    q = (await env.c.get("/v1/queue", headers=std)).json()["data"]
    assert q[0]["job_id"] == job["id"]
    env.clock.advance(1801)
    done = await env.c.get(f"/v1/jobs/{job['id']}/wait?timeout=5", headers=std)
    assert done.json()["state"] == "succeeded", done.json()
    assert env.k.sched.state.current == "creative"
    assert "qwen3.8-flash:vllm-salient" not in env.runner.up
    out = done.json()["outputs"][0]
    f = await env.c.get(out["url"], headers=std)
    assert f.status_code == 200 and f.content.startswith(b"\x89PNG")
    ev = (await env.c.get(f"/v1/jobs/{job['id']}/events", headers=std)).json()["data"]
    assert [e["state"] for e in ev] == ["queued", "running", "succeeded"]


async def go_creative(env):
    r = await env.c.post("/v1/mode", json={"mode": "creative"}, headers=env.owner)
    assert r.status_code == 202
    await env.until(lambda: env.k.sched.state.current == "creative" and not env.k.sched.switching)


async def test_pause_resume_keeps_progress(env):
    await go_creative(env)
    std = await env.key("proj-a", "standard")
    env.comfy.steps = 400
    job = (await env.c.post("/v1/jobs", json={"type": "image", "prompt": "a cat"}, headers=std)).json()
    await env.until(lambda: env.comfy.jobs.get(job["id"], {}).get("step", 0) >= 3)
    r = await env.c.post(f"/v1/jobs/{job['id']}/pause", headers=std)
    assert r.json()["state"] in ("pausing", "paused")
    await env.until(lambda: env.k.jobs.get(job["id"]), 1)
    j = await env.until(lambda: _state(env, job["id"], "paused"))
    step_at_pause = env.comfy.jobs[job["id"]]["step"]
    other = await env.key("proj-b", "standard")
    assert (await env.c.post(f"/v1/jobs/{job['id']}/resume", headers=other)).status_code == 404
    env.comfy.steps = step_at_pause + 3
    await env.c.post(f"/v1/jobs/{job['id']}/resume", headers=std)
    await env.until(lambda: _state(env, job["id"], "succeeded"))
    assert env.comfy.runs == [(job["id"], 0), (job["id"], step_at_pause)]  # continued, not restarted


async def _state(env, jid, want):
    j = await env.k.jobs.get(jid)
    return j if j["state"] == want else None


async def test_preemption_pauses_batch_for_critical(env):
    await go_creative(env)
    batch, crit = await env.key("bulk", "batch"), await env.key("important", "critical")
    env.comfy.steps = 50
    b = (await env.c.post("/v1/jobs", json={"type": "image", "prompt": "bulk"}, headers=batch)).json()
    await env.until(lambda: env.comfy.jobs.get(b["id"], {}).get("step", 0) >= 2)
    c = (await env.c.post("/v1/jobs", json={"type": "image", "prompt": "now"}, headers=crit)).json()
    await env.until(lambda: _state(env, c["id"], "succeeded"), 10)
    bj = await env.until(lambda: _state(env, b["id"], "succeeded"), 10)
    assert bj["preemptions"] == 1
    order = [r[0] for r in env.comfy.runs]
    assert order == [b["id"], c["id"], b["id"]]
    ev = (await env.c.get(f"/v1/jobs/{b['id']}/events", headers=batch)).json()["data"]
    assert any("preempted by" in (e["reason"] or "") for e in ev)


async def test_coding_request_pauses_media_and_switches(env):
    await go_creative(env)
    std = await env.key("proj-a", "standard")
    env.comfy.steps = 10**6
    job = (await env.c.post("/v1/jobs", json={"type": "image", "prompt": "long"}, headers=std)).json()
    await env.until(lambda: env.comfy.jobs.get(job["id"], {}).get("step", 0) >= 2)
    r = await env.c.post("/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": []}, headers=env.owner)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"].startswith("flash:")
    assert env.k.sched.state.current == "coding"
    j = await env.k.jobs.get(job["id"])
    assert j["state"] == "queued" and j["paused_by"] == "system" and j["preemptions"] == 0
    st = (await env.c.get("/v1/status", headers=std)).json()
    assert st["mode"]["hold"]["kind"] == "coding" and st["running_jobs"] == []


async def test_standard_mode_request_is_deferred_during_hold(env):
    std = await env.key("proj-a", "standard")
    await env.c.post("/v1/chat/completions", json={"model": "qwen3.8-flash", "messages": []}, headers=env.owner)
    r = await env.c.post("/v1/mode", json={"mode": "creative"}, headers=std)
    assert r.status_code == 202 and r.json()["status"] == "pending" and r.json()["reason"] == "coding hold"
    assert int(r.headers["retry-after"]) > 1700
    env.clock.advance(1801)
    await env.until(lambda: env.k.sched.state.current == "creative" and not env.k.sched.switching)
    r = await env.c.post("/v1/mode", json={"mode": "coding"}, headers=std)
    assert r.status_code == 403


async def test_concurrent_ticks_start_one_switch(env):
    # request_mode() ticks inline while the loop ticks too; both once passed the `switching` check and ran two
    # switches in parallel, starting every engine twice (seen on the box at cutover)
    sched = env.k.sched
    n = env.runner.starts.count("qwen3.8-27b:vllm")
    owner = await env.k.keys.verify(OWNER)
    await asyncio.gather(*[sched.tick() for _ in range(3)], sched.request_mode(owner, "creative"),
                         *[sched.tick() for _ in range(3)])
    await env.until(lambda: sched.state.current == "creative" and not sched.switching)
    assert env.runner.starts.count("qwen3.8-27b:vllm") == n + 1
    await env.k.log.flush()
    starts = [e for e in await env.k.log.scheduler_events() if e["kind"] == "mode_switch_start" and e["data"]["dst"] == "creative"]
    assert len(starts) == 1


async def test_validation_idempotency_and_cancel(env):
    std = await env.key("proj-a", "standard")
    r = await env.c.post("/v1/jobs", json={"type": "video", "prompt": "x", "audio": "music"}, headers=std)
    assert r.status_code == 400 and "audio" in r.json()["error"]["message"]
    r = await env.c.post("/v1/jobs", json={"type": "image", "prompt": "x", "model": "ltx-2.5"}, headers=std)
    assert r.status_code == 400
    r = await env.c.post("/v1/jobs", json={"type": "upscale_image", "image": "https://evil/x.png"}, headers=std)
    assert r.status_code == 400 and "remote URLs" in r.json()["error"]["message"]
    h = {**std, "Idempotency-Key": "abc"}
    a = await env.c.post("/v1/jobs", json={"type": "image", "prompt": "x"}, headers=h)
    b = await env.c.post("/v1/jobs", json={"type": "image", "prompt": "x"}, headers=h)
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    r = await env.c.post(f"/v1/jobs/{a.json()['id']}/cancel", headers=std)
    assert r.json()["state"] == "cancelled"
    r = await env.c.patch(f"/v1/jobs/{a.json()['id']}", json={"priority": 0}, headers=std)
    assert r.status_code == 409


async def test_upload_and_reference_images(env):
    std = await env.key("proj-a", "standard")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = await env.c.post("/v1/files", files={"file": ("char.png", png, "image/png")}, headers=std)
    assert r.status_code == 201, r.text
    fid = r.json()["file_id"]
    r = await env.c.post("/v1/jobs", json={"type": "image", "prompt": "her", "reference_images": [fid, r.json()["url"]],
                                           "priority": 0}, headers=std)
    assert r.status_code == 201, r.text
    j = await env.k.jobs.get(r.json()["id"])
    assert j["inputs"] == {"reference_images": ["reference_images_0.png", "reference_images_1.png"]}
    assert j["priority"] == 2 and j["priority_req"] == 0           # capped at the tier ceiling
    other = await env.key("proj-b", "standard")
    assert (await env.c.get(f"/v1/files/{fid}", headers=other)).status_code == 404


async def test_text_job_pause_continues_text(env):
    await go_creative(env)
    env.planner.delay = 0.05
    env.planner.words = 40
    std = await env.key("proj-a", "standard")
    job = (await env.c.post("/v1/jobs", json={"type": "text", "prompt": "write a story"}, headers=std)).json()
    await env.until(lambda: _state(env, job["id"], "running"))
    await asyncio.sleep(0.3)
    await env.c.post(f"/v1/jobs/{job['id']}/pause", headers=std)
    j = await env.until(lambda: _state(env, job["id"], "paused"))
    partial = j["progress"]["partial"]
    assert partial.startswith("w0") and "w39" not in partial
    env.planner.delay = 0
    await env.c.post(f"/v1/jobs/{job['id']}/resume", headers=std)
    j = await env.until(lambda: _state(env, job["id"], "succeeded"))
    assert j["outputs"][0]["text"] == partial + "more "
    last = env.planner.requests[-1]
    assert last["continue_final_message"] and last["messages"][-1] == {"role": "assistant", "content": partial}


async def test_rate_limit_429(env):
    b = await env.key("bulk", "batch")   # 60 rpm
    codes = [(await env.c.get("/v1/mode", headers=b)).status_code for _ in range(62)]
    assert codes.count(429) >= 1 and codes[0] == 200


async def test_core_restart_adopts_engines_and_resumes_job(env, tmp_path):
    await go_creative(env)
    std = await env.key("proj-a", "standard")
    env.comfy.steps = 10**6
    job = (await env.c.post("/v1/jobs", json={"type": "image", "prompt": "long"}, headers=std)).json()
    await env.until(lambda: env.comfy.jobs.get(job["id"], {}).get("step", 0) >= 2)
    starts = list(env.runner.starts)
    await env.k.stop()  # core dies; engines (detached) keep running, ComfyUI keeps sampling
    k2 = Karspark(REPO, tmp_path, PEPPER, runner=env.runner, http=httpx.AsyncClient(transport=env.k.http._transport),
                  clock=env.clock, background=False, tick_s=0.01, mem_free=lambda: 1000, poll_s=0.01)
    await k2.start()
    try:
        assert k2.sched.state.current == "creative" and env.runner.starts == starts  # adopted, nothing restarted
        env.comfy.steps = env.comfy.jobs[job["id"]]["step"] + 3
        for _ in range(500):
            j = await k2.jobs.get(job["id"])
            if j["state"] == "succeeded":
                break
            await asyncio.sleep(0.01)
        assert j["state"] == "succeeded"
        assert [r for r in env.comfy.runs if r[0] == job["id"]] == [(job["id"], 0)]  # followed, not re-run
    finally:
        await k2.stop()
        env.k = k2


async def test_core_restart_reloads_only_engines_whose_recipe_changed(env, tmp_path):
    await go_creative(env)
    env.runner.hashes["qwen3.8-27b:vllm"] = "older-recipe"   # as if the planner's recipe was edited and deployed
    starts = list(env.runner.starts)
    await env.k.stop()
    k2 = Karspark(REPO, tmp_path, PEPPER, runner=env.runner, http=httpx.AsyncClient(transport=env.k.http._transport),
                  clock=env.clock, background=False, tick_s=0.01, mem_free=lambda: 1000, poll_s=0.01)
    await k2.start()
    try:
        for _ in range(500):
            if k2.engines.ready("qwen3.8-27b:vllm") and not k2.sched.switching:
                break
            await asyncio.sleep(0.01)
        assert env.runner.starts[len(starts):] == ["qwen3.8-27b:vllm"]   # ComfyUI adopted, the planner reloaded
        assert k2.engines.ready("comfyui:comfy") and k2.sched.state.current == "creative"
    finally:
        await k2.stop()
        env.k = k2


async def test_cancel_the_engine_never_confirms_is_forced(env):
    await go_creative(env)
    std = await env.key("proj-a", "standard")
    env.comfy.steps = 10**6
    job = (await env.c.post("/v1/jobs", json={"type": "image", "prompt": "stuck"}, headers=std)).json()
    await env.until(lambda: env.comfy.jobs.get(job["id"], {}).get("step", 0) >= 1)
    env.comfy.hold = True                                   # the engine stops answering the cancel
    await env.c.post(f"/v1/jobs/{job['id']}/cancel", headers=std)
    await asyncio.sleep(0.1)
    assert (await env.k.jobs.get(job["id"]))["state"] == "cancelling"
    env.clock.advance(121)
    await env.until(lambda: _is_state(env, job["id"], "cancelled"))
    nxt = (await env.c.post("/v1/jobs", json={"type": "image", "prompt": "next"}, headers=std)).json()
    env.comfy.hold, env.comfy.steps = False, 3
    await env.until(lambda: _is_state(env, nxt["id"], "succeeded"))   # the slot was freed


async def _is_state(env, job_id, state):
    return (await env.k.jobs.get(job_id))["state"] == state


async def test_cold_boot_starts_in_the_default_mode(env, tmp_path):
    await go_creative(env)
    await env.k.stop()
    env.runner.up.clear()                                   # the box rebooted: no engine survived
    k2 = Karspark(REPO, tmp_path, PEPPER, runner=env.runner, http=httpx.AsyncClient(transport=env.k.http._transport),
                  clock=env.clock, background=False, tick_s=0.01, mem_free=lambda: 1000, poll_s=0.01)
    await k2.start()
    try:
        for _ in range(500):
            if not k2.sched.switching:
                break
            await asyncio.sleep(0.01)
        assert k2.sched.state.current == "coding" and k2.engines.ready("qwen3.8-flash:vllm-salient")
    finally:
        await k2.stop()
        env.k = k2
