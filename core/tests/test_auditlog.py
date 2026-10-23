import asyncio

from karspark.auditlog import AuditLog
from karspark.events import Bus


async def test_writes_and_queries(tmp_path, clock):
    bus = Bus()
    log = await AuditLog(tmp_path / "logs.db", bus, clock).open()
    with bus.subscribe() as q:
        log.job_event("job_1", "queued", actor="proj", reason="created")
        topic, ev = await asyncio.wait_for(q.get(), 1)
        assert topic == "job" and ev["state"] == "queued"
    for n in range(2000):  # under load: batched, nothing lost
        log.request({"request_id": f"r{n}", "key_id": "k", "project": "p", "status": 200, "tokens_in": 1},
                    {"q": n}, {"a": n})
    await log.flush()
    assert len(await log.requests(limit=5000)) == 1000  # capped page size
    d = await log.request_detail("r7")
    assert '"q": 7' in d["request_body"]
    u = await log.usage(0)
    assert u[0]["requests"] == 2000 and u[0]["tokens_in"] == 2000
    ev = await log.job_events("job_1")
    assert ev[0]["reason"] == "created"
    clock.advance(31 * 86400)
    await log.cleanup(30, 365)
    assert (await log.request_detail("r7"))["request_body"] is None
    await log.close()
