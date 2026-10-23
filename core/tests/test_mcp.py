"""The karspark MCP server over real Streamable HTTP (in-process ASGI), with the same fake engines."""
import asyncio
import json

import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from karspark import workers
from karspark.app import Karspark, create_app
from conftest import REPO, Clock
from fakes import FakeComfy, FakeRunner, FakeText, transport

OWNER = "sk-ks-owner-test-key-0000000000"


@pytest.fixture
async def served(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "POLL_S", 0.01)
    monkeypatch.setenv("KARSPARK_API_KEY", OWNER)
    from karspark import config
    cfg = config.load(REPO)
    runner = FakeRunner()
    comfy = FakeComfy(tmp_path, steps=3)
    handlers = {"qwen3.8-flash:vllm-salient": FakeText("flash"), "qwen3.8-27b:vllm": FakeText("planner"),
                "muse-glimmer-30b:vllm-dflash": FakeText("planner"),
                "qwen3-0.6b:vllm": FakeText("tiny"), "comfyui:comfy": comfy}
    k = Karspark(REPO, tmp_path, "x" * 40, runner=runner, http=httpx.AsyncClient(transport=transport(cfg, handlers, runner)),
                 clock=Clock(), background=False, tick_s=0.01, mem_free=lambda: 1000, poll_s=0.01)
    app = create_app(k)
    started, stop = asyncio.Event(), asyncio.Event()

    async def life():  # anyio scopes must be entered and left in the same task
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    t = asyncio.create_task(life())
    await started.wait()
    for _ in range(500):
        if k.sched.state.current == "coding" and not k.sched.switching:
            break
        await asyncio.sleep(0.01)
    yield app, k
    stop.set()
    await t


def client(app, key):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://karspark",
                              headers={"Authorization": f"Bearer {key}"}, timeout=30)


async def test_mcp_needs_a_key(served):
    app, _ = served
    async with client(app, "wrong") as c:
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert r.status_code == 401


async def test_tools_status_text_and_image(served):
    app, k = served
    async with client(app, OWNER) as http, streamable_http_client("http://karspark/mcp", http_client=http) as (rd, wr), \
            ClientSession(rd, wr) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        assert {"generate_text", "generate_image", "split_layers", "upscale_image", "generate_video", "upscale_video",
                "get_job", "wait_job", "pause_job", "resume_job", "cancel_job", "set_priority", "status", "list_jobs",
                "queue", "job_events", "list_models", "usage", "recent_requests", "request_mode", "upload_file"} <= names
        st = await s.call_tool("status", {})
        data = json.loads(st.content[0].text)
        assert data["mode"]["current"] == "coding" and data["you"]["tier"] == "owner"
        t = await s.call_tool("generate_text", {"prompt": "hello"})
        assert json.loads(t.content[0].text)["text"].startswith("flash:")
        t = await s.call_tool("generate_text", {"prompt": "a long story", "as_job": True, "wait_s": 5})
        job = json.loads(t.content[0].text)
        assert job["kind"] == "text" and job["state"] == "succeeded" and job["outputs"][0]["text"].startswith("w0")
        await s.call_tool("request_mode", {"mode": "creative"})
        for _ in range(500):
            if k.sched.state.current == "creative" and not k.sched.switching:
                break
            await asyncio.sleep(0.01)
        img = await s.call_tool("generate_image", {"prompt": "a cat", "wait_s": 10})
        kinds = [c.type for c in img.content]
        assert kinds[0] == "image" and kinds[-1] == "text", img.content
        job = json.loads(img.content[-1].text)
        assert job["state"] == "succeeded" and job["params"]["steps"] if "steps" in job["params"] else True
        a = await s.call_tool("generate_image", {"prompt": "a dog", "idempotency_key": "dog-1", "wait_s": 0})
        b = await s.call_tool("generate_image", {"prompt": "a dog", "idempotency_key": "dog-1", "wait_s": 0})
        assert json.loads(a.content[-1].text)["id"] == json.loads(b.content[-1].text)["id"]
        ev = json.loads((await s.call_tool("job_events", {"job_id": job["id"]})).content[0].text)
        assert [e["state"] for e in ev["events"]][-1] == "succeeded"
        res = await s.read_resource("karspark://status")
        assert json.loads(res.contents[0].text)["mode"]["current"] == "creative"
    await k.log.flush()
    tools = [r["tool"] for r in await k.log.requests(path="/mcp", limit=200)]
    assert "generate_image" in tools and "status" in tools
