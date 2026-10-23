"""The Python SDK (clients/python) against the real app over HTTP, with fake engines."""
import asyncio
import socket
import sys

import pytest
import uvicorn

from conftest import REPO
from test_service import OWNER, env, go_creative  # noqa: F401  (fixture)

sys.path.insert(0, str(REPO / "clients" / "python"))
from karspark_client import JobFailed, Karspark, KarsparkError  # noqa: E402


@pytest.fixture
async def served(env):
    from karspark.app import create_app
    app = create_app(env.k, with_mcp=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    await env.until(lambda: server.started)
    env.url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    yield env
    server.should_exit = True
    await task


async def test_sdk_image_layers_text_and_control(served, tmp_path):
    env = served
    await go_creative(env)
    std = (await env.key("sdk-proj", "standard"))["Authorization"].split()[1]

    def flow():
        ks = Karspark(std, env.url, retries=0)
        job = ks.image("a cat", size="1024x1024")
        assert job.kind == "image" and job.model == "flux2-dev" and job.priority == 2
        saved = job.wait(timeout=10).save(tmp_path / "cat.png")
        assert saved[0].read_bytes().startswith(b"\x89PNG")
        lay = ks.layers(saved[0]).wait(timeout=10)                       # local path -> upload -> file_id
        assert lay.state == "succeeded"
        assert ks.text("hi").startswith("planner:")
        ev = [e["state"] for e in job.events()]
        assert ev[0] == "queued" and ev[-1] == "succeeded"
        assert any(m["id"] == "flux2-dev" for m in ks.models())
        with pytest.raises(KarsparkError) as e:
            ks.video("x", duration=99)
        assert e.value.status == 400
        return ks

    ks = await asyncio.to_thread(flow)

    env.comfy.steps = 400
    job = await asyncio.to_thread(ks.image, "slow")
    await env.until(lambda: env.comfy.jobs.get(job.id, {}).get("step", 0) >= 2)
    await asyncio.to_thread(job.pause)
    j = await asyncio.to_thread(job.wait, 5)                               # returns on a user pause
    assert j.state == "paused" and j.paused_by == "user"
    await asyncio.to_thread(job.cancel)
    with pytest.raises(JobFailed):
        await asyncio.to_thread(job.wait, 5)
    with pytest.raises(KarsparkError) as e:
        await asyncio.to_thread(Karspark("sk-wrong", env.url, retries=0).status)
    assert e.value.status == 401
